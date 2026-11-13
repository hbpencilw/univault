---
name: univault-ed
description: "Ed Discussion (edstem.org) adapter for UniVault. Bulk-archive every thread of an Ed course into resumable local archives (Markdown + JSONL) via the official Ed JSON API with a personal token, and archive Lessons (content slides, quiz prompts, PDF slides) with ed-lessons. Use when the user wants to sync, update, or search Ed discussions, staff answers, announcements, lessons or course materials."
description_zh: "UniVault 的 Ed Discussion（edstem.org）适配器。用个人 token 走 Ed 官方 JSON API，把整门课的讨论帖批量归档为可增量更新的本地语料（Markdown + JSONL），并用 ed-lessons 归档 Lessons（正文 slide、quiz 题干、PDF 课件）。用户想同步/更新/检索 Ed 讨论、老师回答、公告、Lessons 或课程资料时使用。"
version: 0.2.0
---

# UniVault adapter — Ed Discussion (edstem.org)

## Canonical script

`ed-archive` — bundled in this plugin at `scripts/ed-archive` (canonical
source, stdlib-only Python 3). On this machine it is also installed and on
PATH at `~/.local/bin/ed-archive`; prefer the PATH copy, fall back to
locating the plugin's `scripts/ed-archive`.

```bash
ed-archive <CODE> <COURSE_ID> <DEST_DIR> [--delay 1.0] [--list-only]
```

Auth: personal API token from https://edstem.org/settings/api-tokens, read
from `~/.config/edstem-cli/token` (created with mode 0600 by `~/.local/bin/ed-setup`,
which never echoes the token). `EDSTEM_TOKEN` env var also works. If
`ed-setup` has never run, ask the user to create the token and run it — do
not invent storage locations for the token.

## Lessons — `ed-lessons`

```bash
ed-lessons <CODE> <COURSE_ID> <DEST> [--delay 1.0] [--no-files] [--list-only]
```

Archives Ed **Lessons** (the `/courses/{cid}/lessons` pages: Topic 1, Workshops,
Sample Tests, …), read-only:

- `document` slides → Markdown (`lessons.jsonl` + `ed_lessons.md`)
- `quiz` slides → **prompt/passage only** (never attempts or answers)
- `pdf` slides → downloaded from `static.edusercontent.com` into
  `lesson_files/<NN>-<slug>/<SS>-<slug>.pdf` (public URLs, no auth header,
  https + public-IP allowlist only)
- resumable: lesson ids already in `lessons.jsonl` are skipped; existing PDFs
  are not re-fetched; `--list-only` counts without writing; `--no-files`
  records PDF metadata without downloading

`univault sync` runs `ed-lessons` automatically after `ed-archive` for each
unit with a known Ed course id (`--no-lessons` disables it).

Scope guard: archived lesson content is the student-visible material; locked
or hidden lessons are only recorded if the API returns them. No attempt data,
no answers, no submissions.

## Output (written into DEST_DIR)

| File | Contents |
|---|---|
| `ed_discussions.md` | **Main deliverable** — every thread as Markdown: metadata, question body, answers with nested comments, staff flagged |
| `threads_index.md` | one-row-per-thread table with links |
| `threads.jsonl` | raw API payload per thread (one `{thread, users}` object per line) |
| `threads_index.json` | flat list metadata |
| `README.md` | counts, categories, provenance |

**Resumable by design**: thread IDs already in `threads.jsonl` are skipped,
so re-running after new posts fetches only the delta. Safe to run weekly.
`--list-only` indexes without fetching bodies (fast reconnaissance).

The script's own docstring mentions `corpus.md`; the actual filename is
`ed_discussions.md`. Trust this table.

## Resolving Ed course IDs

Run `edstem units --fields id,code,name` and copy the numeric id for the unit
you want. Never guess ids. Example (fictional):

| Unit | Ed course ID |
|---|---|
| UNIT1001 | `10001` |

## Archive destinations (established convention)

Archives live inside the course's own memory workspace, never the course root:
`<learning_root>/<term>/<code>/memory/resources/ed/`. Pass an **absolute**
`DEST_DIR`; `ed-archive` rewrites the "update command" line in the
`ed_discussions.md` header with that exact path — a stale path there means the
archive was moved by hand, so fix the header line.

