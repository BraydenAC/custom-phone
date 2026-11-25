---
paths:
  - "src/flip/flash.py"
  - "src/flip/device.py"
  - "src/flip/backup.py"
connects: [build-and-pins, signed-images, config-and-profile, toolbox-rootfs, sd-image, cli, lk2nd, tests]
---

# Flashing, backups and talking to the bootloader

## Features

- `device.py`:
  - finds the phone's mode from USB IDs and fastboot (`Mode`: stock fastboot, lk2nd
    fastboot, toolbox USB storage, other fastboot, not detected; Linux is found by
    phone-link instead);
  - wraps `fastboot` (overridden by `FLIP_FASTBOOT` in tests);
  - `getvar all`, and the lk2nd boot log.
- `flash.py`: `flip flash` targets `all` (first install: key + vbmeta + toolbox), `toolbox`,
  `linux-first` and `kaios-first`. It plans the partition writes, runs a preflight (stock
  fastboot only, the right product, images present, key state), and records the flashed
  layout in `~/.config/flip/flashed-layout`.
- `backup.py`:
  - full eMMC backups through the toolbox's USB storage: block device checks, partition
    extraction, `SHA256SUMS`;
  - `list`, `verify` and `adopt-vbmeta`;
  - the system partition's PARTUUID (used for KaiOS in recovery).

## Connected to

- Images come from build-and-pins (`build.OUT`) and signed-images. Key state comes from
  `keys.py`.
- Partition names and sizes, and USB IDs, come from config-and-profile.
- toolbox-rootfs provides the USB storage that backups read, and the "recovery" reboot
  path.
- Backups feed build-and-pins (KaiOS-for-recovery, the adopted vbmeta) and sd-image
  (firmware extraction). The backup format (directory layout, `SHA256SUMS`) is relied on
  in all three.

## Coding notes

- These modules write the phone's eMMC. Guard rails are the feature:
  - refuse when the mode or product is wrong;
  - refuse lk2nd fastboot for flashing (offsets differ there);
  - check the size of every image against its partition before writing anything;
  - confirm through `confirm`.

  Never weaken a check to make something work. Add a test for every new refusal
  (`tests/test_device_flash.py` uses the `fake_fastboot` fixture).
- `backups/` holds the IMEI and radio calibration. Code may read and hash it, and copy from
  it into private outputs, but must never print its contents, upload it, or write it
  anywhere readable by others.
- Backups are read-only once written (`finish` sets that). `verify` must stay able to
  re-check old backups, so never change `SHA256SUMS`' format without keeping the old one
  readable.
- These commands are run by the user (CLAUDE.md). Code changes here are tested with fakes,
  never by running them against the phone.
