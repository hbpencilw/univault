import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from lib import moodle_api, moodle_scan  # noqa: E402
from lib.moodle_api import AjaxResult  # noqa: E402

BASE = "https://learning.monash.edu"

STATE = {
    "section": [{"id": 1, "number": 0, "title": None, "cmlist": [11, 12, 13, 14, 15], "parent": None}],
    "cm": [
        {"id": 11, "modname": "File", "name": "Notes", "url": f"{BASE}/mod/resource/view.php?id=11", "uservisible": True},
        {"id": 12, "modname": "Folder", "name": "Slides", "url": f"{BASE}/mod/folder/view.php?id=12", "uservisible": True},
        {"id": 13, "modname": "Forum", "name": "Announcements", "url": f"{BASE}/mod/forum/view.php?id=13", "uservisible": True},
        {"id": 14, "modname": "Page", "name": "Intro", "url": f"{BASE}/mod/page/view.php?id=14", "uservisible": True},
        {"id": 15, "modname": "Text and media area", "name": "Banner", "url": None, "uservisible": True},
    ],
}

RESOURCE = f'<a href="{BASE}/pluginfile.php/9/mod_resource/content/1/MathWorkbook.pdf">MathWorkbook.pdf</a>'
FOLDER = (f'<a href="{BASE}/pluginfile.php/11/mod_folder/content/0/a.pdf">a.pdf</a>'
          f'<a href="{BASE}/pluginfile.php/12/mod_folder/content/0/b.pptx">b.pptx</a>')
FORUM = f'<tr><td><a href="{BASE}/mod/forum/discuss.php?d=55">Subject A</a></td><td>Alice</td><td>2</td></tr>'
SECTION_PAGE = ('<li class="activity activity-wrapper label modtype_label" id="module-15" '
                'data-id="15" data-activityname="Banner"><div class="activity-altcontent">'
                '<div class="no-overflow"><p>Label body</p></div></div></li>'
                '<div class="assessmentsummary"><div>Assessment structure</div></div>')


class FakeClient:
    def __init__(self):
        self.base_url = BASE
        self.host = "learning.monash.edu"
        self.get_calls = []
        self.head_calls = []
        self.pages = {
            f"{BASE}/mod/resource/view.php?id=11": RESOURCE,
            f"{BASE}/mod/folder/view.php?id=12": FOLDER,
            f"{BASE}/mod/forum/view.php?id=13": FORUM,
            f"{BASE}/course/view.php?id=1&section=0": SECTION_PAGE,
        }

    def get(self, url):
        self.get_calls.append(url)
        body = self.pages.get(url)
        if body is None:
            raise AssertionError(f"unexpected GET {url}")
        return SimpleNamespace(body=body.encode(), status=200, headers={}, url=url)

    def head(self, url, external=False):
        self.head_calls.append(url)
        return SimpleNamespace(status=200, headers={"content-length": "1234"}, body=b"", url=url)


def fake_call(client, sesskey, function, args=None):
    if function == "core_course_get_enrolled_courses_by_timeline_classification":
        return AjaxResult(function, "supported", data={"courses": [
            {"id": 1, "shortname": "UNIT1001_S2_2026", "fullname": "Modelling for data analysis"}]})
    if function == "core_courseformat_get_state":
        return AjaxResult(function, "supported", data=json.dumps(STATE))
    return AjaxResult(function, "unsupported")


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.session = SimpleNamespace(sesskey="k", userid="1")
        self._tmp = tempfile.TemporaryDirectory()
        self.dest = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def run_scan(self, dest=None, **kw):
        with mock.patch.object(moodle_api, "call", side_effect=fake_call):
            return moodle_scan.scan(self.client, self.session, str(dest or self.dest), **kw)

    def test_scan_counts_and_no_download(self):
        manifest = self.run_scan()
        totals = manifest["totals"]
        self.assertEqual(totals["courses"], 1)
        self.assertEqual(totals["activities"], 5)      # resource, folder, forum, page, label
        self.assertEqual(totals["files"], 3)           # 1 resource + 2 folder files
        self.assertEqual(totals["announcements"], 1)
        self.assertEqual(totals["unknown_sizes"], 0)

        course = manifest["courses"][0]
        self.assertEqual(course["code"], "UNIT1001")
        self.assertEqual(course["sections"], 0)        # only section 0 present
        self.assertEqual(course["announcements_forum"]["name"], "Announcements")
        label = next(r for r in course["resources"] if r["type"] == "label")
        self.assertTrue(label["content_available"])
        self.assertEqual(label["category"], "label")
        self.assertEqual(totals["categories"].get("label"), 1)
        self.assertEqual(totals["categories"].get("file"), 2)
        # assessment widget is recorded as evidence, never as a section summary
        self.assertEqual(totals["section_summaries"], 0)
        self.assertEqual(totals["assessment_widgets"], 1)
        detail = course["sections_detail"][0]
        self.assertFalse(detail["summary_available"])
        self.assertTrue(detail["assessment_widget_present"])

        # no file bodies were fetched
        self.assertFalse(any("pluginfile.php" in u for u in self.client.get_calls))

    def test_scan_writes_only_state_and_reports(self):
        self.run_scan()
        files = {str(p.relative_to(self.dest)) for p in self.dest.rglob("*") if p.is_file()}
        self.assertIn("_state/scan-latest.json", files)
        self.assertTrue(any(f.startswith("_reports/scan-") and f.endswith(".md") for f in files))
        self.assertTrue(all(f.startswith("_state/") or f.startswith("_reports/") for f in files),
                        f"unexpected files: {files}")
        self.assertFalse((self.dest / "moodle_files").exists())

    def test_scan_manifest_is_valid_json_and_lists_resources(self):
        self.run_scan()
        payload = json.loads((self.dest / "_state" / "scan-latest.json").read_text())
        resources = payload["courses"][0]["resources"]
        self.assertEqual(len(resources), 5)  # resource, folder, forum, page, label
        file_entry = next(r for r in resources if r["type"] == "resource")
        self.assertEqual(file_entry["files"][0]["size"], 1234)

    def test_scan_rejects_traversal_dest(self):
        with self.assertRaises(ValueError):
            self.run_scan(dest=Path(self._tmp.name) / ".." / "evil")

    def test_scan_course_filter(self):
        manifest = self.run_scan(course_ids=[999])
        self.assertEqual(manifest["totals"]["courses"], 0)

    def test_announcement_pagination(self):
        def page(start, count):
            rows = "".join(
                f'<tr><td><a href="{BASE}/mod/forum/discuss.php?d={start + i}">S{start + i}</a></td>'
                f"<td>A</td><td>0</td></tr>"
                for i in range(count)
            )
            return f"<table><tbody>{rows}</tbody></table>"

        self.client.pages[f"{BASE}/mod/forum/view.php?id=13"] = page(100, 10)
        self.client.pages[f"{BASE}/mod/forum/view.php?id=13&page=1"] = page(200, 2)
        manifest = self.run_scan()
        course = manifest["courses"][0]
        self.assertEqual(len(course["announcements"]), 12)
        self.assertIn(f"{BASE}/mod/forum/view.php?id=13&page=1", self.client.get_calls)


if __name__ == "__main__":
    unittest.main()
