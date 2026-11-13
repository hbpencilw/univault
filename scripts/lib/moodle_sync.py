"""moodle_sync — explicit archive command (files, pages, labels, summaries, announcements).

Contract:
- runs only when the user explicitly invokes `moodle-archive sync`;
- reads a scan manifest produced by `moodle-archive scan`;
- can be restricted to single stable resources: `cmids` (course module ids) and
  `discussion_ids` (announcement discussions) — no whole-course download is
  implied by a selective request;
- downloads through stable activity URLs (CloudFront redirects handled by the
  HTTP client without leaking cookies; signed CDN URLs are never persisted);
- every item is committed through CourseStore (staged, hashed, versioned);
- a single failing resource never aborts the course or the run.
"""
import os
import re
import time
import urllib.parse

from . import moodle_download, moodle_parse, safe_fs
from .moodle_errors import MoodleError
from .moodle_store import CourseStore

METADATA_TYPES = {"label", "book", "quiz", "assign", "url", "lti", "attendance", "cms", "forum"}
_IMG_RE = re.compile(r'<img([^>]*?)src="([^"]*pluginfile[^"]*)"', re.I | re.S)


def _slug(value: str, maxlen: int = 60) -> str:
    text = safe_fs.sanitize_component(value or "", fallback="item")
    text = "".join(ch if ch.isalnum() or ch in "-_. " else "_" for ch in text).strip(" ._")
    return (text or "item")[:maxlen]


def _section_dir(number) -> str:
    try:
        n = int(number)
    except (TypeError, ValueError):
        return "General"
    return "General" if n == 0 else f"Section {n:02d}"


def _course_dir(course: dict, dest_root: str | None) -> str:
    if dest_root:
        return os.path.join(safe_fs.sanitize_component(dest_root),
                            _slug(course.get("code") or f"course-{course.get('id')}"))
    suggested = course.get("suggested_dir")
    if not suggested:
        raise ValueError("course has no suggested_dir; pass --dest-root")
    return suggested


def _tally(stats: dict, outcome: dict) -> None:
    status = outcome.get("status")
    if status in ("new", "updated", "unchanged", "planned"):
        stats[status] += 1
    elif status == "skipped":
        stats["skipped"] += 1


def _meta_file(course: dict, res: dict, name: str) -> dict:
    return {
        "title": name or res.get("name"),
        "category": _section_dir(res.get("section")),
        "url": res.get("url"),
        "extra": {"cmid": res.get("cmid"), "modname": res.get("type"), "section": res.get("section")},
    }


