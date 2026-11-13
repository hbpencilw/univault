"""moodle_structure — course structure from core_courseformat_get_state.

The AJAX payload is a double-encoded JSON string on Monash (verified
2026-11-12). Module "modname" values are display strings ("File", "Folder",
"Text and media area", "External tool", "CMS"), so they are normalised here.
"""
import json

MODNAME_MAP = {
    "file": "resource",
    "folder": "folder",
    "page": "page",
    "book": "book",
    "url": "url",
    "forum": "forum",
    "quiz": "quiz",
    "assignment": "assign",
    "external tool": "lti",
    "attendance": "attendance",
    "text and media area": "label",
    "label": "label",
    "cms": "cms",
    "subsection": "cms",
}


def normalize_modname(name: str | None) -> str:
    key = (name or "").strip().lower()
    return MODNAME_MAP.get(key, key.replace(" ", "_") or "unknown")


def parse_state(data):
    """Accept dict or JSON string (Monash double-encodes it); return a dict."""
    for _ in range(2):
        if not isinstance(data, str):
            break
        data = json.loads(data)
    return data if isinstance(data, dict) else {}


def build_structure(state: dict) -> dict:
    """{sections: [...], cms: {cmid: {...}}, announcements_forum: cm|None}"""
    sections = []
    for sec in state.get("section") or []:
        sections.append({
            "id": sec.get("id"),
            "number": sec.get("number"),
            "title": sec.get("title") or sec.get("rawtitle"),
            "rawtitle": sec.get("rawtitle"),
            "cmlist": [int(x) for x in (sec.get("cmlist") or [])],
            "visible": sec.get("visible", True),
            "hassummary": bool(sec.get("hassummary")),
            "parent": sec.get("parent"),
            "sectionurl": sec.get("sectionurl"),
        })

    cm_section = {}
    for sec in sections:
        for cmid in sec["cmlist"]:
            cm_section.setdefault(cmid, sec["number"])

    cms = {}
    for cm in state.get("cm") or []:
        cid = cm.get("id")
        if cid is None:
            continue
        cms[int(cid)] = {
            "id": int(cid),
            "modname": cm.get("modname"),
            "type": normalize_modname(cm.get("modname")),
            "name": cm.get("name"),
            "url": cm.get("url"),
            "uservisible": cm.get("uservisible", True),
            "stealth": bool(cm.get("stealth")),
            "section": cm_section.get(int(cid)),
        }

    announcements = None
    for cm in cms.values():
        if cm["type"] != "forum":
            continue
        name = (cm["name"] or "").lower()
        if "announce" in name:
            announcements = cm
            break
    if announcements is None:
        announcements = next((c for c in cms.values() if c["type"] == "forum"), None)

    return {"sections": sections, "cms": cms, "announcements_forum": announcements}
