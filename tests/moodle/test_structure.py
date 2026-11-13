import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from lib import moodle_structure  # noqa: E402

STATE = {
    "section": [
        {"id": 1, "number": 0, "title": None, "rawtitle": None, "cmlist": [10, 11], "parent": None},
        {"id": 2, "number": 1, "title": "Week 1", "rawtitle": "Week 1", "cmlist": [12, 13], "parent": None},
    ],
    "cm": [
        {"id": 10, "modname": "Forum", "name": "Announcements", "url": "https://learning.monash.edu/mod/forum/view.php?id=10", "uservisible": True},
        {"id": 11, "modname": "CMS", "name": "Welcome Section", "url": None, "uservisible": True},
        {"id": 12, "modname": "File", "name": "Notes", "url": "https://learning.monash.edu/mod/resource/view.php?id=12", "uservisible": True},
        {"id": 13, "modname": "Text and media area", "name": "Banner", "url": None, "uservisible": True},
    ],
}


class StructureTests(unittest.TestCase):
    def test_modname_normalisation(self):
        cases = {
            "File": "resource", "Folder": "folder", "Page": "page", "Book": "book",
            "URL": "url", "Forum": "forum", "Quiz": "quiz", "Assignment": "assign",
            "External tool": "lti", "Text and media area": "label", "CMS": "cms",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(moodle_structure.normalize_modname(raw), expected)

    def test_build_structure_maps_sections_and_finds_announcements(self):
        st = moodle_structure.build_structure(json.loads(json.dumps(STATE)))
        self.assertEqual(len(st["sections"]), 2)
        self.assertEqual(st["cms"][12]["type"], "resource")
        self.assertEqual(st["cms"][12]["section"], 1)
        self.assertEqual(st["cms"][13]["type"], "label")
        self.assertEqual(st["announcements_forum"]["id"], 10)

    def test_double_encoded_state_is_parsed(self):
        st = moodle_structure.build_structure(
            moodle_structure.parse_state(json.dumps(json.dumps(STATE)))
        )
        self.assertEqual(st["announcements_forum"]["name"], "Announcements")

    def test_falls_back_to_first_forum(self):
        state = {
            "section": [{"id": 1, "number": 0, "cmlist": [1], "title": None}],
            "cm": [{"id": 1, "modname": "Forum", "name": "Unit forum", "url": "u", "uservisible": True}],
        }
        st = moodle_structure.build_structure(state)
        self.assertEqual(st["announcements_forum"]["id"], 1)


if __name__ == "__main__":
    unittest.main()
