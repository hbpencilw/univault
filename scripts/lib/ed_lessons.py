"""ed_lessons — archive Ed Discussion Lessons (content slides, quiz prompts, PDFs).

Read-only. Uses the personal Ed API token (same as ed-archive):
  GET /api/courses/{cid}/lessons
  GET /api/lessons/{lid}
PDF slides carry a public URL on static.edusercontent.com (no auth); all other
content stays on the API host. No attempt/answer data is fetched.

Outputs into DEST (the course's memory/resources/ed directory):
  lessons.jsonl     one record per lesson (resumable, existing ids skipped)
  ed_lessons.md     human-readable compilation
  lesson_files/<NN>-<slug>/<SS>-<slug>.pdf
"""
import hashlib
import html as html_mod
import http.client
import ipaddress
import json
import os
import re
import socket
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

API_HOST = "edstem.org"
FILE_HOST_SUFFIX = ".edusercontent.com"
TOKEN_PATH = os.path.expanduser("~/.config/edstem-cli/token")
UA = "univault-ed-lessons/0.1"


# ------------------------------------------------------------------ helpers
def load_token() -> str:
    tok = os.environ.get("EDSTEM_TOKEN", "").strip()
    if tok:
        return tok
    if os.path.exists(TOKEN_PATH):
        tok = Path(TOKEN_PATH).read_text().strip()
        if tok:
            return tok
    raise SystemExit("no Ed token: run ed-setup first (or set EDSTEM_TOKEN)")


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slug(text: str, maxlen: int = 60) -> str:
    text = re.sub(r"[^\w\-. ]+", "", text or "").strip().replace(" ", "_")
    text = re.sub(r"_+", "_", text)
    return (text or "item")[:maxlen]


def _host_public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0].split("%")[0])
        except ValueError:
            return False
        if not ip.is_global:
            return False
    return bool(infos)


def validate_file_url(url: str, *, dns_check: bool = True) -> urllib.parse.SplitResult:
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https":
        raise ValueError(f"unsupported scheme: {p.scheme}")
    host = (p.hostname or "").lower()
    if not (host == "edusercontent.com" or host.endswith(FILE_HOST_SUFFIX)):
        raise ValueError(f"host not allowed: {host}")
    if dns_check and not _host_public(host):
        raise ValueError(f"host does not resolve to a public address: {host}")
    return p


def _api_get(path: str, token: str, timeout: int = 45):
    conn = http.client.HTTPSConnection(API_HOST, timeout=timeout)
    try:
        conn.request("GET", path, headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            "User-Agent": UA,
        })
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} on {path}: {body[:200]!r}")
        return json.loads(body)
    finally:
        conn.close()


# ------------------------------------------------------------------ xml -> md
_INLINE = {"bold": "**", "italic": "*", "code": "`"}


def _node_text(node) -> str:
    """Render an Ed <document> node to Markdown."""
    tag = node.tag.split("}")[-1]
    inner = (node.text or "")
    for child in node:
        inner += _node_text(child) + (child.tail or "")
    inner = inner.strip()

    if tag == "document":
        return inner
    if tag == "heading":
        level = min(6, max(1, int(node.attrib.get("level", "1"))))
        return f"{'#' * level} {inner}\n\n"
    if tag == "paragraph":
        return inner + "\n\n"
    if tag == "list":
        ordered = (node.attrib.get("style") or "bullet") not in ("bullet", "unordered")
        items = []
        n = 0
        for item in node:
            if item.tag.split("}")[-1] != "list-item":
                continue
            n += 1
            prefix = f"{n}. " if ordered else "- "
            text = _node_text(item).strip().replace("\n\n", "\n")
            items.append(prefix + text)
        return "\n".join(items) + "\n\n"
    if tag == "list-item":
        return inner
    if tag in _INLINE:
        mark = _INLINE[tag]
        return f"{mark}{inner}{mark}"
    if tag == "math":
        return f"${inner}$"
    if tag == "break":
        return "\n"
    if tag == "link":
        href = node.attrib.get("href", "")
        return f"[{inner or href}]({href})" if href else inner
    if tag in ("image", "figure"):
        src = node.attrib.get("src", "")
        return f"![{node.attrib.get('alt', 'image')}]({src})" if src else inner
    if tag == "web-snippet":
        return inner
    return inner


