---
name: phone-service
description: Add or change something that runs on the phone - a program or daemon in sd/overlay/usr/lib/flip, its systemd unit, a setting in /etc/flip, a udev/logind/NetworkManager drop-in, or a Debian package in the SD image - with its test and its place in sd/setup.sh. Use when building phone-side features, or when the user asks how phone services are structured or where a phone setting lives.
---

# Phone-side programs and services

Everything flip adds to the phone is in `sd/overlay/` (copied into the root
file system as-is) and `sd/setup.sh` (runs inside the new system during
`flip sd build`). [docs/architecture.md](../../../docs/architecture.md) lists
what exists.

## Where things go

| Piece | Path in `sd/overlay/` | Notes |
|---|---|---|
| Program | `usr/lib/flip/NAME` | Executable bit set in git (tests check it). Python 3 or bash, standard library and Debian packages only |
| Unit | `etc/systemd/system/flip-NAME.service` | Name it `flip-*` |
| Setting | `etc/flip/NAME.conf` | INI with a `[NAME]` section, commented; the program has the same defaults built in |
| Runtime dirs | `usr/lib/tmpfiles.d/flip-NAME.conf` | |
| Log | `/var/log/flip/` | Small, capped files; the journal is size-limited (`journald.conf.d/flip.conf`) |
| Drop-ins | `etc/systemd/logind.conf.d/`, `etc/NetworkManager/conf.d/`, `etc/udev/rules.d/` | |

Then enable the unit in `sd/setup.sh` (the `systemctl enable` lines). Files
arrive owned by root with their git modes (`sd.py` `root_owned_tar`). Only
secrets need tightening in `setup.sh`.

## Patterns to follow

Look at an existing service of the same kind before writing a new one:

- **Event-driven daemon with testable logic:** `usr/lib/flip/screen`. A pure
  `Policy` class takes `now` as an argument, so tests drive time. The loop
  blocks in `select()` with a computed timeout (no busy loops), reads
  settings via `load_config`, and falls back to defaults on bad values.
- **Small logger in bash:** `usr/lib/flip/battery-log`. It reads sysfs
  without forking, appends to a capped file, and marks each boot.
- **Startup coordination:** `splash` writes `/run/flip-splash.pid`, and
  others check it.
- **One-shot hardware setup:** `modem-data`, `device-addresses`.

## Rules for services

- **Startup cost:** the phone has 512 MB and a slow CPU. Don't poll during
  boot. Polling systemctl every 0.4 s once slowed startup from 14 s to 21 s;
  1 s cost nothing measurable.
- **Ordering:** a unit `Before=systemd-udevd` must not be
  `After=local-fs.target`. That cycle drops the phone into emergency mode,
  and a test guards it. Keep `After=`/`Wants=` minimal.
- **Privilege:**
  - Admin functions stay root-only, behind flipd.
  - Anything the future agent will use should run unprivileged, as user
    `phone` or with `DynamicUser=`.
  - Protected settings are root-owned files.
  - Never open a listening socket on the network.
- **Battery:** a new always-on service needs a `battery-measure` before and
  after.

## Tests

Put each service's tests in `tests/test_NAME.py`:

- **Python programs:** load the overlay file the way `tests/test_screen.py`
  does (`SourceFileLoader`, `sys.dont_write_bytecode = True` so no
  `__pycache__` lands in the overlay), then test the pure logic, the config
  parsing and the sysfs readers against `tmp_path`.
- **Shell scripts:** substitute the real paths with temporary ones and run
  the script once, as `test_logger_writes_a_sample` in
  `tests/test_battery.py` does.
- **Wiring:** assert that the unit's `ExecStart` exists, that `setup.sh`
  enables it, and that the program is executable.

## Debian packages

- **For new cards:** add the package to `SD_PACKAGES` in
  `src/flip/profile.py`, grouped by purpose. It's installed from the pinned
  snapshot in `pins.toml` `[debian]`, and reaches a card only through
  `flip sd build` plus `flip sd write`, which erases the card. That write is
  the user's step.
- **On the running phone:** `apt-get install -y PKG` over `flip phone run`
  (the phone uses live Debian mirrors). Tell the user it's installed live, so
  the card and a fresh build briefly differ.
- Prefer packages already in Debian over vendored binaries. Anything
  external must be pinned (CLAUDE.md, "No live upstream dependencies").

## Getting it onto the phone

Use the `deploy-to-phone` skill: push, set owner and mode, run
`daemon-reload`, then `enable --now`.
