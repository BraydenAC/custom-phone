# flip: Linux on the Nokia 2780 Flip

Debian 13 on a QM215 flip phone (512 MB RAM) that will be run mostly by an
agent with speech in and out. This repo is the `flip` CLI that builds,
flashes, backs up and administers it, plus everything that goes on the
phone. This file is the development harness: it covers coding on this repo,
not the phone's own agent, which will get a separate harness.

The map: [docs/architecture.md](docs/architecture.md). Commands and user-facing
behaviour: [README.md](README.md). Vision and deferred work:
[ROADMAP.md](ROADMAP.md). Suggest from it when the user asks what's next,
but never start work from it unprompted. Add anything that gets put off.

## Layout

| Path | What |
|---|---|
| `src/flip/` | The CLI (Python, Typer, run with `uv run flip ...`). `cli.py` wires the commands, and the modules do the work: `build`, `flash`, `sd`, `phone`, `backup`, `battery`, ... |
| `tests/` | pytest, including tests that load or run the phone-side scripts against fake sysfs |
| `sd/overlay/` | Files copied into the phone's root file system: programs in `usr/lib/flip/`, units in `etc/systemd/system/`, settings in `etc/flip/` |
| `sd/setup.sh` | Runs inside the new system during `flip sd build`: users, enabled services |
| `patches/linux/`, `defconfig` | Kernel changes, applied to the pinned source on every build |
| `patches/lk2nd/` | Bootloader changes |
| `pins.toml`, `uv.lock` | Every external input, pinned by commit or SHA-256 |
| `linux/`, `lk2nd/`, `out/`, `cache/` | Fetched checkouts and build outputs (gitignored; `linux/` is reset on every build) |

## Commands

```sh
uv run pytest                 # all tests; run after every change
uv run flip build             # kernel, lk2nd, toolbox, signed images -> out/
uv run flip phone run -- CMD  # root command on the phone (over the USB link)
uv run flip phone push LOCAL REMOTE / pull REMOTE LOCAL
uv run flip battery           # battery report from the phone's log
```

`flip phone run` takes the command as separate arguments. Use
`flip phone run -- sh -c '...; ...'` for a shell line, because a single
quoted string is treated as a program name. Only one `flip phone` session
works at a time.

## Areas and change impact

`.claude/rules/` has one file per area of the code. Each file has the area's
features, the areas it's connected to and coding notes that aren't in the
skills. A rule loads by itself when you work on files matching its `paths:`.

Before finishing a change, trace its impact: read the `connects:` list of
each area you touched, and check whether those areas need matching changes
(a sysfs node renamed in a kernel patch breaks `phone-overlay`, a protocol
change in `phone-link` needs a new card, and so on).

| Rule | Area |
|---|---|
| `cli` | `cli.py`, `util.py`, `pyproject.toml` |
| `config-and-profile` | Paths, settings, device facts (wide blast radius) |
| `build-and-pins` | `flip build`, `pins.toml`, verified fetches |
| `signed-images` | Image tools, initramfs, the AVB key |
| `flash-backup-device` | `flip flash`, backups, fastboot |
| `toolbox-rootfs` | The rescue toolbox (`rootfs/init`, `reboot-mode`) |
| `lk2nd` | Bootloader patches and device tree |
| `kernel` | Kernel patches and `defconfig` |
| `sd-image` | `flip sd build/write/update`, `sd/setup.sh` |
| `phone-link` | flip-link client and flipd (runs on the phone) |
| `phone-overlay` | Everything in `sd/overlay/` |
| `battery` | Battery logger and report |
| `tests` | The test suite |
| `harness` | `.claude/`, `CLAUDE.md`, docs, README |

## How a change reaches the phone

| Change | Path to the phone | Skill |
|---|---|---|
| A file in `sd/overlay/` | `flip phone push` + set owner/mode + restart the unit, live; new cards get it from `flip sd build` | `deploy-to-phone` |
| New program, service, setting or Debian package | Overlay + `setup.sh` + test, then deploy as above | `phone-service` |
| Kernel, device tree, defconfig, lk2nd | Patch, `flip build`, then **the user** runs `flip sd update` (kernel) or `flip flash` (lk2nd) | `kernel-and-hardware` |
| Anything that might cost battery | Before/after measurement with `flip battery` | `battery-measure` |