def sync_course(client, session, course: dict, *, dest_root: str | None = None,
                dry_run: bool = False, max_bytes: int | None = None,
                cmids: set[int] | None = None, discussion_ids: set[int] | None = None,
                progress=None) -> dict:
    progress = progress or (lambda msg: None)
    course_dir = _course_dir(course, dest_root)
    store = CourseStore(course_dir, course, dry_run=dry_run)
    stats = {"new": 0, "updated": 0, "unchanged": 0, "planned": 0, "failed": 0,
             "skipped": 0, "bytes": 0, "errors": [],
             "discussions": 0, "posts": 0, "replies": 0, "attachments": 0,
             "labels": 0, "section_summaries": 0, "assessment_widgets_skipped": 0}
    matched = 0
    section_cache: dict[int, dict] = {}

    def fail(kind: str, ident, exc: Exception):
        stats["failed"] += 1
        stats["errors"].append({"kind": kind, "id": ident,
                                "error": getattr(exc, "category", type(exc).__name__),
                                "message": (getattr(exc, "message", str(exc)) or "")[:200]})
        progress(f"  ! {kind} {ident}: {getattr(exc, 'category', type(exc).__name__)}")

    def section_parsed(number: int) -> dict:
        if number not in section_cache:
            url = f"{client.base_url}/course/view.php?id={course.get('id')}&section={number}"
            html = client.get(url).body.decode("utf-8", "replace")
            section_cache[number] = moodle_parse.parse_section_page(html)
        return section_cache[number]

    def archive_html(logical_id: str, kind: str, rel_html: str, fragment: str, meta: dict,
                     asset_base: str) -> None:
        """Sanitize fragment, download inline pluginfile images, rewrite srcs, commit."""
        fragment = moodle_parse.sanitize_html(fragment)
        assets: list[tuple[str, str]] = []

        def replace(match):
            attrs, src = match.group(1), match.group(2)
            name = safe_fs.sanitize_component(
                urllib.parse.unquote(urllib.parse.urlsplit(src).path.rsplit("/", 1)[-1]) or "image")
            local = os.path.join(asset_base, name)
            try:
                staged = moodle_download.download_to_staging(client, src, store.staging_dir,
                                                             max_bytes=max_bytes)
                stats["bytes"] += staged["bytes"]
                _tally(stats, store.commit_file(f"{logical_id}:img:{name}", "file", local,
                                                staged["staging"], {"title": name, "url": src,
                                                                    "category": "Embedded assets"}))
                assets.append((src, name))
                return f'<img{attrs}src="{os.path.basename(asset_base)}/{name}"'
            except MoodleError as e:
                fail("asset", src, e)
                return match.group(0)

        html = _IMG_RE.sub(replace, fragment or "")
        _tally(stats, store.commit_bytes(logical_id, kind, rel_html, html.encode(), meta))

    # ------------------------------------------------------------ files/pages
    do_resources = not discussion_ids
    for res in course.get("resources", []) if do_resources else []:
        rtype, cmid = res.get("type"), res.get("cmid")
        if cmids and cmid not in cmids:
            continue
        matched += 1
        name = res.get("name") or f"cm{cmid}"
        sec = _section_dir(res.get("section"))
        try:
            if rtype in ("resource", "folder") and res.get("files"):
                for f in res["files"]:
                    logical = f"cm:{cmid}" if len(res["files"]) == 1 else f"cm:{cmid}:{f.get('name')}"
                    rel = f"resources/{_slug(sec)}/{_slug(name)}/{safe_fs.sanitize_component(f.get('name') or 'file')}"
                    if dry_run:
                        stats["planned"] += 1
                        continue
                    staged = moodle_download.download_to_staging(
                        client, f["url"], store.staging_dir, max_bytes=max_bytes,
                        expected_size=int(f.get("size") or 0))
                    stats["bytes"] += staged["bytes"]
                    _tally(stats, store.commit_file(logical, "file", rel, staged["staging"],
                                                    _meta_file(course, res, f.get("name") or name)))
            elif rtype == "page" and res.get("url"):
                logical = f"cm:{cmid}"
                rel = f"pages/{cmid}-{_slug(name)}.html"
                if dry_run:
                    stats["planned"] += 1
                    continue
                html = client.get(res["url"]).body.decode("utf-8", "replace")
                content = moodle_parse.extract_page_content(html)
                if content:
                    archive_html(logical, "page", rel, content, _meta_file(course, res, name),
                                 f"pages/{cmid}-{_slug(name)}.assets")
                else:
                    store.commit_metadata(logical, "page", {**_meta_file(course, res, name),
                                                           "archive_status": "metadata-only",
                                                           "extra": {"cmid": cmid,
                                                                     "reason": "page content not found"}})
                    stats["skipped"] += 1
            elif rtype == "label":
                section = int(res.get("section") or 0)
                if dry_run:
                    stats["planned"] += 1
                    continue
                parsed = section_parsed(section)
                label = next((l for l in parsed.get("labels", []) if l.get("cmid") == cmid), None)
                if label and label.get("html"):
                    base = f"sections/{section}/labels/{cmid}-{_slug(name)}"
                    archive_html(f"cm:{cmid}", "label", f"{base}.html", label["html"],
                                 {**_meta_file(course, res, name), "category": _section_dir(section)},
                                 f"{base}.assets")
                    stats["labels"] += 1
                else:
                    store.commit_metadata(f"cm:{cmid}", "label", {
                        **_meta_file(course, res, name), "archive_status": "metadata-only",
                        "extra": {"cmid": cmid, "reason": "label content not rendered on section page"}})
                    stats["skipped"] += 1
            else:
                kind = rtype if rtype in ("book", "quiz", "assign", "forum") else "link"
                store.commit_metadata(f"cm:{cmid}", kind, {
                    **_meta_file(course, res, name), "archive_status": "metadata-only",
                    "extra": {"cmid": cmid, "reason": "metadata-only module type"}})
                stats["skipped"] += 1
        except MoodleError as e:
            fail("resource", cmid, e)
        except (OSError, ValueError) as e:
            fail("resource", cmid, e)

    # -------------------------------------------------------- section summaries
    if not cmids and not discussion_ids:
        for detail in course.get("sections_detail", []):
            if not detail.get("summary_available"):
                if detail.get("assessment_widget_present"):
                    # Monash "Assessment structure" widget is course-level, not
                    # a section summary — never archived as one.
                    stats["assessment_widgets_skipped"] += 1
                continue
            number = detail.get("number")
            try:
                if dry_run:
                    stats["planned"] += 1
                    continue
                parsed = section_parsed(int(number))
                summary = parsed.get("summary_html")
                if not summary:
                    stats["skipped"] += 1
                    continue
                base = f"sections/{number}/summary"
                archive_html(f"section:{course.get('id')}:{number}", "section", f"{base}.html",
                             summary, {"title": detail.get("title") or f"Section {number}",
                                       "category": _section_dir(number),
                                       "url": f"{client.base_url}/course/view.php?id={course.get('id')}&section={number}",
                                       "extra": {"section": number, "kind": detail.get("summary_kind")}},
                             f"{base}.assets")
                stats["section_summaries"] += 1
            except MoodleError as e:
                fail("section-summary", number, e)
            except (OSError, ValueError) as e:
                fail("section-summary", number, e)

    # ---------------------------------------------------------- announcements
    forum = course.get("announcements_forum") or {}
    announcements = [] if cmids else course.get("announcements", [])
    for discussion in announcements:
        did = discussion.get("discussion_id")
        if discussion_ids and did not in discussion_ids:
            continue
        matched += 1
        try:
            if dry_run:
                stats["planned"] += 1
                continue
            url = f"{client.base_url}/mod/forum/discuss.php?d={did}"
            html = client.get(url).body.decode("utf-8", "replace")
            posts = moodle_parse.parse_discussion(html)
            stats["discussions"] += 1
            for post in posts:
                pid = post.get("post_id")
                is_reply = post.get("parent_post_id") is not None
                body = moodle_parse.sanitize_html(post.get("body_html") or "")
                meta = {
                    "title": post.get("subject") or discussion.get("subject"),
                    "category": forum.get("name"),
                    "created_at": post.get("created_at"),
                    "url": f"{url}#p{pid}",
                    "author": {"name": post.get("author"), "staff": None},
                    "extra": {
                        "discussion_id": did,
                        "forum_cmid": forum.get("cmid"),
                        "parent_post_id": post.get("parent_post_id"),
                        "role": "reply" if is_reply else "root",
                        "edited_observed": moodle_parse.has_edit_marker(post.get("body_html") or ""),
                    },
                }
                _tally(stats, store.commit_bytes(f"forum-post:{pid}", "announcement",
                                                 f"announcements/{did}/{pid}.html", body.encode(), meta))
                stats["posts"] += 1
                if is_reply:
                    stats["replies"] += 1
                for att in post.get("attachments", []):
                    aname = safe_fs.sanitize_component(att.get("name") or "attachment")
                    staged = moodle_download.download_to_staging(client, att["url"], store.staging_dir,
                                                                 max_bytes=max_bytes)
                    stats["bytes"] += staged["bytes"]
                    _tally(stats, store.commit_file(f"attachment:{pid}:{aname}", "file",
                                                    f"announcements/{did}/attachments/{pid}/{aname}",
                                                    staged["staging"], {
                                                        "title": aname,
                                                        "category": "Announcement attachments",
                                                        "url": att["url"],
                                                        "extra": {"discussion_id": did,
                                                                  "parent_post_id": pid},
                                                    }))
                    stats["attachments"] += 1
        except MoodleError as e:
            fail("announcement", did, e)
        except (OSError, ValueError) as e:
            fail("announcement", did, e)

    if (cmids or discussion_ids) and matched == 0:
        raise ValueError("no scanned resource matches the given --cmid/--discussion-id selection")

    summary = {
        "course": {"id": course.get("id"), "code": course.get("code"), "shortname": course.get("shortname")},
        "dir": os.path.realpath(course_dir),
        "dry_run": dry_run,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        **stats,
    }
    if not dry_run:
        store.finalize(summary)
        store.write_report(f"sync-{time.strftime('%Y%m%dT%H%M%S')}.md", _course_report(summary))
    return summary


