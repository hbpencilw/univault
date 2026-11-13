"""moodle_http — authenticated HTTP transport for the UniVault Moodle connector.

Standard library only (urllib, http.cookiejar semantics kept simple: we carry
the raw Cookie header the user exported from Edge).

Security invariants (docs/PLAN_MOODLE.md sections 11 and 22):
- authenticated requests only to https://<allowlisted Moodle host> whose DNS
  answers are all public addresses;
- every redirect hop is re-validated; a hop to an SSO/IdP host is reported as
  AUTH_EXPIRED, anything else non-allowlisted is BLOCKED_URL;
- the Cookie header is only attached to allowlisted hosts and is never
  printed — error messages and repr output are redacted;
- downloads stream in 64 KiB chunks to a temporary file, hash with SHA-256,
  fsync, then atomically rename; partial files never masquerade as successes.

Tests inject a fake transport/stream opener; nothing here is global state.
"""
import hashlib
import ipaddress
import os
import random
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass

from .moodle_errors import (
    AuthExpired,
    BlockedUrl,
    ContentMismatch,
    DiskFull,
    FileWriteFailed,
    NetworkFailed,
    PermissionDenied,
    RateLimited,
    ResourceUnavailable,
    ServerError,
)

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0"
)

SSO_HOST_MARKERS = ("okta", "microsoftonline", "saml", "shibboleth", "idp.", "sso.")


# ------------------------------------------------------------------ URL safety
_DNS_CACHE: dict[str, bool] = {}


def resolve_public(host: str) -> bool | None:
    """True=public, False=non-public, None=resolution failure (never cached)."""
    host = (host or "").lower()
    if host in _DNS_CACHE:
        return _DNS_CACHE[host]
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        return None  # transient; callers decide (NetworkFailed, not BlockedUrl)
    ok = True
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0].split("%")[0])
        except ValueError:
            ok = False
            break
        if not ip.is_global:  # rejects private/loopback/reserved/link-local/multicast/unspecified
            ok = False
            break
    if ok and infos:
        _DNS_CACHE[host] = True
    return ok and bool(infos)


def host_resolves_public(host: str) -> bool:
    return resolve_public(host) is True


def _looks_like_sso(host: str, path: str = "") -> bool:
    host = (host or "").lower()
    path = (path or "").lower()
    return any(m in host for m in SSO_HOST_MARKERS) or "login" in path or "/saml" in path


def validate_url(url: str, allowed_hosts: set[str], *, dns_check: bool = True) -> urllib.parse.SplitResult:
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https":
        raise BlockedUrl(f"unsupported scheme: {p.scheme or '(none)'}")
    host = (p.hostname or "").lower()
    if not host:
        raise BlockedUrl("missing host")
    if host not in allowed_hosts:
        raise BlockedUrl(f"host not allowed: {host}")
    if host == "localhost" or host.endswith(".localhost"):
        raise BlockedUrl(f"loopback hostname not allowed: {host}")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        # Literal addresses are checked even when DNS resolution is skipped.
        if not literal.is_global:
            raise BlockedUrl(f"host is not a public address: {host}")
    elif dns_check:
        resolved = resolve_public(host)
        if resolved is False:
            raise BlockedUrl(f"host does not resolve to a public address: {host}")
        if resolved is None:
            raise NetworkFailed(f"DNS resolution failed for {host}")
    return p


def validate_external_url(url: str, *, dns_check: bool = True) -> urllib.parse.SplitResult:
    """Allow a public https host for credential-free redirects (CDN downloads)."""
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https":
        raise BlockedUrl(f"unsupported scheme: {p.scheme or '(none)'}")
    host = (p.hostname or "").lower()
    if not host:
        raise BlockedUrl("missing host")
    if host == "localhost" or host.endswith(".localhost"):
        raise BlockedUrl(f"loopback hostname not allowed: {host}")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not literal.is_global:
            raise BlockedUrl(f"host is not a public address: {host}")
    elif dns_check:
        resolved = resolve_public(host)
        if resolved is False:
            raise BlockedUrl(f"host does not resolve to a public address: {host}")
        if resolved is None:
            raise NetworkFailed(f"DNS resolution failed for {host}")
    return p