Diagnosing the running phone (journal, sysfs, waking from sleep) is in
`deploy-to-phone`. Bringing up new hardware (modem, calls, sensors) is in
`kernel-and-hardware`.

**Reviewing:** the `pr-reviewer` subagent (Sonnet, read-only, posts
nothing) reviews a finished branch against its objective. See step 7 of
`feature-workflow`.

**Reading logs:** hand log digging to the `log-investigator` subagent
(`.claude/agents/log-investigator.md`, Haiku, read-only). Use it for the
phone's journal and dmesg, `/var/log/flip`, `flip battery` runs, and long
build or test output. It returns a short answer with the key lines quoted, so
whole logs stay out of the main conversation. It uses the phone link: don't
run `flip phone` commands while it works.

## Hard rules

The guard (`.claude/hooks/guard.py`, wired in `.claude/settings.json`)
enforces most of these. Treat them as rules anyway.

- **Private data never leaves the machine and is never committed:**
  - `backups/` holds the IMEI, radio calibration and serial.
  - `out/sd/sd.img` and `out/linux-first-recovery.img` contain the firmware and the phone's identity key.
  - `~/.config/flip/` and `*.pem` hold the signing and link keys.
  - Don't read them, copy them or put their contents in output; `flip` handles them itself.
- **The user runs anything that writes the phone's storage or a card:**
  `flip flash`, `flip sd write/update`, `flip backup create`, and `sudo`
  (it needs their password). Prepare the step and say what to run. Run it
  yourself only when the user says so in this conversation.
- **Ask before** breaking changes, mass deletes, rewriting git history,
  pushing, or changing which computers the phone trusts.
- **Git follows the `feature-workflow` skill:** feature branch, chunks in
  waves, PR, and the user squash-merges. Starting that workflow (or the user
  asking) is permission to commit on feature and chunk branches. Never commit
  on `main`; the guard asks if you try. Every push and every PR action
  (create, comment, merge) needs the user's yes.
- **Unplug before powering off.** Powering off with USB plugged in has
  crashed this phone. Never power off or reboot the phone with the cable in
  unless the user asks.
- **Don't handle the user's passwords** (Wi-Fi, sudo). The user types them.
- **Phone security model** (see README "Access"):
  - No SSH, no listening ports; nftables drops incoming traffic.
  - Admin works only over the USB cable through flipd, for trusted computers.
  - The future agent runs as the unprivileged user `phone` with no sudo.
  - Keep it that way.
- **No live upstream dependencies:**
  - Reusing code from weeknd-toolbox (Affe Null) or postmarketOS is fine.
  - Every input is pinned in `pins.toml` (or vendored) and verified on fetch.
  - Never fetch from a moving branch or an unpinned URL.

## Conventions

- **Language:** Python 3.11+ for the CLI. The phone has `python3`, so
  phone-side programs are Python or POSIX/bash scripts using only the
  standard library and Debian packages.
- **Tests run by themselves:** `.claude/hooks/run-tests.py` runs `pytest -x` after
  every edit to code the tests cover (`src/`, `sd/`, `tests/`, `patches/`, ...)
  and reports failures back. It stays silent while the tests pass. Midway
  through a change that spans several files, a red result is expected; finish
  the change, then make it green.
- **Tests:** every change gets a test, including phone-side code (load the
  overlay script in the test the way `tests/test_screen.py` does). Don't leave
  `__pycache__` in `sd/overlay/`, because the overlay goes on the card as-is.
- **Comments and docs** are plain and short. They say why, not what.
  User-facing behaviour goes in the README, in the same style.
- **The phone is slow and has little RAM:** prefer event-driven waits to
  polling, and measure startup and battery cost (see `battery-measure`).
- **Phone clock:** the phone runs on UTC, and `flip battery` shows the
  computer's local time.
