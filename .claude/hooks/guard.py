#!/usr/bin/env python3
"""PreToolUse guard for Claude Code in this repo (see CLAUDE.md, "Hard rules").

Reads the tool call as JSON on stdin and answers:
  deny: never (catastrophic deletes, raw disk writes, reading private data);
  ask:  only with the user's say-so (flashing, writing cards, sudo, history
        rewrites, mass deletes, a second phone session, powering off);
  nothing: the normal permission rules in .claude/settings.json apply.

Permission rules match command prefixes; this also sees commands wrapped in
`sh -c`, `flip phone run -- ...` and pipelines. Stdlib only.
"""

import json
import os
import re
import shlex
import subprocess
import sys

PRIVATE = r"(\bbackups\b|\.pem\b|sd\.img\b|\.config/flip\b|linux-first-recovery\.img)"
READERS = (r"\b(cat|less|more|head|tail|xxd|hexdump|od|strings|base64|cp|scp|rsync|"
           r"tar|zip|curl|wget|nc|grep|rg|awk|sed|python3?|bat|vim?|nano)\b")

# Where a command starts: line start, after ; & | ( or `, after sh -c ', or as
# the command of xargs/env/flip phone run.
CMD = r"(^\s*|[;&|(`]\s*|\b(xargs|exec|env|nohup|time)\s+|-c\s+['\"]\s*|\brun\s+(--\s+)?)"

# Disks: wholesale writes go through flip, which checks the target (and refuses
# the eMMC). On the phone, /dev/mmcblk0 is the eMMC with the IMEI on it.
DISK_TOOLS = r"\b(mkfs(\.\w+)?|wipefs|blkdiscard|sfdisk|fdisk|parted|sgdisk|mkswap)\b"

DENY = [
    (r"\bdd\b[^|;&]*\bof=/dev/", "raw disk write; use `flip sd write` (it checks the "
     "card), or run it yourself"),
    (DISK_TOOLS + r"[^|;&]*/dev/", "partitions or formats a disk; run it yourself"),
    (r"\b(curl|wget)\b[^|;]*\|\s*(sudo\s+)?(ba|z|da)?sh\b", "pipes a download into a shell"),
    (r"\bchmod\s+(-\w*R\w*\s+)?0?777\s+/(\s|$)", "opens up the whole file system"),
    (r"\bgit\s+push\b.*(--force\b|\s-f\b|--force-with-lease\b|\s\+).*\b(main|master)\b",
     "force-push to main"),
    (r"\bgit\s+push\b.*\b(main|master)\b.*(--force\b|\s-f\b)", "force-push to main"),
]

ASK = [
    (r"\bflip\s+flash\b", "flashes the phone (you run this, or say so)"),
    (r"\bflip\s+sd\s+(write|update)\b", "writes the SD card (you run this, or say so)"),
    (r"\bflip\s+backup\s+(create|adopt-vbmeta)\b", "backup / changes which vbmeta builds use"),
    (r"\bflip\s+keys\s+export\b", "copies the private signing key"),
    (r"\bflip\s+phone\s+(trust\s+(add|remove)|setup-host|pin)\b",
     "changes which computers the phone trusts"),
    (r"\bflip\s+clean\b", "deletes build outputs"),
    (CMD + r"sudo\b", "sudo"),
    (r"\bgit\s+push\b", "publishes commits"),
    (r"\bgit\s+(reset\s+--hard|clean\s+-\w*f\w*|rebase|filter-branch|filter-repo)\b",
     "rewrites or discards git history / work"),
    (r"\bgit\s+(checkout|restore)\s+(--\s+)?\.(\s|$)", "discards uncommitted changes"),
    (r"\bgit\s+restore\s+(?!--staged\s)\S|\bgit\s+checkout\s+(\S+\s+)?--\s+\S",
     "discards uncommitted changes to files"),
    (r"\bgit\s+(branch\s+-D|stash\s+(drop|clear))\b", "deletes a branch or stash"),
    (r"\bgit\s+add\b.*\s(-f|--force)\b", "force-adds an ignored (likely private) file"),
    (r"\bfind\b.*\s(-delete|-exec\s+rm)\b", "mass delete"),
    (CMD + r"(systemctl\s+)?(poweroff|shutdown|halt)\b", "powering off while USB is plugged in has crashed "
     "this phone: unplug first"),
    (CMD + r"(systemctl\s+)?reboot\b", "restarts a machine"),
]

CATASTROPHIC = {"/", "/*", "~", "~/", "~/*", "$HOME", "$HOME/", "${HOME}", ".", "./",
                "..", "../", "*", ".*", ".git", "./.git"}


def rm_verdict(command):
    """('deny'|'ask', reason) for rm calls that delete a lot, else None."""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    verdict = None
    for i, tok in enumerate(tokens):
        if tok.rsplit("/", 1)[-1] != "rm":
            continue
        flags, targets = "", []
        for t in tokens[i + 1:]:
            if t in (";", "&&", "||", "|") or t.endswith(";"):
                break
            if t.startswith("-") and t != "-":
                flags += t
            else:
                targets.append(t)
        recursive = bool(re.search(r"(^|[^-])-\w*[rR]|--recursive", flags))
        for t in targets:
            bare = t.rstrip("/") or "/"
            if t in CATASTROPHIC or (t.startswith("/") and bare.count("/") <= 1) \
                    or re.match(r"\.?/?backups(/|$)", t):
                return "deny", f"rm {t}: catastrophic or private-data delete"
        if recursive or len(targets) >= 5 or any(c in "".join(targets) for c in "*?"):
            verdict = ("ask", "mass delete (rm -r, globs or many files)")
    return verdict


