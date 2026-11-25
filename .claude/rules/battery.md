---
paths:
  - "src/flip/battery.py"
  - "sd/overlay/usr/lib/flip/battery-log"
  - "sd/overlay/etc/systemd/system/flip-battery-log.service"
connects: [phone-overlay, phone-link, kernel, cli, tests]
---

# Battery log and report

## Features

- On the phone, `battery-log` (bash, run by `flip-battery-log.service`) appends a line every
  minute to `/var/log/flip/battery.csv`:
  `time,uptime,status,usb,vbat,ocv,capacity,screen,suspends`. It also writes `#boot`
  markers, and `#note` lines from `flip battery note`. The file is capped at 40000 lines
  (halved at start).
- On the computer, `battery.py` parses the log into samples and notes, splits it into runs
  on battery (per boot, at least 10 min, skipping the first 10 min while the OCV settles),
  and reports drain %/h, time asleep, screen-on time and sleeps. `ocv_to_percent`
  interpolates the battery's OCV table.
- `cli.py` `cmd_battery` pulls the log over the link, and `cmd_battery_note` appends a note.

## Connected to

- Kernel patch 0001 defines the OCV table and the power-supply nodes. `OCV_TABLE` must stay
  identical (a test checks it), and renamed sysfs nodes break the logger.
- The `screen` column reads the backlight, and `suspends` reads
  `/sys/power/suspend_stats/success`. Changes in phone-overlay (screen daemon) or kernel
  (suspend) show up here.
- The log is pulled over phone-link. The `battery-measure` skill and the log-investigator
  agent read it.

## Coding notes

- The logger must cost nearly nothing: no forks in the loop (it reads sysfs with
  `read -r`), one write a minute, and `Nice=10`.
- The log format is a contract between bash and Python. Add columns only at the end, keep
  `parse` tolerant of old lines and of a line cut short by a power loss, and update both
  sides and `tests/test_battery.py` together.
- Times in the log are the phone's UTC epoch seconds. The report shows the computer's local
  time.
