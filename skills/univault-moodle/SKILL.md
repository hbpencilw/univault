---
name: univault-moodle
description: "Moodle adapter for UniVault (Monash learning.monash.edu). Archive announcements, course files, pages and deadlines using the browser session exported from Microsoft Edge. Cookie-based CLI: import-cookie, status, probe, scan and sync; verified against a real Monash session. Use when the user wants to sync Moodle course materials or announcements."
description_zh: "UniVault 的 Moodle 适配器（Monash learning.monash.edu）。用 Edge 导出的浏览器会话归档公告、课程文件、页面与截止日期。Cookie 版 CLI：import-cookie / status / probe / scan / sync，已在真实 Monash 会话上验收。用户想同步 Moodle 课程资料或公告时使用。"
version: 0.3.1-beta
---

# UniVault adapter — Moodle

**Status**: production v0.3.1-beta. Cookie-session auth, course scan, file/page/
label/announcement sync, version history, crash recovery and idempotency are
implemented and covered by 111 offline tests; the full flow has been verified
against a real Monash account. The old token/`launch.php` paths are **dead** at
Monash (`admin/tool/mobile/*` return 404) and were removed.

## Quick start

```bash
univault login                    # one-time: Edge window -> Monash Okta/MFA (persistent profile)
univault status                   # Ed token + Moodle session + scan manifest
univault sync --course UNIT1001    # explicit archive for one unit
univault sync                     # explicit archive for all Ed + Moodle courses
```

`univault login` opens a dedicated Edge window (Playwright, system Edge, profile
at `~/Library/Application Support/univault/edge-profile`, 0700). After the
first Okta/MFA login, later syncs reuse the profile silently; when the Okta
session finally expires, `univault sync` reopens the login window
automatically. Passwords are never handled and MFA is never automated.

Playwright lives in its own venv (`~/.local/lib/univault/venv`); the first
`login` bootstraps it via `uv` if needed. The crawl path is unchanged: after
login the Moodle cookie is written to `~/.config/univault/moodle-cookie`
(0600) and the stdlib HTTP client does the rest.

Manual fallback (no Playwright): `moodle-setup import-cookie` / `--from-clipboard`.

### Lower-level commands (still supported)

```bash
moodle-setup login [--timeout 600] [--check]
moodle-setup status               # validate session, count courses
moodle-setup probe                # read-only AJAX capability matrix

moodle-archive scan --dest <DIR> --course-id <CID> --learning-root <ROOT>
moodle-archive report --dest <DIR>
```

`scan` discovers courses, structure, announcement discussions and file/folder
links; it **never downloads file bodies** (HEAD only, CDN redirects followed
without cookies). Verified live on a real unit: 45 sections, 88 activities,
43 files (all sized), 16 announcements, 0 errors.

Cookie sources: `--from-file PATH`, `--from-clipboard` (macOS `pbpaste`).
Storage: `~/.config/univault/moodle-cookie` (dir 0700, file 0600, atomic
write). Values are never printed; only names and lengths are reported.

Exit codes: `0` ok · `1` usage/no cookie · `2` auth failed · `3`
network/blocked · `4` partial capability (probe) / partial scan.

## Sync (explicit archive command)

```bash
MANIFEST=<dest>/_state/scan-latest.json

# plan only (no network, no writes)
moodle-archive sync --from-scan "$MANIFEST" --dry-run

# selective acceptance targets (stable ids from the scan manifest)
moodle-archive sync --from-scan "$MANIFEST" --course-id <course-id> --cmid <cmid>          # one small PDF
moodle-archive sync --from-scan "$MANIFEST" --course-id <course-id> --cmid <cmid>          # one Moodle Page
moodle-archive sync --from-scan "$MANIFEST" --course-id <course-id> --discussion-id <discussion-id>  # one announcement thread

# whole course (only when explicitly requested)
moodle-archive sync --from-scan "$MANIFEST" --course-id <course-id>
```

- Only runs when invoked explicitly; never downloads on `scan`. A selection
  that matches nothing fails with exit 1 instead of archiving the whole course.
- Writes into each course's `suggested_dir` (override with `--dest-root`).
- Layout per course: `moodle.jsonl` (current records), `resources/`, `pages/`,
  `sections/<n>/summary.html`, `sections/<n>/labels/<cmid>.html`,
  `announcements/`, `_history/<logical_id>/<sha>.<ext>`,
  `_state/journal.jsonl`, `_reports/sync-*.md`.
