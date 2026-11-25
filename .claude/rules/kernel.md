---
paths:
  - "patches/linux/**"
  - "defconfig"
connects: [build-and-pins, sd-image, phone-overlay, battery, config-and-profile]
---

# Kernel patches and configuration

## Features

| Patch | Adds |
|---|---|
| `0001-arm64-dts-weeknd-battery-and-charger` | pm8916 BMS (fuel gauge, OCV table) and the linear charger |
| `0002-arm64-dts-weeknd-power-key` | Power key as `KEY_POWER` |
| `0003-arm64-dts-weeknd-lid-switch` | Hall sensor on GPIO 44 as `SW_LID`, which wakes the phone |

`defconfig` is the full kernel `.config` (saved with GCC 12.2; `make olddefconfig` fills in
newer options). Notable options: USB configfs ACM (flip-link), `CONFIG_SUSPEND`, and Wi-Fi,
audio and modem drivers as modules.

## Connected to

- build-and-pins applies patches in order onto the pinned fork. sd-image ships the result
  (`flip sd update` for an existing card).
- phone-overlay programs and battery read the interfaces these create: input codes, the
  `pm8916-*` power-supply names, backlight and framebuffer nodes, and suspend.
- config-and-profile names the dtb (`KERNEL_DTB`).
- Tests check key lines: `test_ocv_table_matches_the_kernel_battery_node` and
  `test_kernel_can_sleep_and_sees_the_lid`.

## Coding notes

The `kernel-and-hardware` skill has the patch workflow. Beyond it:

- Patches must apply with `git apply` onto the pin, in order. Before saving, check that a new
  patch applies after all the others (`flip build` does exactly that).
- Write device-tree changes as a self-contained node or property block, so later patches
  don't conflict. Prefer one patch per hardware topic.
- Built-in code makes the kernel image bigger; modules don't. Prefer `=m` unless the option
  is needed before the root file system is mounted or very early in boot. Today the USB ACM
  gadget, SPI, the panel and SMD are built in; Wi-Fi (wcn36xx), bam-dmux, the modem
  remoteproc and the audio codec are modules.
- Interfaces that userspace relies on (node names, input codes) are a contract. Changing
  one means updating the programs in phone-overlay in the same change.
