---
name: battery-measure
description: Measure battery drain on the phone and compare before/after a change with the on-phone battery log and `flip battery` - planning the run, marking it with a note, reading the report (drain %/h, time asleep, screen-on, sleeps) and judging whether a difference is real. Use when a change could affect power (new daemon, polling, kernel options, CPU scaling, Wi-Fi/modem settings), or when the user asks about battery life or how the measurement works.
---

# Measuring battery drain

`flip-battery-log` on the phone writes one line a minute to
`/var/log/flip/battery.csv`, plus `#boot` and `#note` marker lines.
`uv run flip battery` pulls the log and reports each stretch on battery
(`src/flip/battery.py`).

## A measurement run

1. Mark it, so the report shows the note against the run:
   `uv run flip battery note "before: <what's about to change>"`
2. Ask the user to unplug for 1 to 2 hours and use the phone the way the
   comparison needs (for example lid closed the whole time, or open and idle).
   The USB link counts as USB power: while plugged in, nothing on battery can
   be measured and idle sleep never happens.
3. When it's plugged back in, run `uv run flip battery`. Add `--all` for every
   run and `--save PATH` to keep a copy (outside the repo, or in the scratch
   directory).
4. Make the change, then repeat with a new note ("after: ...") under the same
   conditions.

## Reading the report

- **charge** is the start and end % from the BMS's resting-voltage (OCV)
  estimate through the battery's OCV table, with decimals. The first
  10 minutes after unplugging are skipped while the estimate settles from
  charging.
- **drain %/h** gives "a full charge would last about N h". Under 2% measured
  is marked rough: the OCV estimate only moves in steps of up to 25 mV, so
  short runs are noisy.
- **asleep %** is gaps between samples (the logger doesn't run while
  suspended), and **sleeps** is the suspend count. Lid-closed runs should be
  mostly asleep.
- **screen on %** is minutes with the screen unblanked.

Compare runs only when asleep % and screen-on % are similar. A change that
makes the phone sleep less shows up there first. A real difference should be
clear across more than one pair of runs, or over a long run (several % drop).

## Cheaper checks first

- **CPU use of a service:** CPU ticks over 10 s
  ([deploy-to-phone diagnose.md](../deploy-to-phone/diagnose.md)). An idle
  daemon gains none. A busy loop in `screen` was caught this way, and runs
  logged before that fix look worse than they should.
- **Does it prevent sleep?** `journalctl -b -g 'PM:'` and `suspend_stats`.

## Changing the logger or report

Columns: `time,uptime,status,usb,vbat,ocv,capacity,screen,suspends`. Keep the
logger and `battery.parse` in step, and keep `OCV_TABLE` identical to patch
0001 (a test checks it). Tests are in `tests/test_battery.py`.
