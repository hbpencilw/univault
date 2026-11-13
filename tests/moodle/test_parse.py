import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from lib import moodle_parse  # noqa: E402

DISCUSSION = """<html><body>
<article id="p100" class="forum-post-container" data-post-id="100" data-region="post">
  <div class="forumpost firstpost starter" data-post-id="100" data-content="forum-post">
    <header class="header row d-flex">
      <h3 data-region-content="forum-post-core-subject">Main subject</h3>
      <div class="mb-3">by <a href="https://learning.monash.edu/user/view.php?id=1&amp;course=2">Alice</a>
      - <time datetime="2026-07-22T15:19:19+10:00">22 July</time></div>
    </header>
    <div class="body-content-container"><div class="content-alignment-container">
      <div id="post-content-100" class="post-content-container">
        <p>Hello <a href="https://learning.monash.edu/pluginfile.php/1/att.pdf">att.pdf</a></p>
      </div>
    </div></div>
    <div class="indent" data-region="replies-container">
      <article id="p101" class="forum-post-container" data-post-id="101">
        <h3 data-region-content="forum-post-core-subject">Re: Main subject</h3>
        <div>by <a href="https://learning.monash.edu/user/view.php?id=2">Bob</a>
        <time datetime="2026-07-23T10:00:00+10:00">23 July</time></div>
        <div id="post-content-101" class="post-content-container"><p>Reply body</p></div>
      </article>
    </div>
  </div>
</article>
</body></html>"""

FORUM = """<table><tbody>
<tr><td class="topic"><a href="https://learning.monash.edu/mod/forum/discuss.php?d=55">Subject A</a></td>
<td>Alice</td><td>2</td><td>22 July</td></tr>
<tr><td class="topic"><a href="https://learning.monash.edu/mod/forum/discuss.php?d=56">Subject B</a></td>
<td>Bob</td><td>0</td></tr>
</tbody></table>"""

RESOURCE = """<html><body><div id="region-main">
<a href="https://learning.monash.edu/pluginfile.php/9/mod_resource/content/1/MathWorkbook.pdf">MathWorkbook.pdf</a>
</div></body></html>"""

FOLDER = """<html><body><ul>
<li><a href="https://learning.monash.edu/pluginfile.php/11/mod_folder/content/0/a.pdf">a.pdf</a></li>
<li><a href="https://learning.monash.edu/pluginfile.php/12/mod_folder/content/0/b.pptx">b.pptx</a></li>
</ul></body></html>"""


class ForumViewTests(unittest.TestCase):
    def test_parse_discussions(self):
        out = moodle_parse.parse_forum_discussions(FORUM, "https://learning.monash.edu")
        self.assertEqual([d["discussion_id"] for d in out], [55, 56])
        self.assertEqual(out[0]["subject"], "Subject A")
        self.assertEqual(out[0]["replies"], 2)
        self.assertEqual(out[1]["replies"], 0)


class DiscussionTests(unittest.TestCase):
    def test_parse_posts_with_nested_reply(self):
        posts = moodle_parse.parse_discussion(DISCUSSION)
        self.assertEqual(len(posts), 2)
        main, reply = posts
        self.assertEqual(main["post_id"], 100)
        self.assertIsNone(main["parent_post_id"])
        self.assertEqual(main["subject"], "Main subject")
        self.assertEqual(main["author"], "Alice")
        self.assertEqual(main["created_at"], "2026-07-22T15:19:19+10:00")
        self.assertIn("Hello", main["body_html"])
        self.assertEqual([a["name"] for a in main["attachments"]], ["att.pdf"])

        self.assertEqual(reply["post_id"], 101)
        self.assertEqual(reply["parent_post_id"], 100)
        self.assertEqual(reply["author"], "Bob")
        self.assertIn("Reply body", reply["body_html"])


class LinkExtractionTests(unittest.TestCase):
    def test_resource_wrapper_first_pluginfile(self):
        link = moodle_parse.extract_first_pluginfile(RESOURCE, "https://learning.monash.edu")
        self.assertEqual(link["name"], "MathWorkbook.pdf")
        self.assertIn("/pluginfile.php/9/", link["url"])

    def test_folder_links(self):
        links = moodle_parse.extract_pluginfile_links(FOLDER, "https://learning.monash.edu")
        self.assertEqual([l["name"] for l in links], ["a.pdf", "b.pptx"])

    def test_embedded_image_resource(self):
        html = ('<div class="resourcecontent resourceimg">'
                '<img class="resourceimage" alt="Summary" '
                'src="https://learning.monash.edu/pluginfile.php/1/mod_resource/content/1/Summary.png"></div>')
        link = moodle_parse.extract_first_pluginfile(html, "https://learning.monash.edu")
        self.assertEqual(link["name"], "Summary")
        self.assertIn("Summary.png", link["url"])

    def test_sanitize_removes_active_content_and_tokens(self):
        dirty = ('<script>alert(1)</script><p onclick="x()">hi</p>'
                 '<a href="https://x/y?token=SECRET&a=1">l</a>'
                 '<a href="javascript:evil()">j</a>')
        clean = moodle_parse.sanitize_html(dirty)
        self.assertNotIn("script", clean.lower())
        self.assertNotIn("onclick", clean.lower())
        self.assertNotIn("SECRET", clean)
        self.assertNotIn("javascript:", clean.lower())


class PageContentTests(unittest.TestCase):
    def test_extract_page_content(self):
        html = '<div id="page-content"><p>Hello</p><table><tr><td>x</td></tr></table></div>'
        content = moodle_parse.extract_page_content(html)
        self.assertIn("Hello", content)
        self.assertIn("<table>", content)
        self.assertNotIn("page-content", content)

    def test_extract_page_content_missing(self):
        self.assertIsNone(moodle_parse.extract_page_content("<html><body>nothing</body></html>"))

    def test_edit_marker(self):
        self.assertTrue(moodle_parse.has_edit_marker("<span>Edited by Alice</span>"))
        self.assertFalse(moodle_parse.has_edit_marker("<p>plain</p>"))

    def test_parse_section_page_label_and_widget(self):
        html = """
        <ul class="section"><li class="activity activity-wrapper label modtype_label"
            id="module-15" data-id="15" data-activityname="Banner">
          <div class="activity-altcontent"><div class="no-overflow"><p>Hello label</p></div></div>
        </li></ul>
        <div class="assessmentsummary"><div class="assessment-structure-header">Assessment structure</div></div>
        """
        parsed = moodle_parse.parse_section_page(html)
        # assessment widget is NOT a section summary (course-level table)
        self.assertIsNone(parsed["summary_html"])
        self.assertTrue(parsed["assessment_widget_html"])
        self.assertIn("Assessment structure", parsed["assessment_widget_html"])
        self.assertEqual(parsed["labels"][0]["cmid"], 15)
        self.assertEqual(parsed["labels"][0]["name"], "Banner")
        self.assertIn("Hello label", parsed["labels"][0]["html"])

    def test_parse_section_page_standard_summary(self):
        html = '<div class="summary"><p>Section intro text</p></div>'
        parsed = moodle_parse.parse_section_page(html)
        self.assertEqual(parsed["summary_kind"], "standard")
        self.assertIn("Section intro text", parsed["summary_html"])


if __name__ == "__main__":
    unittest.main()
