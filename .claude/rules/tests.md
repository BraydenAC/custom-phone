---
paths:
  - "tests/**"
connects: [cli, phone-overlay, phone-link, sd-image, flash-backup-device, battery, harness]
---

# Tests

## Features

| File | Covers |
|---|---|
| `test_sd.py` | Image staging, ownership, card checks, update over the link, toolbox-once |
| `test_link.py` | Handshake, rejection, run, shell, push/pull, the trusted list (flipd in a thread over a socketpair) |
| `test_device_flash.py` | Mode detection, flash plans and refusals (`fake_fastboot`) |
| `test_images.py` | Image layout, initramfs reproducibility |
| `test_backup_keys.py` | Backup checks and sums, key handling (`isolated_config`) |
| `test_lock_fetch.py` | `pins.toml` validation, verified fetches |
| `test_splash.py`, `test_screen.py`, `test_battery.py` | Phone-side programs, loaded from `sd/overlay/` |
| `test_harness.py` | Guard decisions, the test hook, settings, skills, agent, rules |

`conftest.py` provides `fake_fastboot` and `isolated_config`.

## Connected to

Each test file mirrors one area. When an area changes, its test file is the first place to
update. The harness hook runs the whole suite after every code edit.

## Coding notes

- Tests never touch the real phone, real cards, the real config directory or `backups/`:
  - use `isolated_config` for keys and records;
  - use `fake_fastboot` for the bootloader;
  - use temporary directories for sysfs;
  - run flipd over a socketpair.
- Phone-side programs are tested from their overlay file. Load them with
  `SourceFileLoader` and `sys.dont_write_bytecode = True`, or run shell scripts with paths
  substituted.
- Threads and sockets: join server threads before closing their file descriptors (see the
  `phone` fixture in `test_link.py`). Closing first lets the next test's sockets reuse the
  numbers.
- Never let a test run the whole suite. That's pytest inside pytest; fake `uv` the way
  `test_harness.py` does.
- The suite should stay fast (about 4 s), because it runs after every edit. Mark anything
  slow, or make it faster.