@dataclass
class RawResponse:
    status: int
    headers: dict
    body: bytes
    url: str


@dataclass
class StreamResponse:
    status: int
    headers: dict
    chunks: object  # iterator of bytes
    url: str


@dataclass
class DownloadResult:
    path: str
    sha256: str
    bytes_written: int
    content_type: str
    content_disposition: str | None
    final_url: str


def _lower_headers(headers) -> dict:
    out = {}
    for k, v in headers.items():
        out[k.lower()] = v
    return out


def _iter_chunks(resp, size=65536):
    while True:
        chunk = resp.read(size)
        if not chunk:
            return
        yield chunk


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # make urllib raise HTTPError so we can validate the hop


def _default_transport(method, url, headers, data, timeout):
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return RawResponse(resp.status, _lower_headers(resp.headers), resp.read(), resp.geturl())
    except urllib.error.HTTPError as e:
        return RawResponse(e.code, _lower_headers(e.headers or {}), e.read() or b"", e.geturl() or url)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise NetworkFailed(f"network error: {e.__class__.__name__}") from e


def _default_stream_open(url, headers, timeout):
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        resp = opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        return StreamResponse(e.code, _lower_headers(e.headers or {}), iter(()), e.geturl() or url)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise NetworkFailed(f"network error: {e.__class__.__name__}") from e
    return StreamResponse(resp.status, _lower_headers(resp.headers), _iter_chunks(resp), resp.geturl())


def looks_like_login_page(html: str) -> bool:
    if not html:
        return False
    return ('name="logintoken"' in html) or ('id="login"' in html and "login/index.php" in html)


def parse_content_disposition(value: str | None) -> str | None:
    if not value:
        return None
    m = re.search(r"filename\*\s*=\s*(?:UTF-8'')?([^;]+)", value, re.I)
    if m:
        return urllib.parse.unquote(m.group(1).strip().strip('"'))
    m = re.search(r'filename\s*=\s*"([^"]+)"', value, re.I) or re.search(r"filename\s*=\s*([^;]+)", value, re.I)
    return m.group(1).strip().strip('"') if m else None


