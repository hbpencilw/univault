# Agent integration

UniVault is **CLI-first**: any agent that can run shell commands can use it.
Instruction files only tell the agent where to look; they never change the
security model.

## Entry points per agent

| Agent | Entry file | Setup |
|---|---|---|
| ZCode | plugin root (`.zcode-plugin/plugin.json`, `skills/`) | add `https://github.com/hbpencilw/univault` (or the local repo path) as a marketplace, install UniVault |
| Claude Code | `CLAUDE.md` | read the repo; optionally copy skills into `.claude/skills/` |
| Cursor | `.cursor/rules/univault.mdc` | open the repo; the rule is `alwaysApply` |
| Gemini CLI | `GEMINI.md` | open the repo |
| OpenAI Codex / OpenCode / Amp / Jules | `AGENTS.md` | open the repo |
| Aider | `CONVENTIONS.md` | `aider --read CONVENTIONS.md` or `/read CONVENTIONS.md` |
| anything else | `AGENTS.md` | `./install.sh`, then call the CLIs |

## Native skills (optional)

The `skills/*/SKILL.md` files follow the portable Agent Skill layout. Claude
Code and compatible agents can discover them if you copy them into the agent's
skills directory:

```bash
mkdir -p .claude/skills
cp -R skills/univault skills/univault-ed skills/univault-moodle .claude/skills/
```

No code changes are required; the scripts remain the source of truth.

## Why no MCP server (yet)

The CLI already exposes everything an agent needs: JSON output (`--json`),
stable exit codes, resumable archives and a documented JSONL schema. A future
MCP layer can wrap the same engine, but it is not required for compatibility.

## Minimal contract an agent must respect

1. `scan` never downloads.
2. `sync` runs only on an explicit user request, scoped to selected courses or
   resources.
3. Remote platforms are read-only; stealth content is not bypassed.
4. Credentials are never printed, copied or committed.
5. Real downloads are never used to answer a documentation question.