def xml_to_markdown(xml: str) -> str:
    """Convert an Ed <document> payload to readable Markdown (regex fallback)."""
    if not xml:
        return ""
    try:
        root = ElementTree.fromstring(xml)
        text = _node_text(root)
    except ElementTree.ParseError:
        text = re.sub(r"<break\s*/?>", "\n", xml)
        text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return html_mod.unescape(text).strip()


# ------------------------------------------------------------------ storage
def safe_dest(dest: str) -> str:
    raw = os.path.expanduser(dest or "")
    if not raw:
        raise ValueError("empty destination")
    if ".." in Path(raw).parts:
        raise ValueError("destination must not contain '..'")
    return os.path.realpath(raw)


def _write_at(directory: str, name: str, data: bytes) -> str:
    from lib import safe_fs
    return safe_fs.write_bytes(directory, name, data)


def download_pdf(url: str, directory: str, name: str, *, timeout: int = 90) -> dict:
    """Download a PDF slide from static.edusercontent.com (no auth header)."""
    p = validate_file_url(url)
    from lib import safe_fs
    os.makedirs(directory, exist_ok=True)
    safe_name = safe_fs.sanitize_component(name, fallback="slide.pdf")
    dest = os.path.join(directory, safe_name)
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return {"path": dest, "skipped": True}

    host = p.hostname
    target = p.path + (("?" + p.query) if p.query else "")
    conn = http.client.HTTPSConnection(host, timeout=timeout)
    try:
        conn.request("GET", target, headers={"User-Agent": UA})
        resp = conn.getresponse()
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} for PDF")
        ctype = (resp.getheader("content-type") or "").split(";")[0].lower()
        if ctype not in ("application/pdf", "application/octet-stream"):
            raise RuntimeError(f"unexpected content-type: {ctype}")
        dir_fd = os.open(directory, os.O_RDONLY)
        tmp = f".{safe_name}.{os.getpid()}.part"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644, dir_fd=dir_fd)
            written, hasher = 0, hashlib.sha256()
            try:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    hasher.update(chunk)
                    written += len(chunk)
                    os.write(fd, chunk)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, safe_name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        finally:
            os.close(dir_fd)
        return {"path": os.path.join(directory, safe_name), "bytes": written,
                "sha256": hasher.hexdigest(), "skipped": False}
    finally:
        conn.close()


# ------------------------------------------------------------------ archive
def load_seen(path: str) -> set:
    seen = set()
    if os.path.exists(path):
        for line in Path(path).read_text(errors="replace").splitlines():
            try:
                seen.add(int(json.loads(line).get("id")))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
    return seen


