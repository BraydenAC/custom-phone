---
paths:
  - ".claude/**"
  - "CLAUDE.md"
  - "docs/**"
  - "README.md"
  - "ROADMAP.md"
  - ".gitignore"
connects: [cli, phone-link, tests]
---

# The agent harness and docs

## Features

- `CLAUDE.md`: the project's map, its hard rules, and how each kind of change reaches the
  phone.
- `.claude/settings.json`: permissions (allow, ask, deny) and the hooks.
- `.claude/hooks/guard.py` (PreToolUse) blocks or asks for destructive or private-data
  commands. `.claude/hooks/run-tests.py` (PostToolUse) runs pytest after code edits.
- `.claude/skills/`: workflow guides (`deploy-to-phone`, `phone-service`,
  `kernel-and-hardware`, `battery-measure`).
- `.claude/agents/log-investigator.md`: a Haiku subagent for reading logs.
- `.claude/rules/`: one file per area of the code, loaded for matching paths. Each lists
  features, connections (`connects:`) and coding notes.
- `docs/architecture.md` and `docs/images/*.svg`: the diagrams. `README.md` holds the
  user-facing documentation, and `.gitignore` keeps private and generated files out of git.

## Connected to

- cli: command names appear in the permission rules and guard patterns. Renamed commands
  need updates here.
- phone-link: one session at a time, and how `flip phone run` takes its arguments, are
  assumptions in the guard, the skills and the agent.
- tests: `test_harness.py` checks all of the harness.

## Coding notes

- **Everything here is public:** the repo is open source. No personal paths, names, device
  identifiers or machine-specific settings. Personal overrides go in
  `.claude/settings.local.json`, which is gitignored.
- **Don't duplicate:** each fact lives in one place.
  - User-facing behaviour belongs in the README.
  - Workflows belong in skills.
  - Area-specific coding notes belong in rules.
  - `CLAUDE.md` holds only what every session needs.
  - Link between them instead of copying.
- **Hooks use the standard library only**, must finish quickly, and must fail open on bad
  input (no output means the normal permissions apply). Every guard pattern gets a test case
  for what it should catch and what it should let through.
- **A new area of code needs a rule file** with `paths:` and `connects:`. `test_harness.py`
  checks that every tracked source file is covered and that every connection names an
  existing rule.
- **`ROADMAP.md`** has the vision and deferred work. It never starts work by itself. When
  something is put off, add it there with why. When an item is picked up or dropped,
  remove it.
- **`.gitignore`** must keep listing `/backups/`, `/out/`, `*.pem` and
  `.claude/settings.local.json`. Removing one risks committing private data.
- **Diagrams:** the SVGs are standalone, using the light palette with a dark-mode media
  query. Update them when a box or arrow in them changes.