Each course's `memory/AGENTS.md` is read-only by default — registering the
resource in `memory/index.md` / `memory/state.md` requires a draft first and
explicit user approval.

## CRITICAL — pagination

The `edstem` CLI's `--max` does NOT paginate: `threads list <unit> --max 2000`
stops at the API's hard page cap of 100 (observed silent truncation — a course
reported exactly 100 with a much higher max thread number). **Never trust
`--max` for a full census.**
Paginate manually (`ed-archive` already does):

```
GET /api/courses/{cid}/threads?limit=100&offset=0&sort=new
GET /api/courses/{cid}/threads?limit=100&offset=100&sort=new   # next page
... until a batch returns fewer than 100 rows
```

A batch of exactly 100 means another page exists.

## Raw API shapes

| Endpoint | Returns |
|---|---|
| `GET /api/courses/{cid}/threads?limit=&offset=&sort=` | `{sort_key, threads[], users[]}` — list objects carry `content` + `document` but no replies |
| `GET /api/threads/{tid}` | `{thread, users[]}` — full object: `answers[]`, `comments[]`, `is_answered`, `is_staff_answered`, `is_anonymous`, counters |

Canonical API base is `https://edstem.org/api/` — no `/au/`, no `/us/`
region prefix (that's a frontend path, returns HTML). Web deeplink format is
`https://edstem.org/au/courses/{cid}/discussion/{tid}`.

- `thread.document` is plain text; `thread.content` is Ed's
  `<document version="2.0">` XML. Prefer `document`.
- `answer` objects carry nested `comments[]` and `is_endorsed` (staff-endorsed).
- `users[]` is per-response; merge across records to resolve author names.
  Staff check: `course_role` in `{admin, staff, teacher, instructor, ta,
  moderator}` — Ed reports teaching staff as `admin`.
- Thread `type` distinguishes questions from posts/announcements.

## `edstem` CLI notes (secondary path)

```bash
edstem units --fields id,code,name
edstem threads <unit> --max 100 --fields id,title,created_at
edstem threads read <unit>#<number>
```

Token from `EDSTEM_TOKEN` or `~/.config/edstem-cli/token` only; no
`edstem login` subcommand. Installed at `~/.local/bin/edstem` (v0.5.0).
Config defaults: 1.0 s request delay, 3 retries. Ed's API is in beta — keep
delays ≥1 s, no parallel hammering; a revoked token costs the user a re-issue.
`--json`/`--yaml`/`--table`, `--fields`, `--output`, `--dry-run` available.

## FALLBACK — cookies + SPA rendering (legacy, only if tokens disabled)

Ed is a Vue SPA; static GET returns a ~13KB shell. If the institution
disables API tokens: export browser cookies to a JSON array, render with
`agent-browser` (`wait --load load`, then `sleep 3` — networkidle hangs on
Ed's WebSocket), verify `document.title` reads `"<UNIT> – Ed Discussion"`,
snapshot each thread, parse with cheerio. DOM markers: `.disthrb-title`,
`.amber-paragraph.amber-content`, `.disthrb-user-name`, `time[datetime]`,
`.disthrb-gutter-btn.accepted.active`. Do not restart cookie extraction
investigations (Safari binary cookies, Chrome LevelDB, Playwright encryption
all dead-ends, observed Aug 2026) without first confirming tokens are
unavailable.

## Scope guard

- Only crawl when the source is actually an Ed Discussion course/thread.
- If the user supplies local course files (PDFs, slides, scripts, notes),
  inventory those locally first; don't re-crawl Ed or expose credentials.
- Keep course-specific claims tied to the supplied course ID; the verified
  probes here are reusable methods, not evidence about other units.
- After archiving, rebuild the unified corpus with `univault-normalize`
  (see the `univault` skill).

## Safety

- An Ed API token grants full access to the user's Ed account — treat it as
  a password. Never commit, print, or log it.
- User must be the account owner.
- Avoid the hosted MCP at `https://edstem.tuuhub.com/mcp` — it sends the
  token to a third-party server.
