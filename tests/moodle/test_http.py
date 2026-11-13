import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from lib.moodle_errors import (  # noqa: E402
    AuthExpired,
    BlockedUrl,
    ContentMismatch,
    NetworkFailed,
)
from lib.moodle_http import (  # noqa: E402
    HttpClient,
    RawResponse,
    decode_body,
    looks_like_login_page,
    validate_url,
)
from fakes import FakeStream, FakeStreamQueue, FakeTransport  # noqa: E402

HOST = "learning.monash.edu"
BASE = f"https://{HOST}"


def client(transport, **kw):
    kw.setdefault("min_interval", 0.0)
    kw.setdefault("dns_check", False)
    return HttpClient(BASE, "MoodleSession=abc; MOODLEID1_=def", transport=transport, **kw)


class UrlSafetyTests(unittest.TestCase):
    def test_rejects_plain_http(self):
        with self.assertRaises(BlockedUrl):
            validate_url("http://learning.monash.edu/", {HOST}, dns_check=False)

    def test_rejects_localhost(self):
        with self.assertRaises(BlockedUrl):
            validate_url("https://localhost/", {"localhost"}, dns_check=False)

    def test_rejects_private_ip(self):
        for host in ("127.0.0.1", "10.0.0.5", "192.168.1.10", "169.254.169.254", "::1"):
            with self.subTest(host=host), self.assertRaises(BlockedUrl):
                validate_url(f"https://{host}/", {host}, dns_check=False)

    def test_rejects_non_allowlisted_host(self):
        with self.assertRaises(BlockedUrl):
            validate_url("https://evil.example/", {HOST}, dns_check=False)

    def test_allows_https_moodle_host(self):
        p = validate_url(f"{BASE}/my/", {HOST}, dns_check=False)
        self.assertEqual(p.hostname, HOST)


class RedirectTests(unittest.TestCase):
    def test_redirect_to_sso_is_auth_expired(self):
        t = FakeTransport([RawResponse(303, {"location": "https://monashuni.okta.com/app/sso/saml?x=1"}, b"", f"{BASE}/my/")])
        with self.assertRaises(AuthExpired):
            client(t).get(f"{BASE}/my/")

    def test_redirect_offsite_non_sso_is_blocked(self):
        t = FakeTransport([RawResponse(302, {"location": "https://cdn.example.net/file"}, b"", f"{BASE}/my/")])
        with self.assertRaises(BlockedUrl):
            client(t).get(f"{BASE}/my/")

    def test_follows_same_host_redirect(self):
        t = FakeTransport([
            RawResponse(302, {"location": "/login/index.php?wantsurl=x"}, b"", f"{BASE}/my/"),
            RawResponse(200, {"content-type": "text/html"}, b"<html>login</html>", f"{BASE}/login/index.php"),
        ])
        resp = client(t).get(f"{BASE}/my/")
        self.assertEqual(resp.status, 200)
        self.assertTrue(resp.url.endswith("/login/index.php?wantsurl=x"))


class RetryTests(unittest.TestCase):
    def test_retries_429_then_succeeds(self):
        t = FakeTransport([
            RawResponse(429, {"retry-after": "0"}, b"", f"{BASE}/x"),
            RawResponse(200, {"content-type": "text/html"}, b"ok", f"{BASE}/x"),
        ])
        resp = client(t).get(f"{BASE}/x")
        self.assertEqual(resp.status, 200)
        self.assertEqual(len(t.calls), 2)

    def test_network_error_raises_after_retries(self):
        t = FakeTransport([NetworkFailed("boom")] * 4)
        with self.assertRaises(NetworkFailed):
            client(t).get(f"{BASE}/x")


