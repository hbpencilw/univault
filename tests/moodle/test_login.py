import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from lib import moodle_login  # noqa: E402


class CookieHeaderTests(unittest.TestCase):
    def test_filters_by_host_and_builds_header(self):
        cookies = [
            {"name": "MoodleSession", "value": "abc", "domain": "learning.monash.edu"},
            {"name": "MOODLEID1_", "value": "def", "domain": ".learning.monash.edu"},
            {"name": "_ga", "value": "analytics", "domain": ".monash.edu"},
            {"name": "other", "value": "x", "domain": "example.com"},
        ]
        header = moodle_login.cookies_to_header(cookies, "learning.monash.edu")
        self.assertIn("MoodleSession=abc", header)
        self.assertIn("MOODLEID1_=def", header)
        self.assertNotIn("analytics", header)
        self.assertNotIn("example.com", header)

    def test_empty(self):
        self.assertEqual(moodle_login.cookies_to_header([], "learning.monash.edu"), "")


class DashboardDetectionTests(unittest.TestCase):
    HOST = "learning.monash.edu"

    def test_authenticated_pages(self):
        for url in ("https://learning.monash.edu/my/",
                    "https://learning.monash.edu/course/view.php?id=1",
                    "https://learning.monash.edu/"):
            with self.subTest(url=url):
                self.assertTrue(moodle_login.is_dashboard_url(url, self.HOST))

    def test_rejects_login_and_sso(self):
        for url in ("https://learning.monash.edu/login/index.php",
                    "https://monashuni.okta.com/app/x/sso/saml",
                    "https://learning.monash.edu/mod/forum/view.php?id=1"):
            with self.subTest(url=url):
                self.assertFalse(moodle_login.is_dashboard_url(url, self.HOST))


if __name__ == "__main__":
    unittest.main()
