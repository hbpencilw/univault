# Architecture

UniVault archives course-platform content into a local, versioned corpus that
AI agents and humans can search offline. It is a ZCode plugin, but every
component is a plain Python CLI.

## Adapters

| Platform | Entry points | State |
|---|---|---|
| Ed Discussion | `ed-archive` (threads), `ed-lessons` (Lessons) | production |
| Moodle | `moodle-setup` (session), `moodle-archive` (scan/sync) | beta, verified against Monash Moodle |
| Canvas | — | planned |

Each adapter writes into a course's own workspace:

```
<course_root>/memory/resources/<platform>/
```

and the unified corpus lives beside it as `memory/resources/univault.jsonl`
(rebuilt by `univault-normalize`).

## Unified record schema

One JSON object per line, platform-agnostic:

```json
{
  "platform": "ed | moodle",
  "kind": "thread | announcement | lesson | page | file | deadline",
  "course": {"code": "UNIT1001", "id": 10001},
  "id": "stable platform id",
  "title": "...", "category": "...",
  "created_at": "...", "updated_at": "...",
  "url": "...",
  "author": {"name": "...", "staff": false},
  "flags": {}, "counts": {},
  "body_md": null,
  "content_hash": "sha256:...", "version_id": "...",
  "is_current": true,
  "archive_status": "archived | metadata-only",
  "relative_path": "...",
  "extra": {}
}
```

`univault-normalize` deduplicates on `(platform, course.id, id)` and can merge
archives from several platforms into one corpus.

## Scan / sync separation

- `moodle-archive scan` discovers courses, sections, activities, file links
  and announcement threads. It never downloads file bodies; sizes are probed
  with HEAD (CDN redirects followed without the session cookie).
- `moodle-archive sync` archives only what the user explicitly selects
  (`--course-id`, `--cmid`, `--discussion-id`) or the whole course when
  requested. It writes files, pages, labels, notices and versions.
- `univault sync` runs Ed threads, Ed Lessons and Moodle sync, then rebuilds
  `univault.jsonl`.

## Storage and versioning

```
moodle.jsonl                 current records (true source for normalize)
course.json                  metadata + last sync stats
resources/                   current files
pages/                       current page HTML (+ assets)
sections/<n>/labels/         label HTML (+ assets)
announcements/<did>/         posts + attachments
_history/<logical_id>/<sha>.<ext>   permanent previous versions
_state/journal.jsonl         begin/done markers for crash recovery
_state/staging/              download staging (cleared on start)
_reports/sync-*.md|json      per-run report
```

A file is staged, hashed (SHA-256), fsynced and atomically renamed. Before
replacing a current file the old one is copied into `_history/`. `moodle.jsonl`
is rewritten atomically and only contains current records. Journal recovery
handles both crash points: a missing current file is restored from history;
a completed replace whose JSONL write was lost is adopted from the journal.

## Security invariants

- Remote access is **read-only**: no forum posts, submissions or profile
  changes. No hidden/stealth content is bypassed.
- Credentials are the browser session exported by the user (or captured by
  the optional Playwright login). Cookie file `~/.config/univault/moodle-cookie`
  is `0600`, directory `0700`, atomic writes. No password is stored and MFA is
  never automated.
- Requests are HTTPS only, to allowlisted hosts, with literal private/loopback
  addresses rejected and DNS answers required to be public. Redirects are
  re-validated per hop; the Cookie header is never sent to a CDN host.
- Archived HTML is sanitised: scripts, event handlers and `token`/`sesskey`
  query parameters are removed. Signed CDN URLs are never persisted.
- `scan` performs no downloads; `sync` only runs when explicitly invoked.

## Repository layout

```
.zcode-plugin/plugin.json   manifest (name: univault)
marketplace.json            local development catalogue
skills/                     router + Ed + Moodle skills
scripts/                    CLIs and lib/ (stdlib only)
tests/                      offline unit tests
docs/                       architecture and third-party notes
references/                 third-party projects and dev notes (git-ignored)
```
