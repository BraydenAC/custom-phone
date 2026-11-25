---
paths:
  - "patches/lk2nd/**"
  - "lk2nd-dts-msm8952/**"
connects: [build-and-pins, signed-images, flash-backup-device, sd-image, toolbox-rootfs]
---

# lk2nd bootloader patches

## Features

| Patch | Adds |
|---|---|
| `0001-aboot-optionally-fall-back-to-recovery` | `LK2ND_FALLBACK_RECOVERY`: with no bootable extlinux, start the toolbox in recovery |
| `0002-boot-add-oem-boot-scan` | `fastboot oem boot-scan`: reports what lk2nd finds on each partition (why a card doesn't boot) |
| `0003-aboot-recovery-key-boots-toolbox` | Volume-up as lk2nd starts skips the search and starts the toolbox |

`lk2nd-dts-msm8952/qm215-qrd-5.dts` hard-codes the 2780 (`qm215-nokia-weeknd`).
`rules.mk` adds it to lk2nd's build.

## Connected to

- build-and-pins builds one lk2nd per layout (`kaios-first` and `linux-first` differ in
  base address and partition). signed-images packs it with the toolbox, and
  flash-backup-device flashes it.
- lk2nd boots the SD card: it looks for ext2 with `extlinux/extlinux.conf` on partitions of
  16 MiB or more, MBR only. sd-image's layout and the toolbox-once rename depend on that
  search.
- toolbox-rootfs: the fallback starts the toolbox, and the PON "recovery" reboot reason
  brings the phone back to lk2nd.

## Coding notes

- A broken lk2nd can stop the phone from booting anything but KaiOS. Stock fastboot
  (volume-down while plugging in) always remains for reflashing. Try changes in RAM first
  (`fastboot boot out/lk2nd-head.img` from lk2nd fastboot), as the skill describes.
- lk2nd is rebuilt clean every time (compile-time options don't trigger rebuilds reliably).
  Don't rely on incremental builds.
- Keep the patches upstreamable in style (lk2nd's C conventions, small and focused): the
  pinned release may be updated later.
