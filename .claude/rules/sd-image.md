---
paths:
  - "src/flip/sd.py"
  - "sd/setup.sh"
  - "sd/debug/**"
  - "sd/README.md"
connects: [config-and-profile, build-and-pins, phone-link, phone-overlay, toolbox-rootfs, kernel, flash-backup-device, cli, lk2nd, tests]
---

# The SD card image (`sd.py`, `sd/setup.sh`)

## Features

- `flip sd build` makes `out/sd/sd.img` without root:
  1. `mmdebstrap` installs the pinned Debian packages (`SD_PACKAGES`) under qemu.
  2. `stage()` adds `sd/overlay/`, the kernel and modules, the firmware extracted from the
     newest backup, flipd (a copy of `src/flip/link/`) and the link keys, plus a generated
     `fstab` and `extlinux.conf`.
  3. `sd/setup.sh` runs inside the image.
  4. `root_owned_tar` + `check_ownership` make every file root-owned.
  5. The ext2 boot and ext4 root partitions are packed into an MBR image.

  `--debug` adds `sd/debug/` (root login on the USB serial line, no flipd).
- `flip sd write` writes a card (checked: an SD card, not the eMMC), reads it back to
  verify, and grows the root partition.
- `flip sd update` puts the new kernel, dtb and modules on a card. It works three ways:
  - in the computer's card reader;
  - with the card in the phone, via the toolbox sharing it;
  - over the link (`update_over_link`: upload, keep `.prev` copies, reboot).
- With the card in the phone: flipd renames `extlinux.conf` to `extlinux.conf.toolbox-once`
  and reboots to recovery, the toolbox shares the card as "flip SD card", flip writes it,
  ejects it, and the phone boots Linux again.
- `flip sd info` reads the image's manifest.

## Connected to

- phone-overlay: `sd/overlay/` is copied verbatim, and `setup.sh` enables its units. A new
  unit that isn't in `setup.sh` never starts on a fresh card.
- phone-link: `src/flip/link/` is copied into `/usr/lib/flipd/flip/link`, and the link keys
  into `/etc/flipd`. `sd.py` also uses flipd to restart the phone and to update over the
  link.
- toolbox-rootfs: the one-shot `extlinux.conf.toolbox-once` rename, the shared card's model
  name, and `flip_toolbox=N`.
- kernel and build-and-pins: kernel image, dtb, modules, `kernel_release`; the Debian
  snapshot pin.
- flash-backup-device: firmware comes from the newest backup.
- config-and-profile: labels, sizes, command line, packages.

## Coding notes

- The image contains private data (firmware, the phone's identity key). Treat `out/sd/` like
  `backups/`.
- Build computer uid 1000 equals the phone user `phone` (uid 1000). Anything copied into the
  image must go through `root_owned_tar`, or files end up owned by the agent's user.
  `check_ownership` enforces that.
- lk2nd reads only MBR partition tables: keep the image MBR.
- Card safety: `write_card` refuses anything that isn't an SD card, and refuses the eMMC
  model. The user's card reports `RM=0`, so "removable" can't be used as a check. Never loosen
  `check_card`.
- `setup.sh` runs under qemu emulation, where `systemctl` can only enable, not start. Keep
  it POSIX `sh` with `set -eu`, and don't do anything there that needs the real hardware.
