# GEMINI.md

Project instructions for Gemini CLI and compatible agents. The universal
briefing is **[AGENTS.md](AGENTS.md)** — read it first.

- Install: `./install.sh`, then ensure `~/.local/bin` is on `PATH`.
- `scan` never downloads; `sync` only on an explicit user request.
- Never read, print, or commit `~/.config/univault/moodle-cookie` or Ed tokens.
- Remote platforms are read-only; no posts, submissions, or stealth bypass.
- For platform detail read `skills/univault-ed/SKILL.md` or
  `skills/univault-moodle/SKILL.md`.

Useful entry points:

```bash
univault status
univault sync --dry-run
moodle-archive scan --dest ~/courses/_scan --learning-root ~/courses --json
```
