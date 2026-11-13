"""moodle_scan — discovery only. Never downloads course files.

Produces a scan manifest (JSON) plus a human report; the manifest is the input
for the later explicit `sync` command. Structural data comes from
core_courseformat_get_state; file/folder links are resolved by reading
activity HTML pages (allowed for enumeration); pluginfile bodies are only
HEADed for size, never fetched.
"""
import json
import os
import re
import time
import urllib.parse
from pathlib import Path

from . import moodle_api, moodle_courses, moodle_parse, moodle_structure
from .moodle_errors import MoodleError
from .moodle_http import parse_content_disposition

SCHEMA = 1
PREVIEW_RESOURCE_TYPES = {"resource", "folder"}
_NAME_RE = re.compile(r"[A-Za-z0-9._-]{1,128}")


# ------------------------------------------------------------------ path safety
def safe_dir(path: str) -> str:
    """Expand and normalise a user-supplied directory; reject traversal parts."""
    raw = os.path.expanduser(path or "")
    if not raw:
        raise ValueError("empty destination directory")
    if ".." in Path(raw).parts:
        raise ValueError("destination directory must not contain '..'")
    resolved = os.path.realpath(raw)
    if ".." in Path(resolved).parts:
        raise ValueError("destination directory escapes its root")
    return resolved


def _check_name(name: str) -> str:
    if not _NAME_RE.fullmatch(name or ""):
        raise ValueError(f"invalid filename: {name!r}")
    return name


