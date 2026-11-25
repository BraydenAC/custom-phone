---
paths:
  - "src/flip/images.py"
  - "src/flip/initramfs.py"
  - "src/flip/keys.py"
  - "tools/**"
  - "overlay-wnd*.dts"
connects: [build-and-pins, config-and-profile, flash-backup-device, toolbox-rootfs, lk2nd, cli]
---

# Boot images, signing and the AVB key

## Features

- `images.py` wraps the vendored Android tools in `tools/` (`mkbootimg`, `mkdtboimg`,
  `avbtool`):
  - assembles the lk2nd + toolbox image;
  - repacks KaiOS's boot image to run from recovery (`kaios_for_recovery`: the stock dtbo
    embedded as `recovery_dtbo`, plus `root=PARTUUID`);
  - makes the vbmeta.
- `initramfs.py`: a reproducible cpio "newc" + gzip writer for the toolbox's initramfs.
- `keys.py`: the AVB signing key in `~/.config/flip/avb-key.pem`. It creates the key, fixes
  its permissions, derives the pkmd the phone stores (`avb_custom_key`), records what was
  flashed, and handles `keys show` and `keys export`.
- `overlay-wnd*.dts`: the dtbo overlays for the phone's hardware IDs (wnd1, wnd2, wnd2e),
  kept from the original image layout.

## Connected to

- build-and-pins calls everything here. flash-backup-device flashes the results and checks
  the key against what the phone has.
- The image layout and sizes come from config-and-profile.
- The toolbox's contents come from toolbox-rootfs, and the bootloader from lk2nd.
- The phone only boots images signed with the key whose pkmd was flashed with
  `flip flash all`. Changing how the key or vbmeta is made can lock the user out of booting
  custom images until they run `flash all` again.

## Coding notes

- `tools/avbtool.py` carries a LOCAL MODIFICATION: re-signing drops the hash descriptors.
  Keep the marker comments. Updating the vendored tools means re-applying that change and
  re-checking the README's "Signing key and verified boot" section, which documents the
  consequence.
- The signing key never enters the repo, the output or logs. Only `keys export` copies it,
  and only to a path the user gives. Write it with `write_private`.
- Image layout changes (offsets, header versions, sizes) need a test in
  `tests/test_images.py`, and ideally an output proven identical before and after a
  refactor.
