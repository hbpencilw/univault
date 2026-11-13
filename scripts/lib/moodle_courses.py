"""moodle_courses — enrolled-course discovery via read-only AJAX.

Verified on Monash (2026-11-12): core_course_get_enrolled_courses_by_timeline
_classification works for "inprogress"; pagination via limit/offset. Course
codes are display values only — the stable identity is (host, course id).
"""
import re

from . import moodle_api

CLASSIFICATIONS = ("inprogress", "past", "future")


def _courses_from(data) -> list[dict]:
    if isinstance(data, dict):
        for key in ("courses", "data"):
            if isinstance(data.get(key), list):
                return data[key]
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def discover_courses(client, sesskey: str, *, classifications=CLASSIFICATIONS,
                     limit: int = 100, delay: float = 0.0) -> list[dict]:
    found: dict[int, dict] = {}
    for cls in classifications:
        offset = 0
        while True:
            result = moodle_api.call(
                client, sesskey,
                "core_course_get_enrolled_courses_by_timeline_classification",
                {"classification": cls, "limit": limit, "offset": offset},
            )
            if not result.ok:
                break
            batch = _courses_from(result.data)
            for c in batch:
                cid = c.get("id")
                if cid is None or cid in found:
                    continue
                found[cid] = {
                    "id": int(cid),
                    "shortname": c.get("shortname"),
                    "fullname": c.get("fullname"),
                    "classification": cls,
                    "startdate": c.get("startdate"),
                    "enddate": c.get("enddate"),
                    "url": f"{client.base_url}/course/view.php?id={cid}",
                }
            if len(batch) < limit:
                break
            offset += limit
            if delay:
                import time
                time.sleep(delay)
    return list(found.values())


_CODE_RE = re.compile(r"([A-Z]{2,4}\d{4})(?![0-9A-Za-z])")
_TERM_RE = re.compile(r"\bS(\d)\b[^\d]*\b(20\d{2})\b", re.I)


def course_code(course: dict) -> str:
    m = _CODE_RE.search(course.get("shortname") or "") or _CODE_RE.search(course.get("fullname") or "")
    return m.group(1) if m else (course.get("shortname") or f"course{course.get('id')}")


def course_term(course: dict) -> str | None:
    text = f"{course.get('shortname') or ''} {course.get('fullname') or ''}"
    m = _TERM_RE.search(text)
    return f"{m.group(2)}_s{m.group(1)}" if m else None


def suggest_dir(course: dict, learning_root: str | None) -> str | None:
    if not learning_root:
        return None
    code = course_code(course).lower()
    term = course_term(course)
    if term:
        return f"{learning_root.rstrip('/')}/{term}/{code}/memory/resources/moodle"
    return f"{learning_root.rstrip('/')}/{code}/memory/resources/moodle"