def lesson_record(lesson: dict, *, download_files: bool, dest: str, log=print) -> dict:
    lid = int(lesson["id"])
    index = lesson.get("index")
    slides = []
    pdf_dir = os.path.join(dest, "lesson_files", f"{int(index):03d}-{slug(lesson.get('title') or 'lesson')}"
                            if index is not None else f"{lid}-{slug(lesson.get('title') or 'lesson')}")
    for slide in lesson.get("slides") or []:
        stype = slide.get("type")
        entry = {
            "slide_id": slide.get("id"),
            "index": slide.get("index"),
            "type": stype,
            "title": slide.get("title"),
        }
        if stype == "document":
            entry["text_md"] = xml_to_markdown(slide.get("content") or "")
        elif stype == "quiz":
            entry["text_md"] = xml_to_markdown(slide.get("passage") or "")
            entry["quiz"] = True
        elif stype == "pdf":
            url = slide.get("file_url")
            entry["text_md"] = ""
            if url and download_files:
                name = f"{int(slide.get('index') or 0):02d}-{slug(slide.get('title') or 'slide')}.pdf"
                try:
                    info = download_pdf(url, pdf_dir, name)
                    entry["pdf"] = {"path": os.path.relpath(info["path"], dest),
                                    "bytes": info.get("bytes"), "sha256": info.get("sha256"),
                                    "skipped": info.get("skipped")}
                except Exception as exc:  # keep the lesson, record the failure
                    entry["pdf"] = {"error": f"{type(exc).__name__}: {exc}"}
                    log(f"    [pdf fail] {slide.get('title')}: {exc}")
            elif url:
                entry["pdf"] = {"url": url}
        else:
            entry["text_md"] = xml_to_markdown(slide.get("content") or "")
        slides.append(entry)
    return {
        "schema": 1,
        "platform": "ed",
        "kind": "lesson",
        "course_id": lesson.get("course_id"),
        "id": lid,
        "index": index,
        "title": lesson.get("title"),
        "lesson_kind": lesson.get("kind"),
        "slides": slides,
        "captured_at": iso_now(),
    }


def render_markdown(records: list[dict], course_id: int) -> str:
    lines = [f"# Ed Lessons — course {course_id}", "",
             f"> archived {datetime.now().strftime('%Y-%m-%d %H:%M')} · {len(records)} lessons", ""]
    for rec in sorted(records, key=lambda r: r.get("index") or 0):
        lines.append(f"## {rec.get('index')}. {rec.get('title')}")
        lines.append("")
        for slide in rec.get("slides") or []:
            lines.append(f"### {slide.get('index')}. {slide.get('title') or ''}".rstrip())
            lines.append("")
            text = (slide.get("text_md") or "").strip()
            if text:
                lines.append(text)
                lines.append("")
            pdf = slide.get("pdf") or {}
            if pdf.get("path"):
                lines.append(f"[PDF]({pdf['path']})")
                lines.append("")
            elif pdf.get("url"):
                lines.append(f"[PDF]({pdf['url']})")
                lines.append("")
    return "\n".join(lines)


def archive(course_id: int, dest: str, *, token: str | None = None, delay: float = 1.0,
            download_files: bool = True, list_only: bool = False, log=print) -> dict:
    token = token or load_token()
    dest = safe_dest(dest)
    os.makedirs(dest, exist_ok=True)
    lessons_path = os.path.join(dest, "lessons.jsonl")

    data = _api_get(f"/api/courses/{course_id}/lessons", token)
    items = data.get("lessons") if isinstance(data, dict) else data
    items = items or []
    if list_only:
        return {"lessons": len(items), "list_only": True}

    seen = load_seen(lessons_path)
    stats = {"lessons": len(items), "new": 0, "skipped": 0, "pdfs": 0, "errors": 0}
    records = []
    for pos, item in enumerate(items, 1):
        lid = item.get("id")
        if lid is None:
            continue
        if int(lid) in seen:
            stats["skipped"] += 1
            continue
        detail = _api_get(f"/api/lessons/{lid}", token)
        lesson = detail.get("lesson", detail) if isinstance(detail, dict) else detail
        rec = lesson_record(lesson, download_files=download_files, dest=dest, log=log)
        with open(lessons_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        records.append(rec)
        stats["new"] += 1
        stats["pdfs"] += sum(1 for s in rec["slides"] if (s.get("pdf") or {}).get("path"))
        stats["errors"] += sum(1 for s in rec["slides"] if (s.get("pdf") or {}).get("error"))
        log(f"  [{pos}/{len(items)}] {rec.get('index')}. {str(rec.get('title'))[:60]}")
        if delay:
            time.sleep(delay)

    all_records = []
    if os.path.exists(lessons_path):
        for line in Path(lessons_path).read_text(errors="replace").splitlines():
            try:
                all_records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    _write_at(dest, "ed_lessons.md", render_markdown(all_records, course_id).encode())
    return stats