- Files: streaming + SHA-256 + Content-Type/Length checks + atomic rename;
  Moodle → CloudFront redirects are followed **without** the cookie; signed
  CDN URLs are never persisted.
- Labels are archived from server-rendered section pages
  (`course/view.php?id=..&section=N`), including inline images (downloaded and
  rewritten locally). Standard Moodle section summaries are archived the same
  way; Monash's `div.assessmentsummary` is a course-level "Assessment
  structure" table (identical on every section) and is **not** archived as a
  section summary — it is counted as evidence only.
- Announcements: root posts + nested replies (`parent_post_id`), author/time,
  attachments; content changes create `_history/` versions.
- Crash safety: journal recovery handles both "current replaced, JSONL not
  committed" (adopts version) and "current missing, history present"
  (restores). The old current is copied to `_history/` before replacement.
- Exit codes: 0 ok · 4 partial (some items failed) · 1/2/3 as above.

## Known limits (v0.3.0-beta)

- Monash currently exposes **no standard section summaries**; the
  `div.assessmentsummary` widget is detected and reported, never archived as a
  summary. Labels in stealth sections (e.g. a course-module marked `stealth=true`)
  are intentionally not rendered and are recorded metadata-only with the
  reason.
- Announcement pagination beyond the discussion page and edit history before
  the first capture are out of scope; observed edits are versioned.
- Real Monash downloads have not been run yet — the first real sync must be
  an explicit user command.
- Full-corpus (all 9 courses) acceptance is still pending.

## How to export the cookie (Edge)

1. Log in to `https://learning.monash.edu` in Microsoft Edge (Okta + MFA).
2. F12 → Network → any `learning.monash.edu` request → Request Headers →
   copy the whole `Cookie:` value.
3. `moodle-setup import-cookie` and paste (input is hidden, so it never lands
   in shell history or chat).

Prefer the whole header (includes `MoodleSession`, `MOODLEID1_`, …). Do not
assume a single cookie is always enough, and never paste the cookie into the
chat.

## P0 capability probe

`moodle-setup probe` calls `/lib/ajax/service.php?sesskey=…` with a
**whitelist** of read-only functions and records per-function status:

```
core_course_get_enrolled_courses_by_timeline_classification
core_courseformat_get_state
core_enrol_get_users_courses
core_course_get_contents
mod_forum_get_forums_by_courses
mod_forum_get_forum_discussions
mod_forum_get_forum_discussion_posts
```

Statuses are distinct: `supported` / `unsupported` / `permission-denied` /
`session-expired` / `invalid-arguments` / `unknown`. Anything unsupported must
fall back to HTML parsing (P1). There is deliberately **no generic
`wsfunction` passthrough**.

Matrix cache: `~/.config/univault/capabilities.json` (not permanent — re-probe
after cookie changes or Moodle upgrades).

## Session validity rules

- Login only counts as valid if `/my/` renders a dashboard **and** yields
  `M.cfg` session data; a login page (`name="logintoken"`) or a redirect to
  Okta is `AUTH_EXPIRED`.
- A missing `sesskey` is a diagnostic warning, not proof of expiry.
- Request paths only ever go to `https://<Moodle host>`; redirects are
  re-validated hop by hop.

## Safety

- Cookie never sent to any other host; literal private/loopback/reserved IPs
  and non-public DNS answers are refused at request time.
- Downloads: streamed, SHA-256 hashed, `.part` + fsync + atomic rename;
  HTML/login bodies rejected; content-length mismatches leave no file.
- Read-only remote operations only: no forum posts, no submissions, no
  profile changes.

## Not implemented yet (P1)

- `moodle-archive scan` (discovery only, no downloads) and `sync` (explicit
  archive command) sharing one engine with this CLI.
- HTML fallback parser, course/section/activity model, file download
  pipeline, announcements + replies + attachments, version history, JSONL
  projection for `univault-normalize`.
- `docs/MOODLE_P0_REPORT.md` (needs the real cookie first).

See `docs/MOODLE_CONNECTOR.md` for the module map and
`docs/MOODLE_BASELINE_AUDIT.md` for the v0.2.0 → v0.3.0 compatibility notes.
