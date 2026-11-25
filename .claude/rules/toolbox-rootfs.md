---
paths:
  - "rootfs/**"
  - "toolbox/**"
connects: [signed-images, build-and-pins, flash-backup-device, sd-image, config-and-profile, lk2nd]
---

# The rescue toolbox (`rootfs/`, `toolbox/`)

## Features

- `rootfs/init` (busybox `sh`, from weeknd-toolbox by Affe Null) is the toolbox's menu on
  the phone's screen:
  - 1. Power off;
  - 2. USB storage: the whole eMMC or a partition, read-only by default, with a
    read-only/read-write mode switch;
  - 3. Disable encryption;
  - 4. View kernel logs;
  - with a card present, **5. SD to computer** (share the card as "flip SD card") and
    **6. Start SD Linux**;
  - 7. Start KaiOS.

  With `extlinux.conf.toolbox-once` on the card, it puts the file back and shares the card
  automatically (`flip sd write/update` with the card in the phone).
- `rootfs/lib/firmware/` holds the panel's init sequence (ST7789VX2).
- `toolbox/reboot-mode.c` is a tiny static program that reboots with a given reason, for
  example `recovery`, so the bootloader starts lk2nd.

## Connected to

- build-and-pins builds the initramfs (`initramfs.py`, busybox from `pins.toml`,
  `BUSYBOX_APPLETS` in config-and-profile) and compiles `reboot-mode`. signed-images packs
  it with lk2nd into boot or recovery.
- flash-backup-device: backups read the USB storage this exposes. `flip backup create`
  expects its USB IDs and model (`TOOLBOX_USB_ID`, `TOOLBOX_USB_MODEL`).
- sd-image: the toolbox-once handshake, the shared card's SCSI model (`SD_INQUIRY` must
  equal `profile.TOOLBOX_SD_MODEL`), and `flip_toolbox=N` (`TOOLBOX_VERSION`) on the command
  line.
- lk2nd starts the toolbox when there's nothing to boot or volume-up is pressed.

## Coding notes

- `init` is busybox `sh` only: use only applets in `BUSYBOX_APPLETS`, plus `reboot-mode`.
  Adding an applet means changing the profile list.
- The eMMC is exposed read-only unless the user picks read-write in the menu. Keep it that
  way, and never make sharing the eMMC automatic. The automatic path shares only
  `/dev/mmcblk1`, the SD card.
- When the toolbox gains a behaviour flip relies on, bump `TOOLBOX_VERSION` so flip can
  refuse an old toolbox instead of hanging.
- The output is reproducible (signed-images). Changes here change the image hash, which is
  expected.
