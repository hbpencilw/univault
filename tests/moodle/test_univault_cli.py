import argparse
import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "scripts" / "univault"


def load_cli():
    loader = SourceFileLoader("univault_cli_under_test", str(CLI))
    spec = importlib.util.spec_from_loader("univault_cli_under_test", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class UnivaultCliTests(unittest.TestCase):
    def setUp(self):
        self.cli = load_cli()

    def test_help_lists_commands(self):
        old = sys.argv
        sys.argv = ["univault", "--help"]
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                with self.assertRaises(SystemExit) as exc:
                    self.cli.main()
            self.assertEqual(exc.exception.code, 0)
        finally:
            sys.argv = old
        for cmd in ("login", "status", "sync"):
            self.assertIn(cmd, out.getvalue())

    def test_ed_archives_discovers_existing_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "2026_s2" / "unit1001" / "memory" / "resources" / "ed"
            target.mkdir(parents=True)
            old_root, old_units = self.cli.LEARNING_ROOT, self.cli.ED_UNITS
            self.cli.LEARNING_ROOT = root
            self.cli.ED_UNITS = {"UNIT1001": 10001}
            try:
                found = self.cli.ed_archives()
            finally:
                self.cli.LEARNING_ROOT, self.cli.ED_UNITS = old_root, old_units
            self.assertEqual([code for code, _ in found], ["UNIT1001"])

    def test_manifest_course_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "scan-latest.json"
            manifest.write_text('{"courses": [{"id": 10001, "code": "UNIT1001"}]}')
            old = self.cli.MANIFEST
            self.cli.MANIFEST = manifest
            try:
                self.assertEqual(self.cli._manifest_course("unit1001")["id"], 10001)
                self.assertIsNone(self.cli._manifest_course("UNIT9999"))
            finally:
                self.cli.MANIFEST = old

    def test_dry_run_skips_ed_downloads(self):
        calls = []
        old_run, old_archives = self.cli._run, self.cli.ed_archives
        self.cli._run = lambda cmd: calls.append(cmd) or 0
        self.cli.ed_archives = lambda: [("UNIT1001", Path("/tmp/unit1001/ed"))]
        args = argparse.Namespace(
            no_ed=False, course=None, no_lessons=False, no_moodle=True,
            dry_run=True, rescan=False, login_timeout=1,
        )
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                rc = self.cli.cmd_sync(args)
        finally:
            self.cli._run, self.cli.ed_archives = old_run, old_archives
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])
        self.assertIn("dry-run: skipped", out.getvalue())


if __name__ == "__main__":
    unittest.main()
