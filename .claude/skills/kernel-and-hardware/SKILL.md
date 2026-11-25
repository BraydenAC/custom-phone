---
name: kernel-and-hardware
description: Change the phone's kernel (patches/linux, defconfig, the qm215-nokia-weeknd device tree) or bootloader (patches/lk2nd, lk2nd-dts-msm8952), build it, and hand the install to the user; and bring up new hardware (modem calls/SMS, audio paths, sensors, keys, wake sources) by researching how it works first. Use for kernel options, device-tree nodes, drivers, lk2nd changes, hardware that doesn't work yet, or when the user asks how kernel or hardware work is done here.
---

# Kernel, bootloader and hardware bring-up

The kernel is a pinned fork (weeknd-toolbox's msm8917/QM215 tree, see
`pins.toml`). The build never keeps edits made in the checkout. Every change
lives as a file in this repo:

| Change | Lives in | Applied by |
|---|---|---|
| Kernel source or device tree | `patches/linux/NNNN-*.patch` | `flip build` (`build.py` `apply_linux_patches`) |
| Kernel options | `defconfig` (a full `.config`) | copied, then `make olddefconfig` |
| lk2nd source | `patches/lk2nd/NNNN-*.patch` | `flip build` |
| lk2nd device tree | `lk2nd-dts-msm8952/` | symlinked into the lk2nd tree |

The device tree is `arch/arm64/boot/dts/qcom/qm215-nokia-weeknd.dts` in the
`linux/` checkout. `flip build` resets `linux/` (`git checkout -- .`) and
re-applies all patches each time, so unsaved edits there are lost.

## Writing a kernel patch

1. Run `uv run flip build` once, so `linux/` exists with the current patches
   applied (uncommitted).
2. Snapshot that state so the new diff contains only your change:
   `git -C linux add -A && git -C linux commit -qm "flip patches (temporary)"`
3. Edit in `linux/`. For new files, run `git -C linux add -N FILE` so the diff
   sees them.
4. Save the change. Number it after the last patch, one topic per patch:
   `git -C linux diff > patches/linux/0004-arm64-dts-weeknd-TOPIC.patch`
5. Drop the temporary commit, keeping the files:
   `git -C linux reset -q HEAD~1`. The next build's checkout cleans up.
6. Add a header like the existing patches: `From: flip`,
   `Subject: [PATCH] area: what`, then a paragraph on why and where the
   values came from (for example "stock hall_sensor: GPIO 44, pull-up, wakeup").
7. Add a test that checks the patch contains the key lines, as
   `tests/test_screen.py` does for the lid patch, then run `uv run flip build`.

## Kernel options

- Edit the line in `defconfig` (`# CONFIG_X is not set` becomes `CONFIG_X=y`
  or `=m`).
- After the build, confirm the option survived:
  `grep CONFIG_X linux/.config`. `olddefconfig` silently drops options whose
  dependencies are off.
- Add a test, as `test_kernel_can_sleep_and_sees_the_lid` does for
  `CONFIG_SUSPEND`.
- Mind the size: modules (`=m`) go into the card's `/usr/lib/modules`, and
  built-in code makes `Image.gz` bigger.

## Getting it onto the phone (the user runs this)

- **Kernel:** after `flip build`, tell the user to run `uv run flip sd update`
  with the phone plugged in and running Linux.
  - Over the link it uploads the new kernel, device tree and modules, keeps
    the old ones as `Image.gz.prev` and `*.dtb.prev`, and reboots. That takes
    about 150 s.
  - The phone reboots plugged in: a restart is fine, a power-off is not.
  - To check afterwards: `flip phone run -- uname -v` shows the build number
    and time, then look at `dmesg` for the new hardware.
  - **Rollback if it doesn't boot:** the user puts the card in the computer's
    reader and copies `Image.gz.prev` and the `.dtb.prev` back over the
    current ones on the boot partition.
- **lk2nd:** after `flip build`, the user flashes with `uv run flip flash`
  from stock fastboot (hold volume-down while plugging in).
  - To try a build without flashing, boot it in RAM from lk2nd's fastboot:
    `fastboot boot out/lk2nd-head.img`.
  - The stock recovery is gone, so a bad lk2nd is fixed by reflashing from
    stock fastboot, which can't be overwritten.

## Bringing up new hardware

Research before writing code. See [bringup.md](bringup.md) for where to
look, what's known about each piece of hardware, and the current state of
the modem, calls and SMS.
