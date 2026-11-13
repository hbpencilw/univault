"""moodle_auth — Edge session cookie import, storage and validation.

Credential rules (docs/PLAN_MOODLE.md sections 7 and 22):
- the only accepted credential is the Cookie header the user copied from a
  real Edge session (whole header, or a single MoodleSession value);
- files live in ~/.config/univault (0700) with mode 0600, written atomically;
- values are never echoed, logged, or written into archives; error messages
  only describe shape ("2 cookies, 38 + 41 chars") never content;
- session validity is judged from the final host, the page body and actual
  access to at least one course — never from HTTP 200 alone.
"""
import json
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .moodle_errors import AuthExpired, BlockedUrl
from .moodle_http import HttpClient, looks_like_login_page

CONFIG_DIR = Path(os.environ.get("UNIVAULT_CONFIG_DIR", "~/.config/univault")).expanduser()
COOKIE_FILE = CONFIG_DIR / "moodle-cookie"
CAPABILITIES_FILE = CONFIG_DIR / "capabilities.json"
DEFAULT_BASE = os.environ.get("MOODLE_BASE", "https://learning.monash.edu").rstrip("/")

_COOKIE_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")

_RE_SESSKEY = [
    re.compile(r'"sesskey"\s*:\s*"([A-Za-z0-9]+)"'),
    re.compile(r'name="sesskey"\s+value="([A-Za-z0-9]+)"'),
]
_RE_USERID = [
    re.compile(r'"userid"\s*:\s*"?(\d+)"?'),
    re.compile(r'"userId"\s*:\s*"?(\d+)"?'),
]
_RE_USERNAME = [re.compile(r'"username"\s*:\s*"([^"]{1,64})"')]
_RE_RELEASE = [re.compile(r'"release"\s*:\s*"([^"]{1,64})"')]
_RE_SITENAME = [re.compile(r'"sitename"\s*:\s*"([^"]{1,120})"')]


# ------------------------------------------------------------------ cookie IO
def parse_cookie_header(raw: str) -> dict:
    pairs: dict[str, str] = {}
    for part in (raw or "").split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError("malformed cookie pair (expected name=value)")
        name, value = part.split("=", 1)
        name, value = name.strip(), value.strip()
        if not name or not _COOKIE_NAME_RE.match(name):
            raise ValueError("invalid cookie name")
        if any(ch in value for ch in "\r\n\x00"):
            raise ValueError("control characters in cookie value")
        if len(name) > 128 or len(value) > 8192:
            raise ValueError("cookie pair too long")
        pairs[name] = value
    if not pairs:
        raise ValueError("no cookies found")
    return pairs


def canonical_cookie_header(pairs: dict) -> str:
    return "; ".join(f"{k}={v}" for k, v in pairs.items())


def cookie_shape(pairs: dict) -> str:
    """Human-readable, value-free description used in reports."""
    return ", ".join(f"{k}({len(v)} chars)" for k, v in pairs.items())


def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass


def save_cookie(raw_header: str, base_url: str = DEFAULT_BASE) -> dict:
    pairs = parse_cookie_header(raw_header)
    ensure_config_dir()
    payload = {
        "schema": 1,
        "host": (urlsplit(base_url).hostname or "").lower(),
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "cookie": canonical_cookie_header(pairs),
    }
    tmp = CONFIG_DIR / f".moodle-cookie.{uuid.uuid4().hex}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, COOKIE_FILE)
        os.chmod(COOKIE_FILE, 0o600)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    return payload


def load_cookie() -> dict | None:
    if not COOKIE_FILE.exists():
        return None
    text = COOKIE_FILE.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        if isinstance(data, dict) and isinstance(data.get("cookie"), str):
            return data
    except json.JSONDecodeError:
        pass
    # legacy: a plain Cookie header file
    try:
        pairs = parse_cookie_header(text)
    except ValueError:
        return None
    return {"schema": 0, "host": (urlsplit(DEFAULT_BASE).hostname or "").lower(),
            "saved_at": None, "cookie": canonical_cookie_header(pairs)}


def clear_cookie() -> bool:
    removed = False
    for p in (COOKIE_FILE, CAPABILITIES_FILE):
        if p.exists():
            p.unlink()
            removed = True
    return removed


def permissions_report() -> list[str]:
    warnings = []
    if not CONFIG_DIR.exists():
        return ["config directory does not exist (no credentials yet)"]
    mode = CONFIG_DIR.stat().st_mode & 0o777
    if mode != 0o700:
        warnings.append(f"config directory mode is {oct(mode)}, expected 0o700")
    if COOKIE_FILE.exists():
        mode = COOKIE_FILE.stat().st_mode & 0o777
        if mode != 0o600:
            warnings.append(f"cookie file mode is {oct(mode)}, expected 0o600")
    return warnings


# ------------------------------------------------------------------ session
@dataclass
class SessionInfo:
    host: str
    final_url: str
    sesskey: str | None = None
    userid: str | None = None
    username: str | None = None
    sitename: str | None = None
    release: str | None = None
    course_ids: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_courses(self) -> bool:
        return bool(self.course_ids)


def _first_match(patterns, text):
    for pat in patterns:
        m = pat.search(text or "")
        if m:
            return m.group(1)
    return None


def extract_session(html: str) -> dict:
    html = html or ""
    return {
        "sesskey": _first_match(_RE_SESSKEY, html),
        "userid": _first_match(_RE_USERID, html),
        "username": _first_match(_RE_USERNAME, html),
        "release": _first_match(_RE_RELEASE, html),
        "sitename": _first_match(_RE_SITENAME, html),
        "course_ids": sorted({int(x) for x in re.findall(r"course/view\.php\?id=(\d+)", html)}),
    }


def validate_session(client: HttpClient, base_url: str = DEFAULT_BASE) -> SessionInfo:
    url = base_url.rstrip("/") + "/my/"
    html, final = client.get_text(url)
    if looks_like_login_page(html):
        raise AuthExpired("received the Moodle login page; the exported session is no longer valid")
    info = extract_session(html)
    session = SessionInfo(
        host=urlsplit(base_url).hostname or "",
        final_url=final,
        sesskey=info["sesskey"],
        userid=info["userid"],
        username=info["username"],
        sitename=info["sitename"],
        release=info["release"],
        course_ids=info["course_ids"],
    )
    if not session.sesskey:
        session.warnings.append("no sesskey found on /my/ (session may be stale, or the theme omits M.cfg)")
    if not session.course_ids:
        session.warnings.append("no course links found on /my/ (account may have no current enrolments)")
    return session


def make_client(base_url: str | None = None, *, dns_check: bool = True, **kwargs) -> tuple[HttpClient, dict]:
    payload = load_cookie()
    if payload is None:
        raise FileNotFoundError("no stored Moodle cookie; run: moodle-setup import-cookie")
    host = (payload.get("host") or "").strip()
    base = base_url or (f"https://{host}" if host else DEFAULT_BASE)
    client = HttpClient(base, payload["cookie"], dns_check=dns_check, **kwargs)
    return client, payload


def import_from_stdin() -> str:
    import getpass

    raw = getpass.getpass("Paste the Cookie request header from Edge (input hidden): ")
    return raw.strip()


def import_from_file(path: str) -> str:
    return Path(path).expanduser().read_text(encoding="utf-8", errors="replace").strip()


def import_from_clipboard() -> str:
    if sys.platform != "darwin":
        raise RuntimeError("clipboard import is implemented for macOS (pbpaste) only")
    result = subprocess.run(["pbpaste"], capture_output=True, text=True, check=False)
    return (result.stdout or "").strip()
