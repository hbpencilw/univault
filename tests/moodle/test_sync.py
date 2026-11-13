import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from lib import moodle_sync  # noqa: E402
from lib.moodle_errors import ContentMismatch, DiskFull  # noqa: E402

BASE = "https://learning.monash.edu"

PAGE_HTML = ('<html><body><div id="page-content">'
             '<p>hello page</p><script>evil()</script><a href="x?token=SECRET">l</a>'
             '</div></body></html>')

SECTION_HTML = """
<li class="activity activity-wrapper label modtype_label" id="module-12"
    data-id="12" data-activityname="Banner">
  <div class="activity-altcontent"><div class="no-overflow">
    <p>Label body with <img src="https://learning.monash.edu/pluginfile.php/7/img.png"></p>
  </div></div>
</li>
<div class="summary"><p>Section summary text</p></div>
<div class="assessmentsummary"><div class="assessment-structure-header">Assessment structure</div></div>
"""

DISCUSSION_HTML = """<html><body>
<article id="p100" class="forum-post-container" data-post-id="100">
  <div class="forumpost firstpost starter" data-post-id="100">
    <h3 data-region-content="forum-post-core-subject">Hello</h3>
    <div>by <a href="https://learning.monash.edu/user/view.php?id=1">Alice</a>
    <time datetime="2026-07-22T15:19:19+10:00">22 July</time></div>
    <div id="post-content-100" class="post-content-container">
      <p>Root body</p>
      <a href="https://learning.monash.edu/pluginfile.php/1/att.pdf">att.pdf</a>
      <a href="https://learning.monash.edu/pluginfile.php/1/att.pdf">att.pdf again</a>
    </div>
    <div class="indent" data-region="replies-container">
      <article id="p101" class="forum-post-container" data-post-id="101">
        <h3 data-region-content="forum-post-core-subject">Re: Hello</h3>
        <div>by <a href="https://learning.monash.edu/user/view.php?id=2">Bob</a></div>
        <div id="post-content-101" class="post-content-container"><p>Reply body</p></div>
      </article>
    </div>
  </div>
</article></body></html>"""


class FakeClient:
    def __init__(self, pages, files, fail=(), errors=None):
        self.base_url = BASE
        self.host = "learning.monash.edu"
        self.pages = pages
        self.files = files
        self.fail = set(fail)
        self.errors = errors or {}
        self.get_calls = []
        self.download_calls = []

    def get(self, url):
        self.get_calls.append(url)
        if url in self.fail:
            raise ContentMismatch("page failed")
        return SimpleNamespace(body=self.pages[url].encode(), status=200, headers={}, url=url)

    def download(self, url, dest, expected_size=0, max_bytes=None):
        self.download_calls.append(url)
        if url in self.fail:
            raise ContentMismatch("download failed")
        if url in self.errors:
            raise self.errors[url]
        data = self.files[url]
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(data)
        return SimpleNamespace(sha256=hashlib.sha256(data).hexdigest(), bytes_written=len(data),
                               content_type="application/pdf", content_disposition=None)


def pages():
    return {
        f"{BASE}/mod/page/view.php?id=11": PAGE_HTML,
        f"{BASE}/course/view.php?id=1&section=0": SECTION_HTML,
        f"{BASE}/mod/forum/discuss.php?d=50": DISCUSSION_HTML,
    }


def files():
    return {
        f"{BASE}/mod/resource/view.php?id=10": b"%PDF-1.4 test",
        f"{BASE}/pluginfile.php/7/img.png": b"PNGDATA",
        f"{BASE}/pluginfile.php/1/att.pdf": b"attachment",
    }


def manifest(course_dir):
    return {"host": "learning.monash.edu", "courses": [{
        "id": 1, "code": "UNIT1001", "shortname": "UNIT1001_S2_2026", "suggested_dir": course_dir,
        "resources": [
            {"cmid": 10, "type": "resource", "name": "Notes", "section": 1,
             "url": f"{BASE}/mod/resource/view.php?id=10", "category": "file",
             "files": [{"url": f"{BASE}/mod/resource/view.php?id=10", "name": "Notes.pdf", "size": 5}]},
            {"cmid": 11, "type": "page", "name": "Week 1", "section": 1,
             "url": f"{BASE}/mod/page/view.php?id=11", "files": [], "category": "page"},
            {"cmid": 12, "type": "label", "name": "Banner", "section": 0, "url": None,
             "files": [], "category": "label", "content_available": True},
        ],
        "sections_detail": [{"number": 0, "title": None, "hassummary": True,
                             "summary_available": True, "summary_kind": "standard",
                             "assessment_widget_present": True, "label_cmids": [12]},
                            {"number": 59, "title": "Examination", "hassummary": True,
                             "summary_available": False, "summary_kind": None,
                             "assessment_widget_present": True, "label_cmids": []}],
        "announcements_forum": {"cmid": 99, "name": "Announcements"},
        "announcements": [{"discussion_id": 50, "subject": "Hello", "replies": 1}],
    }]}


class SyncTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.course_dir = Path(self._tmp.name) / "course"
        self.client = FakeClient(pages(), files())

    def tearDown(self):
        self._tmp.cleanup()

    def run_sync(self, **kw):
        return moodle_sync.sync(self.client, SimpleNamespace(sesskey="k"), manifest(str(self.course_dir)), **kw)

    def test_sync_archives_everything(self):
        result = self.run_sync()
        self.assertEqual(result["status"], "complete")
        summary = result["courses"][0]
        # file, page, label, section summary, root, reply, attachment
        self.assertGreaterEqual(summary["new"], 7)
        self.assertEqual(summary["labels"], 1)
        self.assertEqual(summary["section_summaries"], 1)
        self.assertEqual(summary["discussions"], 1)
        self.assertEqual(summary["posts"], 2)
        self.assertEqual(summary["replies"], 1)
        self.assertEqual(summary["attachments"], 1)

        pdf = list(self.course_dir.rglob("Notes.pdf"))
        self.assertEqual(pdf[0].read_bytes(), b"%PDF-1.4 test")
        label_html = next(self.course_dir.rglob("labels/12-Banner.html")).read_text()
        self.assertIn("Label body", label_html)
        self.assertIn('src="12-Banner.assets/', label_html)  # image rewritten locally
        self.assertTrue(list(self.course_dir.rglob("12-Banner.assets/img.png")))
        summary_html = next(self.course_dir.rglob("summary.html")).read_text()
        self.assertIn("Section summary text", summary_html)
        self.assertNotIn("Assessment structure", summary_html)
        # a widget-only section is counted as skipped, never archived as a summary
        self.assertEqual(summary["assessment_widgets_skipped"], 1)

        records = [json.loads(line) for line in (self.course_dir / "moodle.jsonl").read_text().splitlines()]
        self.assertTrue(any(r["kind"] == "label" and r["content_hash"] for r in records))
        self.assertTrue(any(r["kind"] == "section" for r in records))
        reply = next(r for r in records if r["kind"] == "announcement" and r["extra"].get("role") == "reply")
        self.assertEqual(reply["extra"]["parent_post_id"], 100)

    def test_hash_matches_jsonl_and_no_signed_urls(self):
        self.run_sync()
        records = [json.loads(line) for line in (self.course_dir / "moodle.jsonl").read_text().splitlines()]
        for rec in records:
            rel = rec.get("relative_path")
            if rel and rec.get("version_id"):
                digest = hashlib.sha256((self.course_dir / rel).read_bytes()).hexdigest()
                self.assertEqual(digest, rec["version_id"])
        blob = "".join(p.read_text(errors="ignore") for p in self.course_dir.rglob("*") if p.is_file())
        self.assertNotIn("cloudfront", blob.lower())
        self.assertNotIn("response-content", blob.lower())
        self.assertNotIn("token=", blob.lower())

    def test_second_sync_is_idempotent(self):
        self.run_sync()
        first = (self.course_dir / "moodle.jsonl").read_text()
        history_before = list((self.course_dir / "_history").rglob("*"))
        result = self.run_sync()
        self.assertEqual(result["courses"][0]["new"], 0)
        self.assertGreaterEqual(result["courses"][0]["unchanged"], 7)
        second = (self.course_dir / "moodle.jsonl").read_text()
        self.assertEqual(first, second, "moodle.jsonl must be byte-stable across idempotent runs")
        self.assertEqual(history_before, list((self.course_dir / "_history").rglob("*")))

    def test_selective_cmid_only(self):
        result = self.run_sync(cmids=[10])
        self.assertEqual(result["courses"][0]["new"], 1)
        self.assertEqual(self.client.download_calls, [f"{BASE}/mod/resource/view.php?id=10"])
        page_files = list((self.course_dir / "pages").rglob("*")) if (self.course_dir / "pages").exists() else []
        self.assertEqual(page_files, [])
        records = [json.loads(line) for line in (self.course_dir / "moodle.jsonl").read_text().splitlines()]
        self.assertEqual({r["logical_id"] for r in records}, {"cm:10"})

    def test_selective_discussion_only(self):
        result = self.run_sync(discussion_ids=[50])
        summary = result["courses"][0]
        self.assertEqual(summary["discussions"], 1)
        self.assertGreaterEqual(summary["new"], 3)  # root, reply, attachment
        self.assertIn(f"{BASE}/pluginfile.php/1/att.pdf", self.client.download_calls)
        self.assertNotIn(f"{BASE}/mod/resource/view.php?id=10", self.client.download_calls)

    def test_selective_no_match_raises(self):
        with self.assertRaises(ValueError):
            self.run_sync(cmids=[999])

    def test_disk_full_keeps_previous_version(self):
        self.run_sync()
        # change content but make the download fail with DISK_FULL
        self.client.errors[f"{BASE}/mod/resource/view.php?id=10"] = DiskFull("no space")
        self.client.files[f"{BASE}/mod/resource/view.php?id=10"] = b"%PDF-2.0 changed"
        result = self.run_sync()
        summary = result["courses"][0]
        self.assertEqual(summary["failed"], 1)
        current = next(self.course_dir.rglob("Notes.pdf"))
        self.assertEqual(current.read_bytes(), b"%PDF-1.4 test")
        self.assertEqual(list((self.course_dir / "_history").rglob("*.pdf")), [])

    def test_sequential_selective_syncs_merge(self):
        self.run_sync(cmids=[10])
        recs1 = {json.loads(line)["logical_id"]: json.loads(line)
                 for line in (self.course_dir / "moodle.jsonl").read_text().splitlines()}
        self.assertEqual(set(recs1), {"cm:10"})

        self.run_sync(cmids=[11])
        recs2 = {json.loads(line)["logical_id"]: json.loads(line)
                 for line in (self.course_dir / "moodle.jsonl").read_text().splitlines()}
        self.assertEqual(set(recs2), {"cm:10", "cm:11"})
        self.assertEqual(recs2["cm:10"]["version_id"], recs1["cm:10"]["version_id"])

        self.run_sync(discussion_ids=[50])
        recs3 = {json.loads(line)["logical_id"]: json.loads(line)
                 for line in (self.course_dir / "moodle.jsonl").read_text().splitlines()}
        self.assertEqual(set(recs3), {"cm:10", "cm:11", "forum-post:100", "forum-post:101",
                                      "attachment:100:att.pdf"})
        self.assertEqual(recs3["cm:10"]["version_id"], recs1["cm:10"]["version_id"])

    def test_announcement_counts_and_unique_ids(self):
        self.run_sync()
        summary = self.__dict__.get("_last_summary")
        records = [json.loads(line) for line in (self.course_dir / "moodle.jsonl").read_text().splitlines()]
        ids = [r["logical_id"] for r in records]
        self.assertEqual(len(ids), len(set(ids)), "duplicate logical ids")
        anns = [r for r in records if r["kind"] == "announcement"]
        roots = [r for r in anns if r["extra"].get("role") == "root"]
        replies = [r for r in anns if r["extra"].get("role") == "reply"]
        attachments = [r for r in records if r["kind"] == "file" and r["extra"].get("parent_post_id") == 100]
        self.assertEqual((len(roots), len(replies), len(attachments)), (1, 1, 1))

    def test_content_change_creates_history(self):
        self.run_sync(cmids=[10])
        old = next(self.course_dir.rglob("Notes.pdf")).read_bytes()
        new = b"%PDF-2.0 changed"
        self.client.files[f"{BASE}/mod/resource/view.php?id=10"] = new
        result = self.run_sync(cmids=[10])
        self.assertEqual(result["courses"][0]["updated"], 1)
        self.assertEqual(next(self.course_dir.rglob("Notes.pdf")).read_bytes(), new)
        hist = list((self.course_dir / "_history").rglob("*.pdf"))
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0].read_bytes(), old)
        rec = next(json.loads(line) for line in
                   (self.course_dir / "moodle.jsonl").read_text().splitlines()
                   if json.loads(line)["logical_id"] == "cm:10")
        self.assertEqual(rec["version_id"], hashlib.sha256(new).hexdigest())
        self.assertEqual(rec["history"], [hashlib.sha256(old).hexdigest()])

    def test_single_failure_is_isolated(self):
        self.client.fail.add(f"{BASE}/mod/page/view.php?id=11")
        result = self.run_sync()
        self.assertEqual(result["courses"][0]["failed"], 1)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(list(self.course_dir.rglob("Notes.pdf")))

    def test_dry_run_writes_nothing(self):
        result = self.run_sync(dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertGreater(result["totals"]["planned"], 0)
        self.assertFalse((self.course_dir / "moodle.jsonl").exists())
        self.assertEqual(list(self.course_dir.rglob("*.pdf")), [])
        self.assertFalse(self.client.download_calls)


if __name__ == "__main__":
    unittest.main()