def other_phone_session():
    """Only one flip-link session works at a time."""
    try:
        out = subprocess.run(["pgrep", "-af", r"flip (phone|battery|sd (write|update))"],
                             capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return any("guard.py" not in line and "pgrep" not in line for line in out.splitlines())


HEREDOC = re.compile(r"([^\n]*<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n)(.*?\n)\2(\n|$)", re.S)
HEREDOC_MARK = re.compile(r"<<-?\s*['\"]?\w+['\"]?")
# What may stand on the heredoc's line (marker removed) for its text to be
# skipped: a command ending in `cat > FILE` or `tee [-a] FILE`, nothing after.
TO_FILE = re.compile(r"(^|&&|;)\s*(cat\s*>>?\s*\S+|tee\s+(-a\s+)?\S+)\s*$")


def without_file_heredocs(command):
    """Drop the text of heredocs that only go into a file (cat > f <<EOF ...
    EOF, tee f <<EOF): writing text that mentions rm -rf or a private path
    does neither. A heredoc fed to anything else (sh, python3, a pipe) is
    code, and stays checked."""
    def strip(m):
        opener = HEREDOC_MARK.sub("", m.group(1)).rstrip()
        if "|" in opener or not TO_FILE.search(opener):
            return m.group(0)
        return m.group(1) + m.group(2) + m.group(4)
    return HEREDOC.sub(strip, command)


def current_branch(cwd):
    try:
        return subprocess.run(["git", "-C", cwd or ".", "rev-parse", "--abbrev-ref", "HEAD"],
                              capture_output=True, text=True, timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


# The script of `sh -c '...'` (and bash/dash/zsh): checked as a command of its own,
# since shlex keeps it as one token.
WRAPPED = re.compile(r"\b(?:sh|bash|dash|zsh)\s+(?:-[a-z]+\s+)*-c\s+(['\"])(.*?)(?<!\\)\1", re.S)
STRICTNESS = {None: 0, "ask": 1, "deny": 2}

# Limits (a regex guard can't parse shell): eval, $(...) building a command name,
# variables holding paths, and scripts written to a file and run later all get past
# it. They still hit the normal permission prompt unless an allow rule matches.


def check_bash(command, cwd=None):
    command = without_file_heredocs(command)
    verdict = _check_bash(command)
    for _, inner in WRAPPED.findall(command):
        v = _check_bash(inner)
        if v and STRICTNESS[v[0]] > STRICTNESS[verdict[0] if verdict else None]:
            verdict = v
    if verdict is None:
        m = re.search(r"\bgit\s+(?:-C\s+(\S+)\s+)?(commit|merge|cherry-pick|revert)\b", command)
        if m:
            where = os.path.join(cwd or ".", m.group(1).strip("'\"")) if m.group(1) else cwd
            if current_branch(where) in ("main", "master"):
                # Work happens on feature and chunk branches; main changes by PR.
                return "ask", "commits or merges directly on main (work on a feature branch)"
    return verdict


def _check_bash(command):
    for pattern, reason in DENY:
        if re.search(pattern, command):
            return "deny", reason
    if re.search(PRIVATE, command) and not re.match(r"\s*(uv run )?flip\b", command):
        # A reader must be a command, not a word in a message (git commit -m '... more').
        if re.search(CMD + READERS, command):
            return "deny", "reads or copies private data (backups, keys, sd.img)"
        if re.search(r"(>>?|\btee\s+(-a\s+)?)\s*['\"]?[^\s'\";|&]*" + PRIVATE, command):
            return "deny", "writes into private data (backups, keys, sd.img)"
    rm = rm_verdict(command)
    if rm and rm[0] == "deny":
        return rm
    for pattern, reason in ASK:
        if re.search(pattern, command):
            return "ask", reason
    if rm:
        return rm
    if re.search(r"\bflip\s+(phone|battery)\b", command) and other_phone_session():
        return "ask", "another flip phone session is running (only one works at a time)"
    return None


def check_path(path):
    if path and re.search(PRIVATE, path):
        return "deny", "private data (backups, keys, sd.img): not for reading or editing"
    return None


def decide(call):
    tool, args = call.get("tool_name", ""), call.get("tool_input") or {}
    if tool == "Bash":
        return check_bash(args.get("command", ""), call.get("cwd"))
    if tool in ("Read", "Edit", "Write", "NotebookEdit"):
        return check_path(args.get("file_path") or args.get("notebook_path"))
    if tool in ("Grep", "Glob"):
        return check_path(args.get("path")) or check_path(args.get("pattern") if tool == "Glob" else None)
    return None


def main():
    try:
        call = json.load(sys.stdin)
    except ValueError:
        return 0
    verdict = decide(call)
    if verdict:
        decision, reason = verdict
        json.dump({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": f"flip guard: {reason}",
        }}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
