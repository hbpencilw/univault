import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from lib.moodle_store import CourseStore  # noqa: E402

COURSE = {"code": "UNIT1001", "id": 1, "shortname": "UNIT1001_S2_2026"}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class StoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "moodle"
        self.store = CourseStore(str(self.dir), COURSE)

    def tearDown(self):
        self._tmp.cleanup()

    def commit(self, data: bytes, logical="cm:1", rel="resources/Section 01/Notes/a.pdf"):
        return self.store.commit_bytes(logical, "file", rel, data, {"title": "a.pdf"})

    def test_new_then_unchanged(self):
        out = self.commit(b"v1")
        self.assertEqual(out["status"], "new")
        self.assertTrue((self.dir / "resources/Section 01/Notes/a.pdf").exists())
        self.store.finalize({"new": 1})
        recs = [json.loads(line) for line in (self.dir / "moodle.jsonl").read_text().splitlines()]
        self.assertEqual(len(recs), 1)
        self.assertTrue(recs[0]["is_current"])
        self.assertEqual(recs[0]["content_hash"], "sha256:" + sha(b"v1"))

        store2 = CourseStore(str(self.dir), COURSE)
        out2 = store2.commit_bytes("cm:1", "file", "resources/Section 01/Notes/a.pdf", b"v1", {"title": "a.pdf"})
        self.assertEqual(out2["status"], "unchanged")

    def test_update_preserves_history(self):
        self.commit(b"v1")
        self.store.finalize({"new": 1})
        store2 = CourseStore(str(self.dir), COURSE)
        out = store2.commit_bytes("cm:1", "file", "resources/Section 01/Notes/a.pdf", b"v2", {"title": "a.pdf"})
        self.assertEqual(out["status"], "updated")
        hist = list((self.dir / "_history").rglob("*.pdf"))
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0].read_bytes(), b"v1")
        rec = store2.records["cm:1"]
        self.assertEqual(rec["version_id"], sha(b"v2"))
        self.assertEqual(rec["history"], [sha(b"v1")])
        self.assertEqual((self.dir / "resources/Section 01/Notes/a.pdf").read_bytes(), b"v2")

    def test_commit_file_moves_staging_into_place(self):
        staging_dir = Path(self.store.staging_dir)
        staging_dir.mkdir(parents=True, exist_ok=True)
        staging = staging_dir / "x.part"
        staging.write_bytes(b"filedata")
        out = self.store.commit_file("cm:2", "file", "resources/General/b.bin", str(staging), {"title": "b.bin"})
        self.assertEqual(out["status"], "new")
        self.assertFalse(staging.exists())
        self.assertEqual((self.dir / "resources/General/b.bin").read_bytes(), b"filedata")

    def test_recovery_restores_current_from_history(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "moodle.jsonl").write_text(json.dumps({
            "schema": 2, "platform": "moodle", "kind": "file", "course": COURSE,
            "id": "cm:9", "logical_id": "cm:9", "version_id": "abc123",
            "content_hash": "sha256:abc123", "relative_path": "resources/General/c.bin",
            "is_current": True, "history": [],
        }) + "\n")
        hist_dir = self.dir / "_history" / "cm_9"
        hist_dir.mkdir(parents=True)
        (hist_dir / "abc123.bin").write_bytes(b"restored")
        state = self.dir / "_state"
        state.mkdir(parents=True, exist_ok=True)
        (state / "journal.jsonl").write_text(json.dumps(
            {"op": "begin", "logical_id": "cm:9", "version": "def456"}) + "\n")

        CourseStore(str(self.dir), COURSE)  # recovery runs in __init__
        self.assertEqual((self.dir / "resources/General/c.bin").read_bytes(), b"restored")

    def test_recovery_adopts_completed_move(self):
        self.commit(b"v1")
        self.store.finalize({"new": 1})
        rel = "resources/Section 01/Notes/a.pdf"
        current = self.dir / rel
        sha1, sha2 = sha(b"v1"), sha(b"v2")
        # simulate: history copy done + current replaced, but JSONL not rewritten
        hist_dir = self.dir / "_history" / "cm_1"
        hist_dir.mkdir(parents=True, exist_ok=True)
        (hist_dir / f"{sha1}.pdf").write_bytes(b"v1")
        current.write_bytes(b"v2")
        state = self.dir / "_state"
        with open(state / "journal.jsonl", "a") as fh:
            fh.write(json.dumps({"op": "begin", "logical_id": "cm:1", "version": sha2, "path": rel}) + "\n")

        store2 = CourseStore(str(self.dir), COURSE)
        rec = store2.records["cm:1"]
        self.assertEqual(rec["version_id"], sha2)
        self.assertEqual(rec["content_hash"], "sha256:" + sha2)
        self.assertIn(sha1, rec["history"])

    def test_staging_dir_is_nested_under_state(self):
        self.assertEqual(Path(self.store.staging_dir).resolve(),
                         (self.dir / "_state" / "staging").resolve())
        self.assertTrue((self.dir / "_state" / "staging").is_dir())
        self.assertFalse((self.dir / "_state_staging").exists())

    def test_dry_run_writes_nothing(self):
        dry_dir = Path(self._tmp.name) / "dry"
        store = CourseStore(str(dry_dir), COURSE, dry_run=True)
        out = store.commit_bytes("cm:1", "file", "resources/General/a.bin", b"x", {})
        self.assertEqual(out["status"], "planned")
        self.assertFalse(dry_dir.exists())
        self.assertFalse((dry_dir / "moodle.jsonl").exists())

    def test_metadata_only_record(self):
        out = self.store.commit_metadata("cm:5", "label", {"title": "Banner", "archive_status": "metadata-only"})
        self.assertEqual(out["status"], "new")
        self.assertEqual(out["record"]["archive_status"], "metadata-only")
        self.assertIsNone(out["record"]["content_hash"])

    def test_metadata_only_is_idempotent(self):
        meta = {"title": "Banner", "archive_status": "metadata-only",
                "extra": {"cmid": 5, "reason": "stealth"}}
        first = self.store.commit_metadata("cm:5", "label", meta)
        self.assertEqual(first["status"], "new")
        captured = first["record"]["captured_at"]
        second = self.store.commit_metadata("cm:5", "label", meta)
        self.assertEqual(second["status"], "unchanged")
        self.assertEqual(second["record"]["captured_at"], captured)


if __name__ == "__main__":
    unittest.main()
