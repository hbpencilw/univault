import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


class AgentDocsTests(unittest.TestCase):
    def test_universal_entry_points_exist(self):
        for name in ("AGENTS.md", "CLAUDE.md", "GEMINI.md", "CONVENTIONS.md",
                     ".cursor/rules/univault.mdc", "docs/AGENT_INTEGRATION.md"):
            with self.subTest(name=name):
                path = REPO / name
                self.assertTrue(path.is_file(), name)
                self.assertGreater(path.stat().st_size, 100, name)

    def test_agents_briefing_has_safety_rules(self):
        text = (REPO / "AGENTS.md").read_text()
        for needle in ("scan", "sync", "moodle-cookie", "read-only",
                       "explicit", "any agent"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_cursor_rule_is_always_applied(self):
        text = (REPO / ".cursor/rules/univault.mdc").read_text()
        self.assertIn("alwaysApply: true", text)
        self.assertIn("AGENTS.md", text)

    def test_readme_links_agents_file(self):
        text = (REPO / "README.md").read_text()
        self.assertIn("AGENTS.md", text)
        self.assertIn("docs/AGENT_INTEGRATION.md", text)


if __name__ == "__main__":
    unittest.main()
