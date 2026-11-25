---
paths:
  - "src/flip/cli.py"
  - "src/flip/util.py"
  - "src/flip/__init__.py"
  - "src/flip/__main__.py"
  - "pyproject.toml"
connects: [config-and-profile, build-and-pins, signed-images, flash-backup-device, sd-image, phone-link, battery, tests, harness]
---

# CLI shell (`cli.py`, `util.py`)

## Features

- `cli.py` wires every command group with Typer: `build`, `flash`, `clean`, `doctor`, `backup`,
  `keys`, `device`, `sd`, `phone` (+ `phone trust`), `battery` (+ `battery note`).
- `util.py` has the helpers every module uses:
  - `FlipError` for expected failures, printed without a traceback;
  - `say`/`warn` for output;
  - `run`, which echoes the command and raises `FlipError` on failure;
  - `sha256_file`/`sha256_bytes`;
  - `write_private` (mode 600).
- `pyproject.toml` declares the console script, the dependencies (typer, cryptography) and the
  pytest settings.

## Connected to

- Every module: `cli.py` only parses arguments and calls into them. A renamed or re-signatured
  module function breaks the command that calls it.
- `README.md` "Commands" table: user-facing commands and options are documented there.
- `.claude/settings.json` and `.claude/hooks/guard.py` match commands by name (for example
  `uv run flip sd write`). Renaming a command or subcommand means updating the allow/ask
  rules and the guard patterns.

## Coding notes

- Keep logic out of `cli.py`. Commands are thin: arguments, then one call into a module, then
  output. Put new logic in the module, where tests can reach it without Typer.
- Commands that change the phone or a card take `--yes` and confirm through `asker(yes)`. A
  module function receives `confirm` as a callable and asks before anything destructive.
- Raise `FlipError("plain sentence")` for anything the user can fix. Let real bugs raise
  normally, so they show a traceback.
- Help texts are one plain sentence and say what the command does to the phone (see the
  existing ones).
- A new dependency in `pyproject.toml` must also work on the build computers. Anything that
  runs on the phone can't use it (see phone-link).