def _course_report(summary: dict) -> str:
    c = summary.get("course", {})
    lines = [f"# Sync — {c.get('code')} ({c.get('shortname')})", "",
             f"- Directory: `{summary.get('dir')}`",
             f"- Finished: {summary.get('finished_at')}",
             f"- new {summary.get('new')} · updated {summary.get('updated')} · "
             f"unchanged {summary.get('unchanged')} · failed {summary.get('failed')} · "
             f"metadata-only {summary.get('skipped')} · bytes {summary.get('bytes')}",
             f"- labels {summary.get('labels')} · section summaries {summary.get('section_summaries')} "
             f"· assessment widgets skipped {summary.get('assessment_widgets_skipped')}",
             f"- announcements: discussions {summary.get('discussions')} · posts {summary.get('posts')} "
             f"· replies {summary.get('replies')} · attachments {summary.get('attachments')}"]
    if summary.get("errors"):
        lines += ["", "## Errors", ""] + [f"- {e['kind']} {e['id']}: {e['error']} — {e['message']}"
                                           for e in summary["errors"]]
    return "\n".join(lines) + "\n"


def sync(client, session, manifest: dict, *, dest_root: str | None = None,
         course_ids: list[int] | None = None, dry_run: bool = False,
         max_bytes: int | None = None, cmids: list[int] | None = None,
         discussion_ids: list[int] | None = None, progress=None) -> dict:
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    wanted = {int(x) for x in (course_ids or [])}
    cmid_set = {int(x) for x in (cmids or [])} or None
    discussion_set = {int(x) for x in (discussion_ids or [])} or None
    totals = {"courses": 0, "new": 0, "updated": 0, "unchanged": 0, "planned": 0,
              "failed": 0, "skipped": 0, "bytes": 0, "discussions": 0, "posts": 0,
              "replies": 0, "attachments": 0, "labels": 0, "section_summaries": 0,
              "assessment_widgets_skipped": 0}
    results = []
    for course in manifest.get("courses", []):
        if wanted and int(course.get("id")) not in wanted:
            continue
        result = sync_course(client, session, course, dest_root=dest_root, dry_run=dry_run,
                             max_bytes=max_bytes, cmids=cmid_set,
                             discussion_ids=discussion_set, progress=progress)
        results.append(result)
        totals["courses"] += 1
        for key in ("new", "updated", "unchanged", "planned", "failed", "skipped", "bytes",
                    "discussions", "posts", "replies", "attachments", "labels",
                    "section_summaries", "assessment_widgets_skipped"):
            totals[key] += result.get(key, 0)
    return {
        "host": manifest.get("host"),
        "sync_id": time.strftime("%Y%m%dT%H%M%S"),
        "started_at": started,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "dry_run": dry_run,
        "selection": {"cmids": sorted(cmid_set) if cmid_set else None,
                      "discussion_ids": sorted(discussion_set) if discussion_set else None},
        "status": "partial" if totals["failed"] else "complete",
        "totals": totals,
        "courses": results,
    }
