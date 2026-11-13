import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ARCHIVE = REPO / "scripts" / "moodle-archive"

sys.path.insert(0, str(REPO / "scripts"))
from lib import moodle_auth  # noqa: E402


def load_archive_cli():
    loader = SourceFileLoader("moodle_archive_cli_under_test", str(ARCHIVE))
    spec = importlib.util.spec_from_loader("moodle_archive_cli_under_test", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class ArchiveCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        d = Path(self._tmp.name)
        self._saved = (moodle_auth.CONFIG_DIR, moodle_auth.COOKIE_FILE, moodle_auth.CAPABILITIES_FILE)
        moodle_auth.CONFIG_DIR = d
        moodle_auth.COOKIE_FILE = d / "moodle-cookie"
        moodle_auth.CAPABILITIES_FILE = d / "capabilities.json"
        self.dest = d / "scan"
        self.cli = load_archive_cli()

    def tearDown(self):
        moodle_auth.CONFIG_DIR, moodle_auth.COOKIE_FILE, moodle_auth.CAPABILITIES_FILE = self._saved
        self._tmp.cleanup()

    def invoke(self, *argv):
        old = sys.argv
        out, err = io.StringIO(), io.StringIO()
        sys.argv = ["moodle-archive", *argv]
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    self.cli.main()
                    code = 0
                except SystemExit as e:
                    code = e.code if isinstance(e.code, int) else 0
        finally:
            sys.argv = old
        return code, out.getvalue(), err.getvalue()

    def test_scan_without_cookie_exits_1(self):
        code, _out, err = self.invoke("scan", "--dest", str(self.dest))
        self.assertEqual(code, 1)
        self.assertIn("no stored Moodle cookie", err)

    def test_sync_missing_manifest_exits_1(self):
        code, _out, err = self.invoke("sync", "--from-scan", str(self.dest / "none.json"))
        self.assertEqual(code, 1)
        self.assertIn("scan manifest not found", err)

    def test_sync_dry_run_without_cookie(self):
        manifest = self.dest / "manifest.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text('{"host": "learning.monash.edu", "courses": []}')
        code, out, err = self.invoke("sync", "--from-scan", str(manifest), "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("dry-run", out)

    def test_auth_failure_prints_recovery_hint(self):
        from lib.moodle_errors import AuthExpired
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = self.cli._fail(AuthExpired("redirected to single sign-on"))
        self.assertEqual(code, 2)
        self.assertIn("import-cookie --from-clipboard", err.getvalue())

    def test_help_lists_subcommands(self):
        old = sys.argv
        sys.argv = ["moodle-archive", "--help"]
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                with self.assertRaises(SystemExit):
                    self.cli.main()
        finally:
            sys.argv = old
        for cmd in ("scan", "status", "report", "sync"):
            self.assertIn(cmd, out.getvalue())


if __name__ == "__main__":
    unittest.main()
