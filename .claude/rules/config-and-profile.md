---
paths:
  - "src/flip/config.py"
  - "src/flip/profile.py"
connects: [build-and-pins, signed-images, flash-backup-device, sd-image, phone-link, toolbox-rootfs, kernel, cli, phone-overlay]
---

# Paths, settings and device facts (`config.py`, `profile.py`)

## Features

- `config.py` has the repo paths (`REPO`, `PATCHES`, `DEFCONFIG`, `LINUX_DIR`, ...) and the
  user config directory, `~/.config/flip` (overridden by `FLIP_CONFIG_DIR`). That directory
  holds the AVB key, the adopted vbmeta, the flashed-layout and flashed-pkmd records, and the
  link keys (`host-key.pem`, `phone-identity.pem`). It also has the toolchain settings.
- `profile.py` has fixed facts about the Nokia 2780 and our image layout:
  - USB IDs, the stock product (`WND`);
  - partition sizes, the eMMC size range;
  - kernel dtb names, the toolbox command line and `TOOLBOX_VERSION`;
  - layouts (`kaios-first`/`linux-first`), dtbo overlays, busybox applets;
  - SD labels, sizes and command line, and `SD_PACKAGES`.

## Connected to

Almost everything reads these, so a change here has a wide blast radius:

- `profile` partition sizes and USB IDs: flash-backup-device (safety checks) and
  signed-images (image sizes).
- `TOOLBOX_VERSION`: the toolbox command line (`boot_cmdline`) and `sd.py`
  `PHONE_TOOLBOX_VERSION`, which refuses to restart the phone into an older toolbox.
- `SD_BOOT_LABEL`/`SD_ROOT_LABEL`/`SD_CMDLINE`: sd-image (build, write, update) and lk2nd,
  which finds `extlinux.conf` on the boot partition.
- `TOOLBOX_SD_MODEL`: toolbox-rootfs (`SD_INQUIRY` in `rootfs/init` must match it exactly).
- `SD_PACKAGES`: sd-image, and what the phone-overlay programs can rely on.
- `KERNEL_DTB*`: kernel (device tree file name).
- Config paths: keys, link keys and backups. The guard and `settings.json` deny access to
  `~/.config/flip`.

## Coding notes

- Treat profile values as facts measured on the real phone. Change one only with evidence
  (a fastboot `getvar`, the partition table, a datasheet), and say where it came from in a
  comment.
- Bump `TOOLBOX_VERSION` whenever the toolbox gains something flip depends on, so flip can
  tell old toolboxes apart.
- Nothing in `config` may put private files inside the repo. Keys and records live under
  the config directory, and backups under the gitignored `backups/`.
- Tests use the `isolated_config` fixture (`FLIP_CONFIG_DIR` set to a temporary directory),
  so they never touch the real keys.
