# AGENTS.md — UniVault

UniVault works with **any agent** through its command-line tools. No MCP server
is required. This file is the universal entry point; agent-specific files
(`CLAUDE.md`, `GEMINI.md`, `.cursor/rules/`, `CONVENTIONS.md`) only add
platform notes.

## What this repository is

A ZCode plugin **and** a set of portable Python CLIs that archive:

- **Ed Discussion** — threads (`ed-archive`) and Lessons (`ed-lessons`)
- **Moodle** — announcements, files, pages, labels (`moodle-archive`)
- a unified corpus (`univault-normalize` → `univault.jsonl`)

## Safety rules (non-negotiable)

1. **Remote is read-only.** Never post, submit, or change anything on Ed or
   Moodle. Never bypass hidden/stealth content.
2. **Never read, print, copy, or commit** `~/.config/univault/moodle-cookie`
   or `~/.config/edstem-cli/token`. Treat them as passwords.
3. **`scan` never downloads.** `sync` only when the user explicitly asks, and
   only for the selected course/resource (`--course-id`, `--cmid`,
   `--discussion-id`). Do not start a whole-course or all-course download on
   your own initiative.
4. **No network calls for documentation tasks.** Do not run real crawls to
   answer questions that the local archives can answer.
5. Do not modify Moodle/Ed content to test versioning. History is tested
   offline; real changes are only observed when the platform changes them.

## Setup

```bash
./install.sh            # CLIs -> ~/.local/bin, lib -> ~/.local/lib/univault
export PATH="$HOME/.local/bin:$PATH"
```

Ed needs a personal API token (`~/.config/edstem-cli/token`). Moodle needs a
browser session: `univault login` (Edge window) or
`moodle-setup import-cookie --from-clipboard`.

## Command surface

| Command | Purpose |
|---|---|
| `univault sync --dry-run` | plan Ed + Moodle for every course (no downloads) |
| `univault sync --course CODE` | archive one unit, then rebuild its corpus |
| `univault status` | Ed token, Moodle session, scan manifest |
| `moodle-archive scan --dest DIR --course-id ID --learning-root ROOT` | discover only; `--json` available |
| `moodle-archive sync --from-scan MANIFEST [--course-id ID] [--cmid N] [--discussion-id N]` | explicit archive |
| `moodle-archive report --dest DIR [--json]` | latest scan report |
| `moodle-setup status` / `probe` | session check / read-only capability matrix |
| `ed-archive CODE ED_COURSE_ID DEST` | Ed threads |
| `ed-lessons CODE ED_COURSE_ID DEST` | Ed Lessons (content, quiz prompts, PDFs) |
| `univault-normalize <dir>... -o univault.jsonl` | merge to one corpus |

Exit codes: `0` ok · `1` usage · `2` auth expired · `3` network/blocked ·
`4` partial.

## Standard workflows

**Discover a course** (safe, no downloads):

```bash
moodle-archive scan --dest ~/courses/_scan --learning-root ~/courses --json
```

**Archive a specific resource** (only after the user asks):

```bash
moodle-archive sync --from-scan ~/courses/_scan/_state/scan-latest.json \
  --course-id <course-id> --cmid <cmid>
```

**Verify results**: read `<course>/memory/resources/moodle/moodle.jsonl`,
compare each `version_id` with `shasum -a 256` of `relative_path`, and inspect
`_reports/sync-*.md`. History lives in `_history/`.

**Search the corpus**:

```bash
jq -r 'select(.kind=="announcement") | .title' <course>/memory/resources/univault.jsonl
grep -i "topic" <course>/memory/resources/univault.jsonl
```

## Where data goes

Archives are written under the user's study workspace
(`learning_root`, default `~/courses`), never inside this repository:
`<course>/memory/resources/{ed,moodle,univault.jsonl}`.

## If something fails

- `AUTH_EXPIRED` → ask the user to run `univault login` (or
  `moodle-setup import-cookie`).
- No Ed token → user runs `ed-setup`.
- Do not retry real downloads in a loop; report the error category and stop.
