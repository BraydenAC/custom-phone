#!/usr/bin/env python3
"""PostToolUse hook: run the test suite after Claude edits code in this repo.

Silent when the tests pass (or the edited file isn't code the tests cover).
On a failure it hands the failing tests back to Claude, which then fixes them
or, mid-way through a multi-file change, carries on knowing they're red.
Stdlib only.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

# What the tests cover: the CLI, the phone-side files, kernel/lk2nd inputs,
# the harness itself, and the tests.
WATCHED = ("src/", "sd/", "tests/", "patches/", "lk2nd-dts-msm8952/", "rootfs/",
           "toolbox/", ".claude/", "defconfig", "pins.toml", "pyproject.toml", "CLAUDE.md")
TIMEOUT = 300
TAIL = 40  # lines of pytest output handed back on failure


def watched(path, repo):
    try:
        rel = Path(path).resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return False  # outside the repo
    return any(rel == w or rel.startswith(w) for w in WATCHED) and "__pycache__" not in rel


def run_tests(repo):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")  # nothing lands in sd/overlay
    try:
        p = subprocess.run(["uv", "run", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider"],
                           cwd=repo, env=env, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, f"pytest took longer than {TIMEOUT} s"
    except OSError as e:
        return True, f"could not run pytest: {e}"  # don't block on a missing uv
    return p.returncode == 0, (p.stdout + p.stderr).strip()


def checkout_of(path):
    """The checkout (main repo or a git worktree) holding `path`: the nearest
    parent with a .git directory or .git file. Chunk agents edit in their own
    worktrees, so test there, not in $CLAUDE_PROJECT_DIR."""
    for d in Path(path).resolve().parents:
        if (d / ".git").exists():
            return d
    return None


def main():
    try:
        call = json.load(sys.stdin)
    except ValueError:
        return 0
    args = call.get("tool_input") or {}
    path = args.get("file_path") or args.get("notebook_path")
    repo = checkout_of(path) if path else None
    if repo is None or not (repo / "pyproject.toml").exists():
        repo = Path(os.environ.get("CLAUDE_PROJECT_DIR") or call.get("cwd") or ".")
    if not path or not watched(path, repo):
        return 0
    ok, output = run_tests(repo)
    if ok:
        return 0
    tail = "\n".join(output.splitlines()[-TAIL:])
    json.dump({"decision": "block",
               "reason": f"Tests fail after editing {path} (uv run pytest -x):\n{tail}"},
              sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
