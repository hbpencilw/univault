"""moodle_report — human-readable summaries for scan manifests."""
from .moodle_courses import course_code


def _fmt_size(size: int | None) -> str:
    if size is None:
        return "unknown"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{int(size)} B"


def scan_markdown(manifest: dict) -> str:
    t = manifest.get("totals", {})
    lines = [
        f"# Moodle scan — {manifest.get('scan_id')}",
        "",
        f"- Host: `{manifest.get('host')}`",
        f"- Started: {manifest.get('started_at')}  ·  Finished: {manifest.get('finished_at')}",
        f"- Status: **{manifest.get('status')}**",
        f"- Courses: {t.get('courses', 0)}  ·  Activities: {t.get('activities', 0)}  "
        f"·  Files: {t.get('files', 0)}  ·  Announcements: {t.get('announcements', 0)}  "
        f"·  Section summaries: {t.get('section_summaries', 0)}  "
        f"·  Unknown sizes: {t.get('unknown_sizes', 0)}",
        "",
        "| Course | Code | Sections | Files | Announcements | Errors |",
        "|---|---|---|---|---|---|",
    ]
    for c in manifest.get("courses", []):
        files = sum(len(r.get("files") or []) for r in c.get("resources", []))
        anns = len(c.get("announcements") or [])
        errors = len(c.get("errors") or [])
        lines.append(
            f"| {c.get('shortname')} | {c.get('code')} | {c.get('sections')} | {files} | {anns} | {errors} |"
        )
    categories = t.get("categories") or {}
    if categories:
        lines += ["", "## Resource classification", "",
                  "| Category | Count | Meaning |", "|---|---|---|"]
        meanings = {
            "file": "file/folder with downloadable files",
            "file_link_missing": "file/folder where no pluginfile link was found",
            "page": "Moodle page with extractable content",
            "page_missing": "page activity without a URL",
            "label": "label with server-rendered content (archived)",
            "label_missing": "label whose content was not rendered on the section page",
            "metadata_book": "book activity — metadata only in v0.3.0",
            "link_only": "external URL / LTI — link only",
            "forum": "forum — announcements handled separately",
            "metadata_other": "other module type — metadata only",
        }
        for cat, count in sorted(categories.items()):
            lines.append(f"| {cat} | {count} | {meanings.get(cat, '')} |")
        missing = [(c.get("code"), r) for c in manifest.get("courses", [])
                   for r in c.get("resources", []) if str(r.get("category", "")).endswith("_missing")]
        if missing:
            lines += ["", "## Items with missing content (not counted as archived)", ""]
            for code, r in missing[:40]:
                lines.append(f"- {code} cm{r.get('cmid')} ({r.get('name')}): {r.get('reason', 'unknown')}")
    for c in manifest.get("courses", []):
        errs = c.get("errors") or []
        warns = c.get("warnings") or []
        if not errs and not warns:
            continue
        lines += ["", f"## {c.get('shortname')} — diagnostics", ""]
        lines += [f"- ERROR: {e}" for e in errs]
        lines += [f"- warning: {w}" for w in warns[:20]]
    lines += ["", "> Scan performs no file downloads. Run the explicit `sync` command to archive.",
              "> Announcement counts above are discussions; posts/replies are counted at sync time."]
    return "\n".join(lines)
