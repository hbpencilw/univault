import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from lib import ed_lessons  # noqa: E402


class XmlToMarkdownTests(unittest.TestCase):
    def test_document_blocks(self):
        xml = ('<document version="2.0">'
               '<heading level="1">Tasks</heading>'
               '<paragraph>Read <bold>carefully</bold> and use <math>e^x</math>.</paragraph>'
               '<list style="bullet"><list-item><paragraph>one</paragraph></list-item>'
               '<list-item><paragraph>two</paragraph></list-item></list>'
               '<break/><paragraph>done</paragraph></document>')
        md = ed_lessons.xml_to_markdown(xml)
        self.assertIn("# Tasks", md)
        self.assertIn("**carefully**", md)
        self.assertIn("$e^x$", md)
        self.assertIn("- one", md)
        self.assertIn("- two", md)
        self.assertIn("done", md)

    def test_regex_fallback(self):
        md = ed_lessons.xml_to_markdown("<document><paragraph>hello &amp; bye</paragraph>")
        self.assertIn("hello & bye", md)

    def test_empty(self):
        self.assertEqual(ed_lessons.xml_to_markdown(""), "")


class UrlAndPathTests(unittest.TestCase):
    def test_file_url_rules(self):
        ok = ed_lessons.validate_file_url(
            "https://static.edusercontent.com/files/abc", dns_check=False)
        self.assertEqual(ok.hostname, "static.edusercontent.com")
        for bad in ("http://static.edusercontent.com/f",
                    "https://evil.example/f",
                    "https://127.0.0.1/f"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ed_lessons.validate_file_url(bad, dns_check=False)

    def test_safe_dest_rejects_traversal(self):
        with self.assertRaises(ValueError):
            ed_lessons.safe_dest("../evil")

    def test_slug(self):
        self.assertEqual(ed_lessons.slug("Topic 1: Intro/DB"), "Topic_1_IntroDB")


class LessonRecordTests(unittest.TestCase):
    def test_record_without_downloads(self):
        lesson = {
            "id": 1, "index": 1, "title": "Topic 1", "kind": "content", "course_id": 10001,
            "slides": [
                {"id": 10, "index": 1, "type": "document", "title": "Overview",
                 "content": '<document><paragraph>Hello</paragraph></document>'},
                {"id": 11, "index": 2, "type": "quiz", "title": "Review",
                 "passage": '<document><paragraph>Answer this</paragraph></document>'},
                {"id": 12, "index": 3, "type": "pdf", "title": "Slides",
                 "file_url": "https://static.edusercontent.com/files/xyz"},
            ],
        }
        rec = ed_lessons.lesson_record(lesson, download_files=False, dest="/tmp/ed-lessons-test")
        self.assertEqual(rec["kind"], "lesson")
        self.assertIn("Hello", rec["slides"][0]["text_md"])
        self.assertTrue(rec["slides"][1]["quiz"])
        self.assertIn("static.edusercontent.com", rec["slides"][2]["pdf"]["url"])

    def test_render_markdown(self):
        rec = {"index": 1, "title": "Topic 1", "slides": [
            {"index": 1, "title": "Overview", "text_md": "Hello"},
            {"index": 2, "title": "Slides", "pdf": {"path": "lesson_files/x/s.pdf"}},
        ]}
        md = ed_lessons.render_markdown([rec], 10001)
        self.assertIn("## 1. Topic 1", md)
        self.assertIn("Hello", md)
        self.assertIn("[PDF](lesson_files/x/s.pdf)", md)


if __name__ == "__main__":
    unittest.main()
