---
paths:
  - "sd/overlay/**"
connects: [sd-image, phone-link, battery, kernel, config-and-profile, tests]
---

# Phone-side system files (`sd/overlay/`)

## Features

| Area | Files | What it does |
|---|---|---|
| Screen power | `usr/lib/flip/screen`, `flip-screen.service`, `etc/flip/screen.conf` | Screen off after `idle_off` (600 s), sleep after `idle_sleep` (900 s, battery only), lid open/close, keypad light |
| Lid and power key | `etc/systemd/logind.conf.d/flip.conf` | Lid closed: suspend on battery, ignored on USB power; power key: short press ignored, long press powers off |
| Splash | `usr/lib/flip/splash`, `flip-splash.service`, `flip-splash-shutdown@.service`, `usr/lib/systemd/system-shutdown/flip-splash`, `etc/flip/splash.conf`, `getty@tty1.service.d/` | Startup hexagons following real startup steps; shutdown animation and "UNPLUG USB" |
| Panel | `flip-panel.service` | Brings the SPI panel up early |
| Battery log | `usr/lib/flip/battery-log`, `flip-battery-log.service` | See the battery rule |
| Modem | `usr/lib/flip/modem-data`, `flip-modem-data.service`, `usr/lib/tmpfiles.d/flip-modem.conf` | Opens the bam-dmux data channel for ModemManager |
| Addresses | `usr/lib/flip/device-addresses`, `flip-device-addresses.service` | The phone's real Wi-Fi/BT MACs from the `deviceinfo` partition |
| Audio | `usr/share/alsa/ucm2/`, `usr/lib/flip/audio-test` | UCM profiles; test playback and recording on each path |
| Network | `etc/NetworkManager/conf.d/flip.conf`, `etc/nftables.conf` | Wi-Fi; drop all incoming traffic |
| System | `etc/systemd/{journald,system}.conf.d/`, `zram-generator.conf`, `etc/apt/` | Small journal, quiet console, zram swap, automatic security updates, live mirrors |
| Link | `usr/lib/flipd/`, `flipd.service`, `flip-gadget.service`, `70-flip-link.rules` | See the phone-link rule |

## Connected to

- sd-image copies all of this into the image and enables units in `sd/setup.sh`.
- Kernel interfaces (sysfs paths, input codes like `SW_LID`, the power-supply names
  `pm8916-bms-vm` and `pm8916-lbc-chgr`) come from kernel patches. Renaming a node in the
  device tree breaks the programs reading it.
- Programs coordinate through files:
  - `splash` writes `/run/flip-splash.pid`, and `screen` leaves the screen alone while that
    process lives;
  - `screen` and `splash` both check USB power via `/sys/class/power_supply`.
- logind and `screen` split the sleep decisions: lid closed is logind's, idle with the lid
  open is `screen`'s. Change them together.
- Every program has a test in `tests/test_<name>.py` that loads the overlay file directly.

## Coding notes

- Files are copied as-is with their git modes. Programs must be executable in git. Never
  leave `__pycache__` or editor backups here.
- Programs run as root under systemd. Write errors to stderr (they land in the journal), and
  survive missing hardware: log it and carry on, don't crash-loop. The splash must never
  block startup.
- Keep each program's built-in defaults equal to its `etc/flip/*.conf`. Tests compare them.
- Use `CLOCK_MONOTONIC` (`time.monotonic()`) for timers. It stops during suspend, so time
  asleep doesn't count as idle time.
- Assume that `flip phone run`, the agent and the user can all be active: don't take
  exclusive locks on the framebuffer, inputs or the modem port beyond what the program needs.
