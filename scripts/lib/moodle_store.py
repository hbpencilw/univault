"""moodle_store — versioned, crash-recoverable storage for one Moodle course.

Layout (under the course's memory/resources/moodle directory):
  moodle.jsonl                 current records only (true source for normalize)
  course.json                  course metadata + last sync stats
  resources/<section>/<act>/   current files
  pages/<cmid>-<slug>.html     current page HTML
  announcements/<did>/...      announcement posts + attachments
  _history/<logical_id>/<sha>.<ext>   permanent old versions
  _state/journal.jsonl         commit intents/completions (crash recovery)
  _state/staging/              download staging (always cleared on start)
  _reports/sync-<ts>.json|md   per-run reports

Invariants:
- a new version is fully staged/hashed before the current file is replaced;
- the old current file is copied into _history before replacement;
- journal begin/done markers let the next run detect and repair a crash;
- signed CDN URLs are never stored (callers pass stable activity URLs only).
"""
import hashlib
import json
import os
import time
import uuid

from . import safe_fs

SCHEMA = 2


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def sha256_file(path: str) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _logical_dirname(logical_id: str) -> str:
    return safe_fs.sanitize_component(logical_id.replace(":", "_"), fallback="item")


class CourseStore:
    def __init__(self, course_dir: str, course: dict, *, dry_run: bool = False):
        self.dir = os.path.realpath(os.path.expanduser(course_dir))
        self.course = course
        self.dry_run = dry_run
        self.records: dict[str, dict] = {}
        self.journal_name = "journal.jsonl"
        self._dirs = {}
        if not dry_run:
            os.makedirs(self.dir, exist_ok=True)
            for name in ("announcements", "resources", "pages",
                         "_history", "_state", "_reports"):
                self._dirs[name] = safe_fs.ensure_dir(self.dir, name)
            self._dirs["_state/staging"] = safe_fs.ensure_dir(self.dir, "_state", "staging")
            self._load_records()
            self._recover()
        else:
            self._dirs = {"_state/staging": os.path.join(self.dir, "_state", "staging")}

    # ------------------------------------------------------------- internals
    @property
    def staging_dir(self) -> str:
        return self._dirs.get("_state/staging") or os.path.join(self.dir, "_state", "staging")

    def _state_dir(self) -> str:
        return safe_fs.ensure_dir(self.dir, "_state")

    def _journal_append(self, entry: dict) -> None:
        if self.dry_run:
            return
        state = self._state_dir()
        data = (json.dumps(entry, ensure_ascii=False) + "\n").encode()
        dir_fd = os.open(state, os.O_RDONLY)
        try:
            fd = os.open(self.journal_name, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644, dir_fd=dir_fd)
            try:
                os.write(fd, data)
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            os.close(dir_fd)

    def _load_records(self) -> None:
        path = os.path.join(self.dir, "moodle.jsonl")
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("is_current", True) and rec.get("logical_id"):
                    self.records[rec["logical_id"]] = rec

    def _recover(self) -> None:
        """Repair an interrupted commit: restore current from history if needed."""
        safe_fs.clear_staging(self.staging_dir)
        journal_path = os.path.join(self._state_dir(), self.journal_name)
        if not os.path.exists(journal_path):
            return
        begins: list[dict] = []
        done: set[tuple] = set()
        with open(journal_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("op") == "begin":
                    begins.append(entry)
                elif entry.get("op") in ("done", "recovered-commit"):
                    done.add((entry.get("logical_id"), entry.get("version")))
        for entry in begins:
            logical_id = entry.get("logical_id")
            if not logical_id:
                continue
            if (logical_id, entry.get("version")) in done:
                continue
            rec = self.records.get(logical_id)
            if not rec:
                continue
            rel = rec.get("relative_path")
            if not rel:
                continue
            parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
            if not parts:
                continue
            dest_dir = safe_fs.ensure_dir(self.dir, *parts[:-1])
            dest = os.path.join(dest_dir, parts[-1])
            version = entry.get("version") or ""
            if os.path.exists(dest) and version and sha256_file(dest) == version:
                # the move completed but the JSONL projection did not: adopt it
                if rec:
                    old = rec.get("version_id")
                    rec["version_id"] = version
                    rec["content_hash"] = f"sha256:{version}"
                    rec["relative_path"] = rel
                    rec.setdefault("archive_status", "archived")
                    if old and old != version:
                        hist_old = self._history_path(logical_id, old)
                        if os.path.exists(hist_old):
                            history = list(rec.get("history") or [])
                            if old not in history:
                                history.append(old)
                            rec["history"] = history
                    self._journal_append({"op": "recovered-commit", "logical_id": logical_id,
                                          "version": version, "ts": _now()})
                continue
            hist = self._history_path(logical_id, (rec.get("version_id") or ""))
            if not os.path.exists(dest) and os.path.exists(hist):
                safe_fs.copy_file_into(hist, dest_dir, parts[-1])
                self._journal_append({"op": "recovered", "logical_id": logical_id,
                                      "version": rec.get("version_id"), "ts": _now()})

    def _dest_parts(self, rel_path: str) -> tuple[str, str, str]:
        rel = (rel_path or "").replace("\\", "/").strip("/")
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if not parts or any(p == ".." for p in parts):
            raise ValueError(f"invalid relative path: {rel_path!r}")
        directory = safe_fs.ensure_dir(self.dir, *parts[:-1])
        return directory, parts[-1], rel

    def _history_path(self, logical_id: str, sha: str) -> str:
        ext = ""
        rec = self.records.get(logical_id) or {}
        rel = rec.get("relative_path") or ""
        ext = os.path.splitext(rel)[1]
        name = f"{sha}{ext}" if sha else f"{uuid.uuid4().hex}{ext}"
        directory = safe_fs.ensure_dir(self.dir, "_history", _logical_dirname(logical_id))
        return os.path.join(directory, safe_fs.sanitize_component(name, fallback=f"v{ext or ''}"))

    def _record(self, logical_id: str, kind: str, meta: dict, *, sha: str | None,
                rel_path: str | None, status: str) -> dict:
        prior = self.records.get(logical_id) or {}
        return {
            "schema": SCHEMA,
            "platform": "moodle",
            "kind": kind,
            "course": {"code": self.course.get("code"), "id": self.course.get("id")},
            "id": logical_id,
            "logical_id": logical_id,
            "title": meta.get("title"),
            "category": meta.get("category"),
            "created_at": meta.get("created_at"),
            "updated_at": meta.get("updated_at"),
            "url": meta.get("url"),
            "author": meta.get("author") or {"name": None, "staff": None},
            "flags": meta.get("flags") or {"answered": None, "staff_answered": None,
                                           "anonymous": None, "pinned": None, "locked": None,
                                           "private": None, "endorsed": None},
            "counts": meta.get("counts") or {"replies": None, "views": None, "votes": None},
            "body_md": meta.get("body_md"),
            "content_hash": f"sha256:{sha}" if sha else None,
            "version_id": sha,
            "is_current": True,
            "source_modified_at": meta.get("source_modified_at"),
            "captured_at": _now(),
            "archive_status": meta.get("archive_status") or ("archived" if sha else "metadata-only"),
            "relative_path": rel_path,
            "extra": meta.get("extra") or {},
            "history": list(prior.get("history") or []),
        }

    def _commit_staged(self, logical_id: str, kind: str, rel_path: str, staging: str,
                       sha: str, byte_size: int, meta: dict) -> dict:
        dest_dir, dest_name, rel = self._dest_parts(rel_path)
        prior = self.records.get(logical_id)
        old_sha = (prior or {}).get("version_id")
        old_rel = (prior or {}).get("relative_path")
        old_path = os.path.join(self.dir, old_rel) if old_rel else None

        # preserve the previous current version before replacing it
        if prior and old_sha and old_path and os.path.exists(old_path):
            hist = self._history_path(logical_id, old_sha)
            if not os.path.exists(hist):
                safe_fs.copy_file_into(old_path, os.path.dirname(hist), os.path.basename(hist))

        self._journal_append({"op": "begin", "logical_id": logical_id, "version": sha,
                              "path": rel, "ts": _now()})
        staging_dir, staging_name = os.path.dirname(staging), os.path.basename(staging)
        safe_fs.move_between(staging_dir, staging_name, dest_dir, dest_name)

        record = self._record(logical_id, kind, meta, sha=sha, rel_path=rel, status="archived")
        if prior and old_sha and old_sha != sha:
            record["history"] = list(prior.get("history") or []) + [old_sha]
        record["extra"] = {**record["extra"], "bytes": byte_size}
        self.records[logical_id] = record
        self._journal_append({"op": "done", "logical_id": logical_id, "version": sha, "ts": _now()})
        return {"status": "updated" if prior else "new", "record": record}

    # --------------------------------------------------------------- API
    def commit_bytes(self, logical_id: str, kind: str, rel_path: str, data: bytes, meta: dict) -> dict:
        sha = hashlib.sha256(data).hexdigest()
        prior = self.records.get(logical_id)
        if self.dry_run:
            return {"status": "planned", "record": self._record(logical_id, kind, meta, sha=sha,
                                                                 rel_path=rel_path, status="planned")}
        dest_dir, dest_name, rel = self._dest_parts(rel_path)
        current = os.path.join(dest_dir, dest_name)
        if prior and prior.get("version_id") == sha and os.path.exists(current):
            return {"status": "unchanged", "record": prior}
        staging_dir = safe_fs.ensure_dir(self._state_dir(), "staging")
        staging_name = f"st-{uuid.uuid4().hex}.part"
        safe_fs.write_bytes(staging_dir, staging_name, data)
        return self._commit_staged(logical_id, kind, rel_path,
                                   os.path.join(staging_dir, staging_name), sha, len(data), meta)

    def commit_file(self, logical_id: str, kind: str, rel_path: str, staging_path: str, meta: dict) -> dict:
        sha = sha256_file(staging_path)
        size = os.path.getsize(staging_path)
        prior = self.records.get(logical_id)
        if self.dry_run:
            safe_fs.remove_quietly(os.path.dirname(staging_path), os.path.basename(staging_path))
            return {"status": "planned", "record": self._record(logical_id, kind, meta, sha=sha,
                                                                 rel_path=rel_path, status="planned")}
        dest_dir, dest_name, rel = self._dest_parts(rel_path)
        current = os.path.join(dest_dir, dest_name)
        if prior and prior.get("version_id") == sha and os.path.exists(current):
            safe_fs.remove_quietly(os.path.dirname(staging_path), os.path.basename(staging_path))
            return {"status": "unchanged", "record": prior}
        return self._commit_staged(logical_id, kind, rel_path, staging_path, sha, size, meta)

    def commit_metadata(self, logical_id: str, kind: str, meta: dict) -> dict:
        prior = self.records.get(logical_id)
        if prior and self._metadata_equal(prior, kind, meta):
            return {"status": "unchanged", "record": prior}
        if self.dry_run:
            return {"status": "planned", "record": prior or {}}
        status = "updated" if prior else "new"
        record = self._record(logical_id, kind, meta, sha=None, rel_path=None, status="metadata-only")
        self.records[logical_id] = record
        return {"status": status, "record": record}

    @staticmethod
    def _metadata_equal(prior: dict, kind: str, meta: dict) -> bool:
        """Metadata-only records are stable: same fields => unchanged record."""
        if prior.get("kind") != kind:
            return False
        for key in ("title", "category", "url", "archive_status"):
            if prior.get(key) != meta.get(key):
                return False
        return (prior.get("extra") or {}) == (meta.get("extra") or {})

    def write_report(self, name: str, text: str) -> str:
        if self.dry_run:
            return ""
        report_dir = safe_fs.ensure_dir(self.dir, "_reports")
        return safe_fs.write_bytes(report_dir, safe_fs.sanitize_component(name), text.encode("utf-8"))

    def finalize(self, summary: dict) -> None:
        if self.dry_run:
            return
        payload = "".join(
            json.dumps(rec, ensure_ascii=False) + "\n"
            for rec in sorted(self.records.values(), key=lambda r: (r.get("kind") or "", r.get("logical_id") or ""))
        )
        safe_fs.write_bytes(self.dir, "moodle.jsonl", payload.encode("utf-8"))
        course_meta = {
            "course": self.course.get("course") or self.course,
            "schema": SCHEMA,
            "last_sync": _now(),
            "stats": summary,
        }
        safe_fs.write_bytes(self.dir, "course.json", json.dumps(course_meta, indent=2, ensure_ascii=False).encode())
        state = safe_fs.ensure_dir(self.dir, "_state")
        safe_fs.write_bytes(state, "sync-latest.json", json.dumps(summary, indent=2, ensure_ascii=False).encode())
