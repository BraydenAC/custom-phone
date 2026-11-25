---
paths:
  - "src/flip/build.py"
  - "src/flip/fetch.py"
  - "src/flip/lock.py"
  - "src/flip/fingerprint.py"
  - "pins.toml"
  - "uv.lock"
connects: [config-and-profile, signed-images, toolbox-rootfs, kernel, lk2nd, sd-image, flash-backup-device, cli]
---

# Build and pinned inputs

## Features

- `build.py`: `flip build` builds, in order:
  1. the kernel (pinned source, `defconfig`, `patches/linux/`, modules exported to
     `out/modules`);
  2. lk2nd for each layout (`patches/lk2nd/`, `lk2nd-dts-msm8952/`);
  3. `reboot-mode` (from `toolbox/`) and the toolbox initramfs;
  4. dtbo, the lk2nd + toolbox image, the KaiOS-for-recovery image (from the newest backup)
     and the signed vbmeta.

  `OUT` names every output, and `flip clean` deletes them.
- `fingerprint.py` (`Stamp`): skips an expensive step when its inputs haven't changed.
- `lock.py`: loads and validates `pins.toml` (git pins by commit, file pins by SHA-256, the
  Debian snapshot, trust levels). `FLIP_URL_<NAME>` swaps in a mirror.
- `fetch.py`: fetches the pinned inputs and verifies every byte against the lockfile.
- `uv.lock` pins the Python dependencies of the CLI itself.

## Connected to

- Outputs are consumed by:
  - flash-backup-device: `flash.py` imports `build.OUT`;
  - sd-image: kernel image, dtb, modules, `kernel_release`.

  Renaming or moving an output breaks both.
- Inputs are owned by kernel, lk2nd, toolbox-rootfs and signed-images. A new kind of input
  must be added to the right fingerprint, or builds will reuse stale outputs.
- KaiOS-for-recovery and vbmeta read the user's backup (flash-backup-device), which is
  private data: flip reads it, the agent doesn't.
- The `pins.toml` Debian snapshot is used by sd-image (`mmdebstrap`).

## Coding notes

- Reproducibility is a feature: the toolbox image is byte-identical for the same inputs.
  Don't add timestamps, unsorted directory walks or host paths to outputs. Use fixed mtimes
  (see `initramfs.py`).
- Every external input is pinned and verified (CLAUDE.md, "No live upstream dependencies").
  A new download needs a `pins.toml` entry with a SHA-256 or commit and a trust note, and must
  go through `fetch.py`.
- `build.py` resets `linux/` and `lk2nd/` to their pins (`git checkout -- .`) before applying
  patches. Never rely on edits in those checkouts.
- Extend the stamp's input list whenever a step starts reading a new file.