class HttpClient:
    def __init__(
        self,
        base_url: str,
        cookie_header: str = "",
        user_agent: str = DEFAULT_UA,
        *,
        timeout_connect: float = 10.0,
        timeout_read: float = 30.0,
        min_interval: float = 1.0,
        max_retries: int = 3,
        allowed_hosts: set[str] | None = None,
        transport=None,
        stream_open=None,
        dns_check: bool = True,
        min_free_bytes: int = 256 * 1024 * 1024,
    ):
        p = urllib.parse.urlsplit(base_url)
        if p.scheme != "https" or not p.hostname:
            raise BlockedUrl(f"invalid base url: {base_url}")
        self.base_url = base_url.rstrip("/")
        self.host = p.hostname.lower()
        self.cookie_header = cookie_header or ""
        self.user_agent = user_agent or DEFAULT_UA
        self.timeout_connect = timeout_connect
        self.timeout_read = timeout_read
        self.min_interval = max(0.0, min_interval)
        self.max_retries = max(0, max_retries)
        self.allowed_hosts = {h.lower() for h in (allowed_hosts or {self.host})} | {self.host}
        self.dns_check = dns_check
        self.min_free_bytes = min_free_bytes
        self._transport = transport or _default_transport
        self._stream_open = stream_open or _default_stream_open
        self._last_request = 0.0
        self.request_count = 0
        self.secret_values: list[str] = []

    # ------------------------------------------------------------- helpers
    def _remember_secret(self, value: str):
        if value:
            self.secret_values.append(value)

    def redact(self, text: str) -> str:
        if not text:
            return text
        out = str(text)
        for v in self.secret_values:
            if v:
                out = out.replace(v, "<redacted>")
        out = re.sub(r"(sesskey=)[^&\"'\s]+", r"\1<redacted>", out)
        out = re.sub(r"(?i)(cookie:\s*).*", r"\1<redacted>", out)
        return out

    def _headers(self, extra: dict | None = None, include_cookie: bool = True) -> dict:
        h = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-AU,en;q=0.9",
        }
        if include_cookie and self.cookie_header:
            h["Cookie"] = self.cookie_header
            if self.cookie_header not in self.secret_values:
                self._remember_secret(self.cookie_header)
        if extra:
            h.update(extra)
        return h

    def _pace(self):
        if self.min_interval <= 0:
            return
        wait = self.min_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    # ------------------------------------------------------------- requests
    def request(self, method: str, url: str, *, data: bytes | None = None,
                extra_headers: dict | None = None, max_redirects: int = 5,
                external: bool = False) -> RawResponse:
        current = url
        allowed = True
        for _ in range(max_redirects + 1):
            if allowed:
                validate_url(current, self.allowed_hosts, dns_check=self.dns_check)
            else:
                validate_external_url(current, dns_check=self.dns_check)
            resp = self._attempt(method, current, data, extra_headers, include_cookie=allowed)
            if resp.status in (301, 302, 303, 307, 308):
                loc = resp.headers.get("location")
                if not loc:
                    raise ResourceUnavailable(f"redirect without Location from {urllib.parse.urlsplit(current).path}")
                target = urllib.parse.urljoin(current, loc)
                tp = urllib.parse.urlsplit(target)
                host = (tp.hostname or "").lower()
                if host in self.allowed_hosts:
                    allowed = True
                elif external:
                    validate_external_url(target, dns_check=self.dns_check)
                    allowed = False
                else:
                    if _looks_like_sso(host, tp.path):
                        raise AuthExpired("redirected to single sign-on; session likely expired")
                    raise BlockedUrl(f"redirect leaves Moodle host: {host}")
                current = target
                if resp.status == 303:
                    method, data = "GET", None
                continue
            resp.url = current  # manual redirect loop: the requested URL is authoritative
            return resp
        raise ServerError("too many redirects")

    def head(self, url: str, *, extra_headers: dict | None = None, external: bool = False) -> RawResponse:
        return self.request("HEAD", url, extra_headers=extra_headers, external=external)

    def _attempt(self, method: str, url: str, data: bytes | None,
                 extra_headers: dict | None, include_cookie: bool = True) -> RawResponse:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._pace()
            self.request_count += 1
            try:
                resp = self._transport(method, url, self._headers(extra_headers, include_cookie=include_cookie),
                                       data, self.timeout_read)
            except NetworkFailed as e:
                last_error = e
                if attempt < self.max_retries:
                    time.sleep(min(30.0, 2.0 ** attempt * (1 + random.random() * 0.2)))
                    continue
                raise
            if resp.status in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                retry_after = resp.headers.get("retry-after")
                delay = min(30.0, float(retry_after)) if (retry_after or "").isdigit() else min(30.0, 2.0 ** attempt)
                time.sleep(delay + random.random())
                last_error = RateLimited(f"HTTP {resp.status}") if resp.status == 429 else ServerError(f"HTTP {resp.status}")
                continue
            return resp
        raise last_error or ServerError("request failed")

    def get(self, url: str, *, extra_headers: dict | None = None) -> RawResponse:
        return self.request("GET", url, extra_headers=extra_headers)

    def post_form(self, url: str, form: dict, *, extra_headers: dict | None = None) -> RawResponse:
        body = urllib.parse.urlencode(form).encode()
        h = {"Content-Type": "application/x-www-form-urlencoded"}
        if extra_headers:
            h.update(extra_headers)
        return self.request("POST", url, data=body, extra_headers=h)

    def get_text(self, url: str) -> tuple[str, str]:
        resp = self.get(url)
        self._raise_for_status(resp, url)
        return decode_body(resp.body, resp.headers.get("content-type", "")), resp.url

    def _raise_for_status(self, resp: RawResponse, url: str):
        if resp.status == 200:
            return
        path = urllib.parse.urlsplit(url).path
        if resp.status in (401, 403):
            raise PermissionDenied(f"HTTP {resp.status} for {path}")
        if resp.status == 404:
            raise ResourceUnavailable(f"HTTP 404 for {path}")
        if resp.status == 429:
            raise RateLimited(f"HTTP 429 for {path}")
        if resp.status >= 500:
            raise ServerError(f"HTTP {resp.status} for {path}")
        raise ResourceUnavailable(f"HTTP {resp.status} for {path}")

    # ------------------------------------------------------------- download
    def download(self, url: str, dest_path: str, *, expected_size: int = 0,
                 max_bytes: int | None = None, allow_html: bool = False) -> DownloadResult:
        dest = os.path.abspath(dest_path)
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        self._check_disk_space(os.path.dirname(dest) or ".", expected_size, max_bytes)

        current, allowed, stream = url, True, None
        for _ in range(6):
            if allowed:
                validate_url(current, self.allowed_hosts, dns_check=self.dns_check)
            else:
                validate_external_url(current, dns_check=self.dns_check)
            self._pace()
            self.request_count += 1
            stream = self._stream_open(current, self._headers(include_cookie=allowed), self.timeout_read)
            if stream.status in (301, 302, 303, 307, 308):
                loc = stream.headers.get("location")
                if not loc:
                    raise ResourceUnavailable("download redirect without Location")
                target = urllib.parse.urljoin(current, loc)
                host = (urllib.parse.urlsplit(target).hostname or "").lower()
                if host in self.allowed_hosts:
                    allowed = True
                else:
                    validate_external_url(target, dns_check=self.dns_check)
                    allowed = False
                current = target
                continue
            break
        else:
            raise ServerError("too many redirects")
        if stream.status != 200:
            raise ResourceUnavailable(f"HTTP {stream.status} for download")

        ctype = (stream.headers.get("content-type") or "").split(";")[0].strip().lower()
        length = stream.headers.get("content-length")
        if not allow_html and ctype == "text/html":
            raise ContentMismatch("server returned HTML where a file was expected (login page?)")

        tmp = os.path.join(os.path.dirname(dest) or ".", f".{os.path.basename(dest)}.{uuid.uuid4().hex}.part")
        hasher = hashlib.sha256()
        written = 0
        try:
            with open(tmp, "wb") as fh:
                for chunk in stream.chunks:
                    if not chunk:
                        continue
                    written += len(chunk)
                    if max_bytes is not None and written > max_bytes:
                        raise ContentMismatch(f"download exceeded max size ({max_bytes} bytes)")
                    hasher.update(chunk)
                    fh.write(chunk)
                fh.flush()
                os.fsync(fh.fileno())
            if length and length.isdigit() and int(length) != written:
                raise ContentMismatch(f"content-length mismatch: expected {length}, got {written}")
            if written == 0:
                raise ContentMismatch("empty response body")
            os.replace(tmp, dest)
        except (ContentMismatch, DiskFull):
            _safe_unlink(tmp)
            raise
        except OSError as e:
            _safe_unlink(tmp)
            raise FileWriteFailed(f"write failed: {e.__class__.__name__}") from e
        return DownloadResult(
            path=dest,
            sha256=hasher.hexdigest(),
            bytes_written=written,
            content_type=ctype,
            content_disposition=parse_content_disposition(stream.headers.get("content-disposition")),
            final_url=stream.url,
        )

    def _check_disk_space(self, directory: str, expected_size: int, max_bytes: int | None):
        try:
            free = shutil_free(directory)
        except OSError:
            return
        needed = self.min_free_bytes
        if expected_size > 0:
            needed = max(needed, int(expected_size * 1.1))
        if free < needed:
            raise DiskFull(f"insufficient disk space in {directory}: need {needed} bytes, free {free}")


def shutil_free(path: str) -> int:
    import shutil
    return shutil.disk_usage(path).free


def _safe_unlink(path: str):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def decode_body(body: bytes, content_type: str) -> str:
    m = re.search(r"charset=([^\s;]+)", content_type or "", re.I)
    enc = m.group(1).strip("\"'") if m else "utf-8"
    try:
        return body.decode(enc, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")
