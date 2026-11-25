"""Battery drain from the phone's log (/var/log/flip/battery.csv, written by
sd/overlay/usr/lib/flip/battery-log once a minute).

Charge comes from the BMS's resting-voltage (OCV) estimate through the
battery's OCV table (patches/linux/0001), like the driver's capacity but with
decimals. Gaps between samples longer than a minute are time asleep.
"""

from __future__ import annotations

import bisect
import time
from dataclasses import dataclass, field

LOG = "/var/log/flip/battery.csv"

# (mV, %) from the battery node, highest first.
OCV_TABLE = [
    (4380, 100), (4298, 95), (4236, 90), (4178, 85), (4123, 80), (4070, 75),
    (4018, 70), (3969, 65), (3918, 60), (3876, 55), (3846, 50), (3820, 45),
    (3798, 40), (3780, 35), (3764, 30), (3750, 25), (3732, 20), (3710, 16),
    (3692, 13), (3686, 11), (3684, 10), (3682, 9), (3678, 8), (3671, 7),
    (3650, 6), (3612, 5), (3558, 4), (3488, 3), (3392, 2), (3249, 1), (3000, 0),
]

INTERVAL = 60   # the logger's period, s
SETTLE = 600    # after unplugging, the OCV estimate is still coming down from charging
MIN_RUN = 600   # shorter runs on battery say nothing


def ocv_to_percent(mv: float) -> float:
    asc = OCV_TABLE[::-1]
    volts = [v for v, _ in asc]
    if mv <= volts[0]:
        return 0.0
    if mv >= volts[-1]:
        return 100.0
    i = bisect.bisect_right(volts, mv)
    (v0, p0), (v1, p1) = asc[i - 1], asc[i]
    return p0 + (p1 - p0) * (mv - v0) / (v1 - v0)


@dataclass
class Sample:
    time: int
    uptime: int
    status: str
    usb: bool
    vbat: int
    ocv: int
    screen: bool
    suspends: int
    boot: int


@dataclass
class Note:
    time: int
    text: str


def parse(text: str) -> tuple[list[Sample], list[Note]]:
    samples, notes, boot = [], [], 0
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("time,"):
            continue
        if line.startswith("#boot"):
            boot += 1
            continue
        if line.startswith("#note "):
            _, t, *rest = line.split(" ", 2)
            try:
                notes.append(Note(int(t), rest[0] if rest else ""))
            except ValueError:
                pass
            continue
        f = line.split(",")
        try:
            samples.append(Sample(int(f[0]), int(f[1]), f[2], f[3] == "1", int(f[4]),
                                  int(f[5]), f[7] == "1", int(f[8] or 0), boot))
        except (IndexError, ValueError):
            continue  # a line cut short by a power loss
    return samples, notes


@dataclass
class Run:
    """A stretch on battery (unplugged, one boot)."""
    samples: list[Sample]
    notes: list[Note] = field(default_factory=list)

    @property
    def start(self) -> int:
        return self.samples[0].time

    @property
    def end(self) -> int:
        return self.samples[-1].time

    @property
    def measured(self) -> list[Sample]:
        """The samples the drain is measured over (after the OCV settles)."""
        settled = [s for s in self.samples if s.time >= self.start + SETTLE]
        return settled if len(settled) >= 2 else self.samples

    def asleep(self) -> int:
        return sum(max(0, b.time - a.time - INTERVAL)
                   for a, b in zip(self.samples, self.samples[1:])
                   if b.time - a.time > INTERVAL * 1.25)

    def screen_on(self) -> int:
        return sum(min(INTERVAL, b.time - a.time)
                   for a, b in zip(self.samples, self.samples[1:]) if a.screen)

    def sleeps(self) -> int:
        return self.samples[-1].suspends - self.samples[0].suspends

    def drain(self) -> tuple[float, float, float] | None:
        """(start %, end %, % per hour) over the measured part."""
        m = self.measured
        hours = (m[-1].time - m[0].time) / 3600
        if hours <= 0:
            return None
        a, b = ocv_to_percent(m[0].ocv), ocv_to_percent(m[-1].ocv)
        return a, b, (a - b) / hours


def runs(samples: list[Sample], notes: list[Note]) -> list[Run]:
    out: list[Run] = []
    cur: list[Sample] = []
    for s in samples:
        if cur and (not s.usb and s.boot == cur[-1].boot):
            cur.append(s)
            continue
        if len(cur) >= 2:
            out.append(Run(cur))
        cur = [s] if not s.usb else []
    if len(cur) >= 2:
        out.append(Run(cur))
    out = [r for r in out if r.end - r.start >= MIN_RUN]
    # a note belongs to the run it was made in, or the next one
    prev_end = 0
    for r in out:
        r.notes = [n for n in notes if prev_end < n.time <= r.end]
        prev_end = r.end
    return out


def _dur(seconds: float) -> str:
    m = int(seconds) // 60
    return f"{m // 60}h{m % 60:02d}m" if m >= 60 else f"{m}m"


def _clock(t: int) -> str:
    return time.strftime("%b %d %H:%M", time.localtime(t))


def describe(run: Run) -> list[str]:
    span = run.end - run.start
    asleep, screen = run.asleep(), run.screen_on()
    lines = [f"{_clock(run.start)} -> {time.strftime('%H:%M', time.localtime(run.end))}  "
             f"{_dur(span)} on battery: asleep {100 * asleep / span:.0f}%, "
             f"screen on {100 * screen / span:.0f}%, {run.sleeps()} sleeps"]
    for n in run.notes:
        lines.append(f"  note: {n.text}")
    d = run.drain()
    m = run.measured
    if d is None:
        return lines
    a, b, rate = d
    lines.append(f"  charge {a:.1f}% -> {b:.1f}% (OCV {m[0].ocv} -> {m[-1].ocv} mV)")
    if rate <= 0:
        lines.append("  no measurable drain yet (too short)")
    else:
        lines.append(f"  drain {rate:.2f} %/h: a full charge would last about "
                     f"{100 / rate:.0f} h ({100 / rate / 24:.1f} days)")
        if a - b < 2:
            lines.append("  (under 2% measured: rough; leave it unplugged longer)")
    return lines


def report(text: str, count: int | None = 5) -> list[str]:
    samples, notes = parse(text)
    if not samples:
        return ["no samples in the log yet"]
    found = runs(samples, notes)
    last = samples[-1]
    lines = [f"now: {ocv_to_percent(last.ocv):.0f}% (OCV {last.ocv} mV, battery "
             f"{last.vbat} mV), {'on USB power' if last.usb else 'on battery'}; "
             f"logged since {_clock(samples[0].time)}"]
    if not found:
        lines.append(f"no stretches on battery of {MIN_RUN // 60}+ minutes yet")
    for r in (found if count is None else found[-count:]):
        lines.append("")
        lines.extend(describe(r))
    return lines
