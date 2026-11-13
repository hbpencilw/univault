import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from lib import moodle_auth  # noqa: E402
from lib.moodle_errors import AuthExpired  # noqa: E402
from lib.moodle_http import HttpClient, RawResponse  # noqa: E402
from fakes import FakeTransport, login_page, my_page  # noqa: E402

HOST = "learning.monash.edu"
BASE = f"https://{HOST}"


class AuthTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        d = Path(self._tmp.name)
        self._saved = (moodle_auth.CONFIG_DIR, moodle_auth.COOKIE_FILE, moodle_auth.CAPABILITIES_FILE)
        moodle_auth.CONFIG_DIR = d
        moodle_auth.COOKIE_FILE = d / "moodle-cookie"
        moodle_auth.CAPABILITIES_FILE = d / "capabilities.json"

    def tearDown(self):
        moodle_auth.CONFIG_DIR, moodle_auth.COOKIE_FILE, moodle_auth.CAPABILITIES_FILE = self._saved
        self._tmp.cleanup()

    def client(self, responses):
        return HttpClient(BASE, "MoodleSession=abc", transport=FakeTransport(responses),
                          dns_check=False, min_interval=0.0)


class CookieParsingTests(AuthTestCase):
    def test_parse_basic(self):
        pairs = moodle_auth.parse_cookie_header("MoodleSession=abc; MOODLEID1_=xyz")
        self.assertEqual(pairs, {"MoodleSession": "abc", "MOODLEID1_": "xyz"})

    def test_rejects_malformed(self):
        for raw in ("", "novalue", "=novalue", "a=b; broken"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                moodle_auth.parse_cookie_header(raw)

    def test_shape_never_leaks_values(self):
        shape = moodle_auth.cookie_shape({"MoodleSession": "supersecret"})
        self.assertIn("MoodleSession", shape)
        self.assertNotIn("supersecret", shape)


class CookieStorageTests(AuthTestCase):
    def test_save_permissions_and_roundtrip(self):
        payload = moodle_auth.save_cookie("MoodleSession=abc; MOODLEID1_=xyz", BASE)
        self.assertEqual(payload["host"], HOST)
        self.assertEqual(stat.S_IMODE(os.stat(moodle_auth.COOKIE_FILE).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(moodle_auth.CONFIG_DIR).st_mode), 0o700)
        loaded = moodle_auth.load_cookie()
        self.assertEqual(loaded["cookie"], "MoodleSession=abc; MOODLEID1_=xyz")

    def test_load_legacy_plain_text(self):
        moodle_auth.ensure_config_dir()
        moodle_auth.COOKIE_FILE.write_text("MoodleSession=legacy")
        loaded = moodle_auth.load_cookie()
        self.assertEqual(loaded["schema"], 0)
        self.assertIn("MoodleSession=legacy", loaded["cookie"])

    def test_clear_removes_both_files(self):
        moodle_auth.save_cookie("MoodleSession=abc", BASE)
        moodle_auth.CAPABILITIES_FILE.write_text("{}")
        self.assertTrue(moodle_auth.clear_cookie())
        self.assertFalse(moodle_auth.COOKIE_FILE.exists())
        self.assertFalse(moodle_auth.CAPABILITIES_FILE.exists())

    def test_import_from_file(self):
        p = moodle_auth.CONFIG_DIR / "in.txt"
        p.write_text("MoodleSession=fromfile")
        self.assertEqual(moodle_auth.import_from_file(str(p)), "MoodleSession=fromfile")


class SessionValidationTests(AuthTestCase):
    def test_valid_session_extracts_fields(self):
        c = self.client([RawResponse(200, {"content-type": "text/html"},
                                     my_page(sesskey="KEY1", userid="999", courses=(10001, 10002)).encode(), f"{BASE}/my/")])
        s = moodle_auth.validate_session(c, BASE)
        self.assertEqual(s.sesskey, "KEY1")
        self.assertEqual(s.userid, "999")
        self.assertEqual(s.course_ids, [10001, 10002])
        self.assertIn("4.5.1", s.release)
        self.assertEqual(s.warnings, [])

    def test_login_page_raises_auth_expired(self):
        c = self.client([RawResponse(200, {"content-type": "text/html"}, login_page().encode(), f"{BASE}/login/index.php")])
        with self.assertRaises(AuthExpired):
            moodle_auth.validate_session(c, BASE)

    def test_missing_sesskey_produces_warning_not_failure(self):
        html = "<html><body>dashboard</body></html>"
        c = self.client([RawResponse(200, {"content-type": "text/html"}, html.encode(), f"{BASE}/my/")])
        s = moodle_auth.validate_session(c, BASE)
        self.assertIsNone(s.sesskey)
        self.assertTrue(any("sesskey" in w for w in s.warnings))

    def test_no_courses_produces_warning(self):
        c = self.client([RawResponse(200, {"content-type": "text/html"},
                                     my_page(courses=()).encode(), f"{BASE}/my/")])
        s = moodle_auth.validate_session(c, BASE)
        self.assertFalse(s.has_courses)
        self.assertTrue(any("course" in w for w in s.warnings))


if __name__ == "__main__":
    unittest.main()
