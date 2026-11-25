# Diagnosing the running phone

All of these are `uv run flip phone run -- sh -c '...'` lines. Pull logs into the
session's scratch directory, not into the repo.

## First look

```sh
systemctl --failed --no-legend; uptime; free -m
journalctl -b -p warning --no-pager | tail -50       # this boot's warnings and errors
journalctl -b -u flip-screen -n 50 --no-pager        # one unit
dmesg --level=err,warn | tail -40                    # kernel
systemd-analyze; systemd-analyze blame | head        # startup time
```

## Hardware state (sysfs)

| What | Where |
|---|---|
| Battery | `/sys/class/power_supply/pm8916-bms-vm/{status,voltage_now,voltage_ocv,capacity}` (microvolts) |
| Charger / USB power | `/sys/class/power_supply/pm8916-lbc-chgr/online` (1 = cable delivers power; the USB link counts) |
| Screen | `/sys/class/graphics/fb0/blank` (0 on, 4 powered down), `/sys/class/backlight/backlight/bl_power` |
| Keypad light | `/sys/class/backlight/ext-backlight/brightness` |
| Sleep | `/sys/power/state`, `/sys/power/mem_sleep` (s2idle), `/sys/power/suspend_stats/{success,fail}` |
| Input devices | `cat /proc/bus/input/devices` (keys, power key, lid switch SW_LID on GPIO 44) |
| Wake sources | `cat /sys/kernel/debug/wakeup_sources` (if debugfs is mounted) |

The `flip-screen` daemon owns screen power: it blanks the screen after 10 min
idle and suspends after 15 min idle on battery. logind suspends when the lid
closes on battery (`/etc/systemd/logind.conf.d/flip.conf`). Settings are in
`/etc/flip/screen.conf`.

## CPU use of a service

`ps` often doesn't show Python daemons by name. Read their CPU ticks:

```sh
pid=$(systemctl show -p MainPID --value flip-screen)
a=$(cut -d" " -f14,15 /proc/$pid/stat); sleep 10; b=$(cut -d" " -f14,15 /proc/$pid/stat)
echo "$a -> $b"    # utime stime in 1/100 s; unchanged = idle
```

An idle daemon should gain 0 ticks. Growth while nothing happens means a busy
loop. That has happened before: `Policy.wait` returned 0 once the screen was
off.

## Network and modem

```sh
nmcli -t dev; nmcli -t con show --active; ip -br addr
mmcli -L; mmcli -m any                 # ModemManager's view (stops at sim-missing without a SIM)
systemctl status rmtfs tqftpserv flip-modem-data ModemManager --no-pager
```

Nothing should listen on the network. Check with `ss -tulpn`: there should be no
listeners besides loopback.

## Audio

`/usr/lib/flip/audio-test speaker|earpiece|headphones|mic|headset` plays or records
through the ALSA UCM profiles.

## After waking from sleep

When the user reports a sleep/wake cycle, check each part came back:

```sh
cat /sys/power/suspend_stats/success; journalctl -b -g 'suspend|resume|PM:' --no-pager | tail -20
nmcli -t -f DEVICE,STATE dev           # Wi-Fi reconnected?
mmcli -L                               # modem still there?
cat /sys/class/graphics/fb0/blank      # screen back on after opening the lid / a key?
```

If the USB link (`flip phone info`) answers, flipd and the gadget survived.
Known gaps: only the power key and the lid wake the phone (number keys probably
don't), and nothing after waking has been checked unplugged yet.

## When the link itself is broken

- The phone doesn't show up as `flip-link`: it may be asleep, off, or booted
  into KaiOS or the toolbox. Ask the user what the screen shows.
- `flip device status` tells fastboot modes apart.
- A card that won't boot: the user puts it in the computer's reader, or
  checks `fastboot oem boot-scan` from lk2nd's fastboot (README, "Booting").
