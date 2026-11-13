---
name: univault
description: "UniVault — unified university course-platform crawler. Archive every announcement, discussion thread, file and staff-uploaded material from your university's platforms. Monash University first (Moodle at learning.monash.edu + Ed Discussion at edstem.org), then the rest of Go8. Routes to the correct platform adapter, owns the unified corpus schema (univault.jsonl) and the per-course storage convention."
description_zh: "UniVault —— 统一的课程平台爬虫：把大学平台的公告、讨论帖、文件和老师上传的学习资料全部归档。先跑通 Monash University（learning.monash.edu 的 Moodle + edstem.org 的 Ed Discussion），再扩展到澳洲八大的其他学校。负责分发到对应平台适配器，维护统一语料 schema（univault.jsonl）与每门课的存储约定。"
version: 0.3.1-beta
---

# UniVault — unified course-platform crawler

One adapter per platform, one unified corpus schema. This skill is the router
and the contract owner; platform skills carry the verified how-to.

**Prerequisite**: the CLI must be installed once with `./install.sh` from the
repository root (or invoked from the plugin's `scripts/` directory). If
`univault` is not on PATH, ask the user to run the installer rather than
guessing a cache path.

University rollout: **Monash first** (Moodle + Ed, the user's 2026 S2 units),
then the remaining Go8 universities (Melbourne, Sydney, UNSW, ANU, UQ, UWA,
Adelaide). Platform adapters stay university-agnostic; a university is just
config: LMS base URL + auth path (e.g. `learning.monash.edu` + Okta SAML).

## Adapter status

| Adapter | Skill | Status |
|---|---|---|
| Ed Discussion | `univault-ed` | **Production** — official API token; resumable thread archiver (`ed-archive`) plus Lessons archiver (`ed-lessons`: content/quiz prompts/PDFs), verified on 3 Monash units |
| Moodle | `univault-moodle` | **Production** — cookie-session auth (`univault login`, `moodle-setup import-cookie/status/probe`), scan/sync, version history, crash recovery; verified against a real Monash account |
| Canvas | — | Planned (user-generated Personal Access Token; API is the most standard of the three) |

Load the platform skill before running any crawl. If the user gives a URL,
detect the platform from it first: `edstem.org` → ed; `*/moodle/*`,
`learning.monash.edu`, `/course/view.php` → moodle; `instructure.com`,
`/courses/{id}` with Canvas assets → canvas.

## Unified corpus schema (univault.jsonl)

Every adapter normalises to one JSON object per item, one per line. This file
is the cross-platform search surface; raw platform payloads stay in each
archive dir alongside it.

```json
{
  "platform": "ed | moodle | canvas",
  "kind": "thread | announcement | page | file | deadline",
  "course": { "code": "UNIT1001", "id": 10001 },
  "id": "platform-native id",
  "number": 12,
  "type": "platform-native subtype (e.g. ed thread type)",
  "title": "...",
  "category": "category / subcategory path (platform-native)",
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601 or null",
  "url": "deeplink to the original item",
  "author": { "name": "...", "staff": false },
  "flags": { "answered": true, "staff_answered": false, "anonymous": false,
             "pinned": false, "locked": false, "private": false, "endorsed": null },
  "counts": { "replies": 3, "views": null, "votes": null },
  "body_md": "main body as markdown/plain text",
  "answers":   [ { "author": {}, "created_at": "...", "endorsed": true,
                   "body_md": "...", "comments": [ { "author": {}, "created_at": "...", "body_md": "..." } ] } ],
  "comments":  [ { "author": {}, "created_at": "...", "body_md": "..." } ]
}
```

Adapters must keep this shape and put anything platform-specific the schema
has no home for into the raw archive, not into the unified record.

## Storage convention

- Platform archives: `<course_root>/memory/resources/<platform>/`
  (e.g. `.../unit1001/memory/resources/ed/` — matches the existing ed-archive
  layout; do not move or rename existing archives).
- Unified corpus: `<course_root>/memory/resources/univault.jsonl`, rebuilt
  from the platform archive dirs after every sync.
- Credentials: `~/.config/univault/<platform>-token` (mode 0600) or env vars.
  Never in git, notes, or shell history.

## Standard sync flow

1. Resolve the course root from the user's layout
   (`$UNIVAULT_LEARNING_ROOT/<term>/<code>/`).
2. Run the platform adapter (its skill has the exact command). Adapters are
   **resumable by design** — re-running only fetches the delta.
3. Rebuild the unified corpus:
   `univault-normalize <course_root>/memory/resources/<platform>... -o <course_root>/memory/resources/univault.jsonl`
   (the script lives in the plugin's `scripts/`, and is installed on PATH at
   `~/.local/bin/univault-normalize`).
4. Report counts per platform/kind and anything new since last sync.

## Cross-platform search recipes

```bash
# staff answers mentioning "kaggle" across the whole corpus
grep -l kaggle <course>/memory/resources/univault.jsonl
jq -r 'select(.platform=="ed" and .flags.staff_answered) | .title' univault.jsonl

# everything new in the last 7 days, all platforms
jq -r --arg since "$(date -v-7d +%Y-%m-%dT%H:%M:%SZ)" \
   'select(.created_at > $since) | "\(.platform) \(.kind) \(.title) \(.url)"' univault.jsonl
```

## Roadmap

- Canvas adapter (PAT → courses / announcements / assignments).
- `univault-deadlines`: merge Moodle calendar events + Canvas assignments +
  Ed assessments into one feed.
- Go8 expansion: repeat the per-university probe → adapter recipe for each
  new university's Moodle/Ed/Canvas deployment.