class DownloadTests(unittest.TestCase):
    def test_download_streams_hashes_and_commits(self):
        import hashlib
        import tempfile
        chunks = [b"abc", b"def"]
        c = client(FakeTransport([]), stream_open=FakeStream(chunks, {"content-type": "application/pdf", "content-length": "6"}))
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "sub" / "file.pdf"
            res = c.download(f"{BASE}/pluginfile.php/1/x.pdf", str(dest))
            self.assertEqual(dest.read_bytes(), b"abcdef")
            self.assertEqual(res.sha256, hashlib.sha256(b"abcdef").hexdigest())
            self.assertEqual(res.bytes_written, 6)
            self.assertEqual(res.content_type, "application/pdf")
            self.assertFalse(list(Path(d).glob("*.part")))

    def test_download_rejects_html_login_page(self):
        c = client(FakeTransport([]), stream_open=FakeStream([b"<html>login</html>"],
                                                             {"content-type": "text/html"}))
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "file.pdf"
            with self.assertRaises(ContentMismatch):
                c.download(f"{BASE}/pluginfile.php/1/x.pdf", str(dest))
            self.assertFalse(dest.exists())

    def test_download_content_length_mismatch_leaves_no_file(self):
        c = client(FakeTransport([]), stream_open=FakeStream([b"abc"], {"content-type": "application/pdf",
                                                                        "content-length": "10"}))
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "file.pdf"
            with self.assertRaises(ContentMismatch):
                c.download(f"{BASE}/pluginfile.php/1/x.pdf", str(dest))
            self.assertFalse(dest.exists())
            self.assertFalse(list(Path(d).glob("*.part")))

    def test_download_refuses_blocked_url(self):
        c = client(FakeTransport([]))
        with self.assertRaises(BlockedUrl):
            c.download("https://127.0.0.1/x.pdf", "/tmp/x.pdf")


class ExternalRedirectTests(unittest.TestCase):
    def test_external_redirect_strips_cookie(self):
        t = FakeTransport([
            RawResponse(302, {"location": "https://d25zr1xy094zys.cloudfront.net/file.pdf"}, b"",
                        f"{BASE}/pluginfile.php/1/x.pdf"),
            RawResponse(200, {"content-type": "application/pdf", "content-length": "6"}, b"abcdef",
                        "https://d25zr1xy094zys.cloudfront.net/file.pdf"),
        ])
        c = client(t)
        resp = c.request("GET", f"{BASE}/pluginfile.php/1/x.pdf", external=True)
        self.assertEqual(resp.status, 200)
        self.assertIn("Cookie", t.calls[0]["headers"])
        self.assertNotIn("Cookie", t.calls[1]["headers"])

    def test_download_follows_cdn_redirect_without_cookie(self):
        import tempfile
        sq = FakeStreamQueue([
            {"status": 303, "headers": {"location": "https://d25zr1xy094zys.cloudfront.net/x.pdf"}},
            {"status": 200, "headers": {"content-type": "application/pdf", "content-length": "6"},
             "chunks": [b"abcdef"]},
        ])
        c = client(FakeTransport([]), stream_open=sq)
        with tempfile.TemporaryDirectory() as d:
            res = c.download(f"{BASE}/pluginfile.php/1/x.pdf", str(Path(d) / "x.pdf"))
            self.assertEqual(res.bytes_written, 6)
        self.assertIn("Cookie", sq.calls[0]["headers"])
        self.assertNotIn("Cookie", sq.calls[1]["headers"])

    def test_external_url_rejects_private_host(self):
        from lib.moodle_http import validate_external_url
        with self.assertRaises(BlockedUrl):
            validate_external_url("https://127.0.0.1/x", dns_check=False)
        with self.assertRaises(BlockedUrl):
            validate_external_url("https://localhost/x", dns_check=False)


class RedactionTests(unittest.TestCase):
    def test_cookie_value_never_survives_redaction(self):
        c = client(FakeTransport([]))
        c._headers()  # registers the cookie as a secret
        text = c.redact("Cookie: MoodleSession=abc; MOODLEID1_=def; sesskey=s3ssk3y")
        self.assertNotIn("abc", text)
        self.assertNotIn("def", text)
        self.assertNotIn("s3ssk3y", text)

    def test_login_page_detection(self):
        self.assertTrue(looks_like_login_page('<input type="hidden" name="logintoken" value="x">'))
        self.assertFalse(looks_like_login_page("<html>dashboard</html>"))

    def test_decode_body_honours_charset(self):
        self.assertEqual(decode_body("héllo".encode("latin-1"), "text/html; charset=latin-1"), "héllo")


if __name__ == "__main__":
    unittest.main()
