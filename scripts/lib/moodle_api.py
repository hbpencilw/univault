"""moodle_api — whitelisted read-only Moodle AJAX calls + capability probe.

Moodle's session-authenticated AJAX endpoint (/lib/ajax/service.php?...
&sesskey=...) accepts a JSON body of [{"index":0,"methodname":...,"args":...}].
Only functions that Moodle itself serves to a logged-in browser are attempted;
unknown or write functions are refused by construction (no generic passthrough).

Capability statuses (docs/PLAN_MOODLE.md section 7.4) are distinct:
supported / unsupported / permission-denied / session-expired /
invalid-arguments / unknown.
"""
import json
import time
from dataclasses import dataclass, field

from .moodle_errors import AuthExpired, PermissionDenied, ServerError

AJAX_PATH = "/lib/ajax/service.php"

# Read-only functions only. args_required documents what a caller must supply.
READ_ONLY_FUNCTIONS = {
    "core_course_get_enrolled_courses_by_timeline_classification",
    "core_courseformat_get_state",
    "core_enrol_get_users_courses",
    "core_course_get_contents",
    "mod_forum_get_forums_by_courses",
    "mod_forum_get_forum_discussions",
    "mod_forum_get_forum_discussion_posts",
}

STATUS_MAP = {
    "servicenotavailable": "unsupported",
    "accessexception": "permission-denied",
    "nopermissions": "permission-denied",
    "requireloginerror": "session-expired",
    "invalidparameter": "invalid-arguments",
    "invalidparameterformat": "invalid-arguments",
    "invalidtoken": "session-expired",
    "invalidrecord": "unknown",
}


@dataclass
class AjaxResult:
    function: str
    status: str  # supported | unsupported | permission-denied | session-expired | invalid-arguments | unknown
    data: object = None
    error_code: str | None = None
    error_message: str | None = None
    raw: object = None

    @property
    def ok(self) -> bool:
        return self.status == "supported"

    def to_dict(self) -> dict:
        out = {"function": self.function, "status": self.status}
        if self.error_code:
            out["error_code"] = self.error_code
        if self.error_message:
            out["error_message"] = self.error_message
        return out


def _extract_error(item: dict) -> tuple[str | None, str | None]:
    exc = item.get("exception") or {}
    code = item.get("errorcode") or exc.get("errorcode")
    message = item.get("message") or exc.get("message")
    return code, message


def classify_error(code: str | None, message: str | None) -> str:
    if code in STATUS_MAP:
        return STATUS_MAP[code]
    text = f"{code or ''} {message or ''}".lower()
    if "not available" in text or "servicenotavailable" in text:
        return "unsupported"
    if "permission" in text or "access" in text:
        return "permission-denied"
    if "login" in text or "session" in text:
        return "session-expired"
    if "invalid" in text and "param" in text:
        return "invalid-arguments"
    return "unknown"


def call(client, sesskey: str, function: str, args: dict | None = None) -> AjaxResult:
    if function not in READ_ONLY_FUNCTIONS:
        raise ValueError(f"function not in read-only whitelist: {function}")
    if not sesskey:
        return AjaxResult(function, "session-expired", error_code="no-sesskey",
                          error_message="sesskey missing from the session")
    url = client.base_url + AJAX_PATH + "?sesskey=" + sesskey
    body = json.dumps([{"index": 0, "methodname": function, "args": args or {}}]).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    try:
        resp = client.request("POST", url, data=body, extra_headers=headers)
    except AuthExpired as e:
        return AjaxResult(function, "session-expired", error_code="redirect-sso", error_message=e.message)
    except PermissionDenied as e:
        return AjaxResult(function, "permission-denied", error_code="http-403", error_message=e.message)
    if resp.status != 200:
        if resp.status in (401, 403):
            return AjaxResult(function, "permission-denied", error_code=f"http-{resp.status}")
        if resp.status == 404:
            return AjaxResult(function, "unsupported", error_code="http-404")
        return AjaxResult(function, "unknown", error_code=f"http-{resp.status}")
    text = resp.body.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return AjaxResult(function, "unknown", error_code="malformed-json",
                          error_message="server did not return JSON (login page or theme override?)")
    if not isinstance(payload, list) or not payload:
        return AjaxResult(function, "unknown", error_code="unexpected-shape")
    item = payload[0]
    if not isinstance(item, dict):
        return AjaxResult(function, "unknown", error_code="unexpected-item")
    if item.get("error"):
        code, message = _extract_error(item)
        return AjaxResult(function, classify_error(code, message), error_code=code,
                          error_message=(message or "")[:300], raw=item)
    return AjaxResult(function, "supported", data=item.get("data"), raw=item)


