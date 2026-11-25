# Bringing up hardware

## Where to look, in order

1. **What the kernel already has:**
   - `linux/arch/arm64/boot/dts/qcom/qm215-nokia-weeknd.dts`, the `msm8917`
     dtsi it includes, and `pm8916` for the PMIC.
   - Check what's defined but disabled before writing anything new.
2. **How mainline and postmarketOS do it** for the same SoC family
   (msm8916/msm8917/msm8937/QM215):
   - the msm8916-mainline project, the postmarketOS wiki pages for the Nokia
     2780 and similar QM215/msm8917 phones, and pmaports device packages.
   - Fine to read and reuse. Pin anything we take (CLAUDE.md).
3. **The stock firmware's device tree**, for pins, GPIOs and regulators:
   - It's in the user's backup, which is private. Ask the user to extract
     and decompile the dtb to a file outside `backups/` if it's needed.
   - Never read `backups/` directly.
4. **The running phone:** `dmesg`, `/proc/bus/input/devices`, `/sys/bus/*/devices`,
   `cat /sys/kernel/debug/gpio` (debugfs), and `i2cdetect` if it's installed.

Write down where each value came from, in the patch description.

## State of the hardware

| Hardware | State | Pieces |
|---|---|---|
| Battery, charging | Works | patch 0001 (pm8916 BMS + charger, OCV table), `battery-log` |
| Power key | Works | patch 0002 (KEY_POWER), logind: short press ignored, long press powers off |
| Lid (hall sensor) | Works, wakes the phone | patch 0003 (GPIO 44, SW_LID, wakeup-source), `screen`, logind |
| Suspend | Works (s2idle) | `CONFIG_SUSPEND=y`. Only power key and lid wake it; checks after waking unplugged still to do |
| Wi-Fi | Works | wcn36xx, real MAC from `deviceinfo` (`device-addresses`), NetworkManager |
| Audio | Works | ALSA UCM; `audio-test` passes speaker, earpiece, headphones, mic, headset |
| GPU | Firmware loads | Adreno a300 |
| Display | Works | ST7789 SPI panel, `flip-panel.service`, fb0 |
| Modem | Up to sim-missing | rmtfs, tqftpserv, `flip-modem-data` (QMI DPM open-port, bam-dmux channel 0; qmicli rejects 0 for rx/tx ids, so use 1), ModemManager |
| Mobile data, SMS, calls | Waiting for a SIM | Untested. Calls will also need call-audio routing |
| CPU frequency scaling | Not looked at | Do it only if `battery-measure` shows it's worth it |

## Lessons

- The lid, power key and other buttons are gpio-keys nodes. Set
  `wakeup-source` for anything that should wake the phone from s2idle.
- A wake source also needs the interrupt to work during suspend. Test by
  closing the lid on battery, opening it, then checking
  `/sys/power/suspend_stats/success` went up.
- Firmware comes from the user's backup and is put into the image by
  `flip sd build` (`sd.py` `extract_firmware`). New firmware files need to
  be added there, not committed.
- lk2nd 23.1 reads only MBR partition tables, so SD images stay MBR.
- When a change crashes the phone into Qualcomm crash-dump mode (the
  computer sees `05c6:900e`), recovery is: unplug, hold power and
  volume-down for about 20 s, then power on unplugged.
