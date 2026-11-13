"""safe_fs — path-confined filesystem primitives for the Moodle archive.

Every write/move/rename is performed relative to an already-open directory
file descriptor with a validated bare filename, so no path component can
escape its base directory. Names are restricted to a conservative charset.
"""
import os
import re
import shutil

_BAD = re.compile(r"[\\/\x00-\x1f]")
_NAME_RE = re.compile(r"[^\\/\x00-\x1f]{1,180}")


def sanitize_component(name: str, fallback: str = "unnamed") -> str:
    name = (name or "").strip()
    name = _BAD.sub("_", name)
    name = name.strip(" .")
    if not name or name in (".", ".."):
        name = fallback
    return name[:180]


def ensure_dir(base: str, *parts: str) -> str:
    base_real = os.path.realpath(base)
    current = base_real
    for part in parts:
        comp = sanitize_component(part)
        current = os.path.join(current, comp)
        os.makedirs(current, exist_ok=True)
    real = os.path.realpath(current)
    if os.path.commonpath([base_real, real]) != base_real:
        raise ValueError("directory escapes base")
    return real


def _check_name(name: str) -> str:
    if not _NAME_RE.fullmatch(name or "") or name in (".", ".."):
        raise ValueError(f"invalid filename: {name!r}")
    return name


def write_bytes(directory: str, name: str, data: bytes) -> str:
    """Write `name` inside `directory` (dir_fd + bare name, atomic replace)."""
    _check_name(name)
    dir_fd = os.open(directory, os.O_RDONLY)
    tmp_name = f".{name}.{os.getpid()}.tmp"
    try:
        fd = os.open(tmp_name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644, dir_fd=dir_fd)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp_name, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    finally:
        os.close(dir_fd)
    return os.path.join(os.path.realpath(directory), name)


def copy_into(src_path: str, directory: str, name: str) -> str:
    """Stream an existing file into directory/name via dir_fd (fsync + rename)."""
    _check_name(name)
    dir_fd = os.open(directory, os.O_RDONLY)
    tmp_name = f".{name}.{os.getpid()}.copy"
    try:
        fd = os.open(tmp_name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644, dir_fd=dir_fd)
        try:
            with open(src_path, "rb") as source:
                while True:
                    chunk = source.read(1 << 16)
                    if not chunk:
                        break
                    os.write(fd, chunk)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp_name, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    finally:
        os.close(dir_fd)
    return os.path.join(os.path.realpath(directory), name)


def move_between(src_dir: str, src_name: str, dst_dir: str, dst_name: str) -> str:
    """Atomic rename between two directories, both addressed by dir_fd."""
    _check_name(src_name)
    _check_name(dst_name)
    src_fd = os.open(src_dir, os.O_RDONLY)
    dst_fd = os.open(dst_dir, os.O_RDONLY)
    try:
        os.replace(src_name, dst_name, src_dir_fd=src_fd, dst_dir_fd=dst_fd)
    finally:
        os.close(src_fd)
        os.close(dst_fd)
    return os.path.join(os.path.realpath(dst_dir), dst_name)


def remove_quietly(directory: str, name: str) -> None:
    _check_name(name)
    try:
        os.unlink(name, dir_fd=os.open(directory, os.O_RDONLY))
    except FileNotFoundError:
        pass


def copy_file_into(src_path: str, directory: str, name: str) -> str:
    """Fallback copy used by recovery when only a path-based read is available."""
    return copy_into(src_path, directory, name)


def clear_staging(staging_dir: str) -> int:
    removed = 0
    try:
        for entry in os.listdir(staging_dir):
            path = os.path.join(staging_dir, entry)
            if os.path.isfile(path):
                os.unlink(path)
                removed += 1
    except FileNotFoundError:
        pass
    return removed
