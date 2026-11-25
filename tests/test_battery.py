"""Battery logger (phone) and `flip battery` (src/flip/battery.py)."""

import re
import subprocess
from pathlib import Path

import pytest

from flip import battery, config, sd

OVERLAY = Path(sd.SD_SRC) / "overlay"
HEADER = "time,uptime,status,usb,vbat,ocv,capacity,screen,suspends\n"


def test_ocv_table_matches_the_kernel_battery_node():
    patch = (config.PATCHES / "linux/0001-arm64-dts-weeknd-battery-and-charger.patch").read_text()
    body = patch[patch.index("ocv-capacity-table-0"):]
    body = body[:body.index(";")]
    pairs = [(int(v) // 1000, int(p)) for v, p in re.findall(r"<(\d+) (\d+)>", body)]
    assert pairs == battery.OCV_TABLE


@pytest.mark.parametrize("mv,pct", [(4380, 100), (4500, 100), (2900, 0), (3846, 50),
                                    (4334, pytest.approx(97.2, abs=0.1))])
def test_ocv_to_percent(mv, pct):
    assert battery.ocv_to_percent(mv) == pct


def line(t, usb=0, ocv=4200, screen=0, susp=0):
    return f"{t},{t},{'Charging' if usb else 'Discharging'},{usb},{ocv - 50},{ocv},90,{screen},{susp}\n"


def test_runs_split_on_usb_power_and_reboots():
    text = HEADER + "#boot 0\n"
    text += "".join(line(t, usb=1) for t in range(0, 600, 60))
    text += "".join(line(t) for t in range(600, 3000, 60))         # run 1: 40 min
    text += line(3000, usb=1)
    text += "".join(line(t) for t in range(3060, 3200, 60))        # too short
    text += "#boot 4000\n"
    text += "".join(line(t) for t in range(4000, 6000, 60))        # run 2
    text += "6100,61,Disch"                                        # cut short by power loss
    samples, notes = battery.parse(text)
    rs = battery.runs(samples, notes)
    assert [(r.start, r.end) for r in rs] == [(600, 2940), (4000, 5980)]


def test_drain_sleep_and_screen():
    # 2 h on battery: awake 10 min with the screen on, asleep 100 min, 10 min awake
    rows, t = [], 0
    for i in range(10):
        rows.append(line(t, ocv=4298 - i, screen=1))
        t += 60
    t += 6000  # asleep
    for i in range(11):
        rows.append(line(t, ocv=4236 - i, susp=1))
        t += 60
    text = HEADER + "#boot 0\n#note 5 before cpufreq\n" + "".join(rows)
    (run,) = battery.runs(*battery.parse(text))
    assert run.asleep() == 6000 and run.sleeps() == 1
    assert run.screen_on() == 600
    start, end, rate = run.drain()
    # measured from 10 minutes in (OCV still settling after unplugging)
    assert run.measured[0].time >= battery.SETTLE
    assert start == pytest.approx(battery.ocv_to_percent(4236))
    assert rate > 0
    out = "\n".join(battery.report(text))
    assert "note: before cpufreq" in out and "asleep" in out and "%/h" in out


def test_report_with_nothing_unplugged():
    text = HEADER + "#boot 0\n" + "".join(line(t, usb=1) for t in range(0, 600, 60))
    out = battery.report(text)
    assert out[0].startswith("now:") and "no stretches" in out[1]
    assert battery.report("") == ["no samples in the log yet"]


def test_logger_writes_a_sample(tmp_path):
    bat, chg, bl, pw = (tmp_path / d for d in ("bat", "chg", "bl", "pw"))
    for d, files in ((bat, {"status": "Discharging", "voltage_now": "4101234",
                            "voltage_ocv": "4150000", "capacity": "78"}),
                     (chg, {"online": "0"}), (bl, {"bl_power": "4"}), (pw, {"success": "3"})):
        d.mkdir()
        for k, v in files.items():
            (d / k).write_text(v + "\n")
    log = tmp_path / "log/battery.csv"
    script = (OVERLAY / "usr/lib/flip/battery-log").read_text()
    script = (script.replace("/var/log/flip/battery.csv", str(log))
              .replace("/sys/class/power_supply/pm8916-bms-vm", str(bat))
              .replace("/sys/class/power_supply/pm8916-lbc-chgr", str(chg))
              .replace("/sys/class/backlight/backlight", str(bl))
              .replace("/sys/power/suspend_stats", str(pw))
              .replace("sleep 60", "exit 0"))
    subprocess.run(["bash", "-c", script], check=True)
    lines = log.read_text().splitlines()
    assert lines[0] + "\n" == HEADER and lines[1].startswith("#boot ")
    f = lines[2].split(",")
    assert f[2:] == ["Discharging", "0", "4101", "4150", "78", "0", "3"]
    (s,), _ = battery.parse(log.read_text())
    assert s.ocv == 4150 and not s.screen and not s.usb


def test_logger_is_enabled():
    unit = (OVERLAY / "etc/systemd/system/flip-battery-log.service").read_text()
    assert "ExecStart=/usr/lib/flip/battery-log" in unit
    assert "flip-battery-log.service" in (Path(sd.SD_SRC) / "setup.sh").read_text()
    assert (OVERLAY / "usr/lib/flip/battery-log").stat().st_mode & 0o111
