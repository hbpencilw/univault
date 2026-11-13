import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from lib import moodle_api  # noqa: E402
from lib.moodle_http import HttpClient, RawResponse  # noqa: E402
from fakes import FakeTransport, err_json, ok_json  # noqa: E402

HOST = "learning.monash.edu"
BASE = f"https://{HOST}"
SESSKEY = "s3ssk3y"


def client(responses):
    return HttpClient(BASE, "MoodleSession=abc", transport=FakeTransport(responses),
                      dns_check=False, min_interval=0.0)


class CallTests(unittest.TestCase):
    def test_rejects_non_whitelisted_function(self):
        with self.assertRaises(ValueError):
            moodle_api.call(client([]), SESSKEY, "core_user_delete_users", {})

    def test_success_returns_data(self):
        c = client([ok_json({"courses": [{"id": 1}]})])
        r = moodle_api.call(c, SESSKEY, "core_course_get_enrolled_courses_by_timeline_classification",
                            {"classification": "inprogress", "limit": 100, "offset": 0})
        self.assertEqual(r.status, "supported")
        self.assertEqual(r.data, {"courses": [{"id": 1}]})
        sent = c._transport.calls[0]
        self.assertIn("sesskey=" + SESSKEY, sent["url"])
        self.assertIn(b"methodname", sent["data"])

    def test_missing_sesskey_is_session_expired(self):
        r = moodle_api.call(client([]), "", "core_course_get_contents", {"courseid": 1})
        self.assertEqual(r.status, "session-expired")

    def test_error_classification(self):
        cases = {
            "servicenotavailable": "unsupported",
            "invalidparameter": "invalid-arguments",
            "nopermissions": "permission-denied",
            "requireloginerror": "session-expired",
            "invalidtoken": "session-expired",
            "something_odd": "unknown",
        }
        for code, expected in cases.items():
            with self.subTest(code=code):
                r = moodle_api.call(client([err_json(code)]), SESSKEY, "core_course_get_contents", {"courseid": 1})
                self.assertEqual(r.status, expected)

    def test_malformed_json_is_unknown_not_crash(self):
        resp = RawResponse(200, {"content-type": "text/html"}, b"<html>login page</html>", f"{BASE}/lib/ajax/service.php")
        r = moodle_api.call(client([resp]), SESSKEY, "core_course_get_contents", {"courseid": 1})
        self.assertEqual(r.status, "unknown")
        self.assertEqual(r.error_code, "malformed-json")

    def test_http_403_is_permission_denied(self):
        resp = RawResponse(403, {}, b"", f"{BASE}/lib/ajax/service.php")
        r = moodle_api.call(client([resp]), SESSKEY, "core_course_get_contents", {"courseid": 1})
        self.assertEqual(r.status, "permission-denied")

    def test_sso_redirect_during_call_is_session_expired(self):
        resp = RawResponse(303, {"location": "https://monashuni.okta.com/app/sso/saml"}, b"", f"{BASE}/lib/ajax/service.php")
        r = moodle_api.call(client([resp]), SESSKEY, "core_course_get_contents", {"courseid": 1})
        self.assertEqual(r.status, "session-expired")


class ProbeTests(unittest.TestCase):
    def test_probe_all_supported(self):
        responses = [
            ok_json({"courses": [{"id": 101, "shortname": "UNIT1001"}]}),
            ok_json({"cm": [], "section": []}),
            ok_json([{"id": 5, "name": "Week 1", "modules": []}]),
            ok_json([{"id": 55, "name": "Announcements", "type": "news"}]),
            ok_json([{"discussion": 777, "name": "Hello"}]),
            ok_json({"posts": [{"id": 1}]}),
        ]
        summary = moodle_api.probe(client(responses), SESSKEY, delay=0)
        statuses = {r["function"]: r["status"] for r in summary["functions"]}
        self.assertEqual(statuses["core_course_get_enrolled_courses_by_timeline_classification"], "supported")
        self.assertEqual(statuses["core_course_get_contents"], "supported")
        self.assertEqual(statuses["mod_forum_get_forum_discussions"], "supported")
        self.assertEqual(statuses["mod_forum_get_forum_discussion_posts"], "supported")
        self.assertEqual(summary["supported"], 6)
        self.assertEqual(summary["course_sample"], {"id": 101, "shortname": "UNIT1001"})

    def test_probe_without_course_marks_dependents_unknown(self):
        responses = [
            err_json("servicenotavailable"),
            err_json("servicenotavailable"),
        ]
        summary = moodle_api.probe(client(responses), SESSKEY, delay=0)
        by_fn = {r["function"]: r["status"] for r in summary["functions"]}
        self.assertEqual(by_fn["core_course_get_enrolled_courses_by_timeline_classification"], "unsupported")
        self.assertEqual(by_fn["core_course_get_contents"], "unknown")
        self.assertEqual(by_fn["mod_forum_get_forum_discussions"], "unknown")
        self.assertEqual(summary["supported"], 0)


if __name__ == "__main__":
    unittest.main()
