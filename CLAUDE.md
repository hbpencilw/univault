# CLAUDE.md

Project instructions for Claude Code (and compatible agents). The universal
briefing is **[AGENTS.md](AGENTS.md)** — read it first.

Quick summary:

- UniVault is a set of Python CLIs; install with `./install.sh`.
- `scan` discovers only; `sync` downloads only when the user explicitly asks.
- Never print or commit `~/.config/univault/moodle-cookie` or Ed tokens.
- Remote platforms are read-only; no posts, submissions, or stealth bypass.
- Skills live in `skills/univault*/SKILL.md` — read the relevant one for a
  platform before crawling.

Typical first commands:

```bash
univault status
univault sync --dry-run
moodle-archive report --dest ~/courses/_scan --json
```