def write_text_at(directory: str, name: str, text: str) -> None:
    """Write a file inside `directory` using a directory fd and a bare name.

    The name is restricted to [A-Za-z0-9._-]; because the file descriptor of
    the directory pins the target, no path component can escape it.
    """
    _check_name(name)
    dir_fd = os.open(directory, os.O_RDONLY)
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644, dir_fd=dir_fd)
        try:
            os.write(fd, text.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        os.close(dir_fd)


def replace_at(directory: str, src_name: str, dst_name: str) -> None:
    _check_name(src_name)
    _check_name(dst_name)
    dir_fd = os.open(directory, os.O_RDONLY)
    try:
        os.replace(src_name, dst_name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    finally:
        os.close(dir_fd)


# ------------------------------------------------------------------ helpers
def _links_for_activity(client, cm: dict) -> tuple[list[dict], str | None]:
    if not cm.get("url"):
        return [], "no activity url"
    if cm["type"] == "resource":
        # Moodle may serve the file directly (303 -> pluginfile -> CDN) or an
        # HTML wrapper page. HEAD resolves the first case without downloading.
        try:
            head = client.head(cm["url"], external=True)
            final = urllib.parse.urlsplit(head.url)
            ctype = (head.headers.get("content-type") or "").split(";")[0].strip().lower()
            if "pluginfile.php" in final.path or (final.hostname or "") != client.host:
                name = (parse_content_disposition(head.headers.get("content-disposition"))
                        or final.path.rsplit("/", 1)[-1] or cm.get("name") or "file")
                length = head.headers.get("content-length")
                size = int(length) if length and str(length).isdigit() else None
                # Download through the stable activity URL (redirect handled at
                # fetch time, cookies only sent to the Moodle host).
                return [{"url": cm["url"], "name": name, "size": size}], None
        except MoodleError:
            pass
        try:
            resp = client.get(cm["url"])
        except MoodleError as e:
            return [], e.category
        html = resp.body.decode("utf-8", "replace")
        first = moodle_parse.extract_first_pluginfile(html, client.base_url)
        return ([first] if first else []), None if first else "no pluginfile link in resource page"

    try:
        resp = client.get(cm["url"])
    except MoodleError as e:
        return [], e.category
    html = resp.body.decode("utf-8", "replace")
    links = moodle_parse.extract_pluginfile_links(html, client.base_url)
    return links, None if links else "no pluginfile links in folder page"


def _head_size(client, url: str) -> int | None:
    """Best-effort size: HEAD, following Moodle's CDN redirect without cookies."""
    try:
        resp = client.head(url, external=True)
    except MoodleError:
        return None
    value = (resp.headers or {}).get("content-length")
    return int(value) if value and str(value).isdigit() else None


def _classify(cm: dict, files: list[dict]) -> str:
    t = cm["type"]
    if t in ("resource", "folder"):
        return "file" if files else "file_link_missing"
    if t == "page":
        return "page" if cm.get("url") else "page_missing"
    if t == "label":
        return "label" if cm.get("_content_available") else "label_missing"
    if t == "book":
        return "metadata_book"
    if t in ("url", "lti"):
        return "link_only"
    if t == "forum":
        return "forum"
    return "metadata_other"


def _section_details(client, course_id: int, structure: dict) -> list[dict]:
    """Fetch section pages that carry labels or summaries; record availability."""
    by_section: dict[int, list[dict]] = {}
    for cm in structure["cms"].values():
        if cm.get("type") == "label":
            by_section.setdefault(cm.get("section") or 0, []).append(cm)
    details = []
    for sec in structure["sections"]:
        number = sec.get("number")
        labels = by_section.get(number, [])
        if not labels and not sec.get("hassummary"):
            continue
        url = f"{client.base_url}/course/view.php?id={course_id}&section={number}"
        parsed = {"summary_html": None, "summary_kind": None, "labels": [],
                  "assessment_widget_html": None}
        error = None
        try:
            html = client.get(url).body.decode("utf-8", "replace")
            parsed = moodle_parse.parse_section_page(html)
        except MoodleError as e:
            error = e.category
        found = {l["cmid"]: l for l in parsed.get("labels", [])}
        for cm in labels:
            cm["_content_available"] = bool((found.get(cm["id"]) or {}).get("html"))
            if not cm["_content_available"]:
                cm["_content_error"] = (
                    "stealth activity — intentionally not rendered on the course page (no direct view)"
                    if cm.get("stealth")
                    else "module not rendered on the section page")
        details.append({
            "number": number,
            "title": sec.get("title"),
            "hassummary": bool(sec.get("hassummary")),
            "summary_available": bool(parsed.get("summary_html")),
            "summary_kind": parsed.get("summary_kind"),
            "assessment_widget_present": bool(parsed.get("assessment_widget_html")),
            "label_cmids": [cm["id"] for cm in labels],
            "error": error,
        })
    return details


def _announcements(client, cm: dict, limit_pages: int = 50) -> tuple[list[dict], str | None]:
    url = cm.get("url")
    if not url:
        return [], "announcement forum has no url"
    discussions: list[dict] = []
    seen: set[int] = set()
    for page in range(limit_pages):
        page_url = f"{url}&page={page}" if page else url
        try:
            html = client.get(page_url).body.decode("utf-8", "replace")
        except MoodleError as e:
            return discussions, e.category
        batch = moodle_parse.parse_forum_discussions(html, client.base_url)
        new = [d for d in batch if d["discussion_id"] not in seen]
        for d in new:
            seen.add(d["discussion_id"])
        discussions.extend(new)
        if not batch or not new or len(batch) < 10:
            break
    return discussions, None


# ------------------------------------------------------------------ scan
def scan(client, session, dest: str, *, course_ids: list[int] | None = None,
         learning_root: str | None = None, resolve_links: bool = True) -> dict:
    dest = safe_dir(dest)
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    manifest = {
        "schema": SCHEMA,
        "scan_id": time.strftime("%Y%m%dT%H%M%S"),
        "host": client.host,
        "started_at": started,
        "status": "complete",
        "auth": {"sesskey_present": bool(session.sesskey), "userid": session.userid},
        "courses": [],
        "totals": {},
        "warnings": [],
    }

    courses = moodle_courses.discover_courses(client, session.sesskey)
    if course_ids:
        wanted = {int(x) for x in course_ids}
        courses = [c for c in courses if c["id"] in wanted]

    totals = {"courses": 0, "files": 0, "announcements": 0, "unknown_sizes": 0, "activities": 0,
              "categories": {}, "section_summaries": 0, "assessment_widgets": 0}
    for course in courses:
        entry = {
            "id": course["id"],
            "shortname": course["shortname"],
            "fullname": course["fullname"],
            "classification": course["classification"],
            "code": moodle_courses.course_code(course),
            "term": moodle_courses.course_term(course),
            "suggested_dir": moodle_courses.suggest_dir(course, learning_root),
            "sections": 0,
            "activities": {},
            "announcements_forum": None,
            "announcements": [],
            "resources": [],
            "warnings": [],
            "errors": [],
        }
        try:
            result = moodle_api.call(client, session.sesskey, "core_courseformat_get_state",
                                     {"courseid": course["id"]})
            if not result.ok:
                entry["errors"].append(f"get_state: {result.status} ({result.error_code or ''})")
                manifest["status"] = "partial"
                manifest["courses"].append(entry)
                continue
            structure = moodle_structure.build_structure(moodle_structure.parse_state(result.data))
            entry["sections"] = len([s for s in structure["sections"] if s["number"] != 0])
            counts: dict[str, int] = {}
            for cm in structure["cms"].values():
                counts[cm["type"]] = counts.get(cm["type"], 0) + 1
            entry["activities"] = counts

            sections_detail = _section_details(client, course["id"], structure) if resolve_links else []
            entry["sections_detail"] = sections_detail
            totals["section_summaries"] += sum(1 for d in sections_detail if d["summary_available"])
            totals["assessment_widgets"] += sum(1 for d in sections_detail if d.get("assessment_widget_present"))

            af = structure["announcements_forum"]
            if af:
                entry["announcements_forum"] = {"cmid": af["id"], "name": af["name"], "url": af["url"]}
                discussions, err = _announcements(client, af)
                entry["announcements"] = discussions
                if err:
                    entry["errors"].append(f"announcements: {err}")
                totals["announcements"] += len(discussions)
            else:
                entry["warnings"].append("no forum module found (unable to locate announcements)")

            for cm in structure["cms"].values():
                if not cm.get("uservisible", True):
                    continue
                if cm["type"] == "cms":
                    continue  # structural container only
                files: list[dict] = []
                if cm["type"] in PREVIEW_RESOURCE_TYPES:
                    files, err = _links_for_activity(client, cm) if resolve_links else ([], "link resolution disabled")
                    if err and resolve_links:
                        entry["warnings"].append(f"{cm['type']} {cm['id']}: {err}")
                    for f in files:
                        if f.get("size") is None:
                            f["size"] = _head_size(client, f["url"])
                        totals["files"] += 1
                        if f["size"] is None:
                            totals["unknown_sizes"] += 1
                category = _classify(cm, files)
                totals["categories"][category] = totals["categories"].get(category, 0) + 1
                resource = {
                    "cmid": cm["id"], "type": cm["type"], "name": cm["name"],
                    "section": cm["section"], "url": cm["url"], "files": files,
                    "category": category,
                }
                if cm["type"] == "label":
                    resource["content_available"] = bool(cm.get("_content_available"))
                if category.endswith("_missing"):
                    resource["reason"] = cm.get("_content_error") or "content not found in server-rendered HTML"
                entry["resources"].append(resource)
                totals["activities"] += 1
        except MoodleError as e:
            entry["errors"].append(f"{e.category}: {e.message}")
            manifest["status"] = "partial"
        manifest["courses"].append(entry)
        totals["courses"] += 1

    manifest["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    manifest["totals"] = totals
    _write_outputs(manifest, dest)
    return manifest


def _write_outputs(manifest: dict, dest: str) -> None:
    from . import moodle_report

    base = safe_dir(dest)
    state_dir = os.path.join(base, "_state")
    report_dir = os.path.join(base, "_reports")
    os.makedirs(state_dir, exist_ok=True)
    os.makedirs(report_dir, exist_ok=True)

    payload = json.dumps(manifest, indent=2, ensure_ascii=False)
    scan_id = re.sub(r"[^A-Za-z0-9]", "", str(manifest.get("scan_id") or "")) or time.strftime("%Y%m%dT%H%M%S")

    write_text_at(state_dir, "scan-latest.json.tmp", payload)
    replace_at(state_dir, "scan-latest.json.tmp", "scan-latest.json")
    write_text_at(report_dir, f"scan-{scan_id}.json", payload)
    write_text_at(report_dir, f"scan-{scan_id}.md", moodle_report.scan_markdown(manifest) + "\n")