# ------------------------------------------------------------------ probing
def _courses_from(result: AjaxResult) -> list[dict]:
    data = result.data
    if isinstance(data, dict):
        for key in ("courses", "data"):
            if isinstance(data.get(key), list):
                return data[key]
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def _forum_id_from(result: AjaxResult) -> int | None:
    forums = result.data if isinstance(result.data, list) else []
    for f in forums:
        if isinstance(f, dict) and f.get("id"):
            return int(f["id"])
    return None


def probe(client, sesskey: str, *, delay: float = 1.0, max_pages: int = 1) -> dict:
    """Probe the whitelisted read-only functions; returns a capability matrix."""
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    results: list[dict] = []

    def record(result: AjaxResult, fallback: str = "html"):
        entry = result.to_dict()
        entry["fallback"] = fallback
        entry["evidence"] = "VERIFIED-LIVE"
        results.append(entry)
        if delay:
            time.sleep(delay)
        return result

    courses: list[dict] = []
    c1 = record(call(client, sesskey, "core_course_get_enrolled_courses_by_timeline_classification",
                     {"classification": "inprogress", "limit": 100, "offset": 0}))
    if c1.ok:
        courses = _courses_from(c1)

    if not courses:
        c2 = record(call(client, sesskey, "core_enrol_get_users_courses", {"userid": 0}))
        # userid is not known here; probe with 0 only to see whether the function
        # is callable at all — callers with a session userid should probe again.
        if c2.ok:
            courses = _courses_from(c2)

    course_id = None
    for c in courses:
        cid = c.get("id") if isinstance(c, dict) else None
        if cid:
            course_id = int(cid)
            break

    if course_id:
        record(call(client, sesskey, "core_courseformat_get_state", {"courseid": course_id}))
        record(call(client, sesskey, "core_course_get_contents", {"courseid": course_id}))
        forums = record(call(client, sesskey, "mod_forum_get_forums_by_courses", {"courseids": [course_id]}))
        forum_id = _forum_id_from(forums) if forums.ok else None
        if forum_id:
            discussions = record(call(client, sesskey, "mod_forum_get_forum_discussions",
                                      {"forumid": forum_id, "page": 0, "perpage": 5}))
            if discussions.ok and isinstance(discussions.data, list) and discussions.data:
                did = discussions.data[0].get("discussion") or discussions.data[0].get("id")
                if did:
                    record(call(client, sesskey, "mod_forum_get_forum_discussion_posts",
                                {"discussionid": int(did)}))
            else:
                results.append({"function": "mod_forum_get_forum_discussion_posts",
                                "status": "unknown", "reason": "no discussion available to probe",
                                "fallback": "html", "evidence": "UNPROBED"})
        else:
            results.append({"function": "mod_forum_get_forum_discussions",
                            "status": "unknown", "reason": "no forum available to probe",
                            "fallback": "html", "evidence": "UNPROBED"})
    else:
        for fn in ("core_courseformat_get_state", "core_course_get_contents",
                   "mod_forum_get_forums_by_courses", "mod_forum_get_forum_discussions",
                   "mod_forum_get_forum_discussion_posts"):
            results.append({"function": fn, "status": "unknown",
                            "reason": "no course discovered; needs a real course id",
                            "fallback": "html", "evidence": "UNPROBED"})

    statuses = [r["status"] for r in results]
    supported = statuses.count("supported")
    summary = {
        "host": client.host,
        "probed_at": started,
        "release": None,
        "functions": results,
        "course_sample": next(({"id": c.get("id"), "shortname": c.get("shortname")}
                               for c in courses if isinstance(c, dict) and c.get("id")), None),
        "supported": supported,
        "total": len(results),
    }
    return summary
