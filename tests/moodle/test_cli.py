import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SETUP = REPO / "scripts" / "moodle-setup"

sys.path.insert(0, str(REPO / "scripts"))
from lib import moodle_auth  # noqa: E402


def load_cli():
    loader = SourceFileLoader("moodle_setup_cli_under_test", str(SETUP))
    spec = importlib.util.spec_from_loader("moodle_setup_cli_under_test", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class CliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        d = Path(self._tmp.name)
        self._saved = (moodle_auth.CONFIG_DIR, moodle_auth.COOKIE_FILE, moodle_auth.CAPABILITIES_FILE)
        moodle_auth.CONFIG_DIR = d
        moodle_auth.COOKIE_FILE = d / "moodle-cookie"
        moodle_auth.CAPABILITIES_FILE = d / "capabilities.json"
        self.cli = load_cli()

    def tearDown(self):
        moodle_auth.CONFIG_DIR, moodle_auth.COOKIE_FILE, moodle_auth.CAPABILITIES_FILE = self._saved
        self._tmp.cleanup()

    def invoke(self, *argv):
        """Call the CLI in-process (no subprocess, no shell)."""
        old_argv = sys.argv
        out, err = io.StringIO(), io.StringIO()
        sys.argv = ["moodle-setup", *argv]
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    self.cli.main()
                    code = 0
                except SystemExit as e:
                    code = e.code if isinstance(e.code, int) else 0
        finally:
            sys.argv = old_argv
        return code, out.getvalue(), err.getvalue()

    def test_status_without_cookie_exits_1(self):
        code, _out, err = self.invoke("status")
        self.assertEqual(code, 1)
        self.assertIn("no stored Moodle cookie", err)

    def test_clear_with_nothing_is_ok(self):
        code, out, _err = self.invoke("clear", "--yes")
        self.assertEqual(code, 0)
        self.assertIn("nothing to clear", out)

    def test_import_cookie_rejects_malformed_file(self):
        bad = moodle_auth.CONFIG_DIR / "bad.txt"
        bad.write_text("novalue")
        code, _out, err = self.invoke("import-cookie", "--from-file", str(bad))
        self.assertEqual(code, 1)
        self.assertIn("invalid cookie header", err)

    def test_help_lists_subcommands(self):
        # argparse exits 0 on --help
        old_argv = sys.argv
        sys.argv = ["moodle-setup", "--help"]
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                with self.assertRaises(SystemExit) as exc:
                    self.cli.main()
            self.assertEqual(exc.exception.code, 0)
        finally:
            sys.argv = old_argv
        for cmd in ("import-cookie", "login", "status", "probe", "clear"):
            self.assertIn(cmd, out.getvalue())


if __name__ == "__main__":
    unittest.main()
