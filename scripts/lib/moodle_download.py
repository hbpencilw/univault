"""moodle_download — file download helpers for the Moodle sync engine.

All transport goes through HttpClient.download, which already:
- follows Moodle -> CDN redirects without ever sending the Cookie header to
  the external host, revalidating every hop,
- streams in 64 KiB chunks, checks Content-Type/Length and free disk space,
- writes a temp file, fsyncs and atomically renames.

This module adds the connector-level policy: only stable activity URLs are
persisted; signed CDN query strings never reach metadata, reports or logs.
"""
import hashlib
import os
import uuid

from .moodle_errors import ContentMismatch, FileWriteFailed, MoodleError


def staged_path(staging_dir: str, suffix: str = ".part") -> str:
    os.makedirs(staging_dir, exist_ok=True)
    return os.path.join(staging_dir, f"dl-{uuid.uuid4().hex}{suffix}")


def download_to_staging(client, url: str, staging_dir: str, *, max_bytes: int | None = None,
                        expected_size: int = 0) -> dict:
    """Download `url` into a staging file; return hash/size/type plus a discard fn."""
    dest = staged_path(staging_dir)
    try:
        result = client.download(url, dest, expected_size=expected_size, max_bytes=max_bytes)
    except MoodleError:
        _unlink(dest)
        raise
    return {
        "staging": dest,
        "sha256": result.sha256,
        "bytes": result.bytes_written,
        "content_type": result.content_type,
        "content_disposition": result.content_disposition,
        # NOTE: result.final_url may be a signed CDN URL — intentionally not kept.
    }


def hash_file(path: str) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def fsync_file(path: str) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def require_non_empty(path: str) -> int:
    size = os.path.getsize(path)
    if size <= 0:
        _unlink(path)
        raise ContentMismatch("downloaded file is empty")
    return size


def safe_extension(name: str, content_type: str = "", fallback: str = ".bin") -> str:
    ext = os.path.splitext(name or "")[1]
    if ext and len(ext) <= 12:
        return ext.lower()
    mapping = {
        "application/pdf": ".pdf", "text/html": ".html", "application/zip": ".zip",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "image/png": ".png", "image/jpeg": ".jpg", "video/mp4": ".mp4",
    }
    return mapping.get((content_type or "").split(";")[0].strip().lower(), fallback)


def move_into_place(src: str, dest: str) -> None:
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    try:
        os.replace(src, dest)
    except OSError as e:
        raise FileWriteFailed(f"could not commit download: {e.__class__.__name__}") from e
