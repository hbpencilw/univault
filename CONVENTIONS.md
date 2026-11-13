# CONVENTIONS.md

Aider / generic coding-agent conventions. The universal briefing is
**[AGENTS.md](AGENTS.md)** — read it first.

- UniVault is CLI-first: `univault`, `moodle-archive`, `moodle-setup`,
  `ed-archive`, `ed-lessons`, `univault-normalize`.
- Install with `./install.sh`; libraries go to `~/.local/lib/univault`.
- Safety: read-only remote, no stealth bypass, never print credentials,
  `scan` never downloads, `sync` only on explicit request.
- Tests are offline: `python3 -m unittest discover -s tests/moodle`.
