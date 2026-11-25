"""Screen power (sd/overlay/usr/lib/flip/screen) and lid/sleep settings."""

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

from flip import sd

OVERLAY = Path(sd.SD_SRC) / "overlay"


@pytest.fixture(scope="module")
def screen():
    sys.dont_write_bytecode = True  # no __pycache__ in the overlay (it goes on the card)
    loader = importlib.machinery.SourceFileLoader(
        "screen", str(OVERLAY / "usr/lib/flip/screen"))
    spec = importlib.util.spec_from_loader("screen", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_off_after_idle_and_on_with_a_key(screen):
    p = screen.Policy(30, now=0)
    assert p.screen_on(29) and p.wait(10) == 20
    assert not p.screen_on(30)
    p.key(100)
    assert p.screen_on(100) and not p.screen_on(131)


def test_lid_closed_is_off_until_opened(screen):
    p = screen.Policy(30, now=0)
    p.lid(True, 5)
    assert not p.screen_on(5) and p.wait(5) is None  # only input changes it
    p.key(6)  # keys while closed don't turn it on
    assert not p.screen_on(6)
    p.lid(False, 500)
    assert p.screen_on(500) and p.screen_on(529)  # opening counts as use


def test_idle_off_zero_means_never(screen):
    p = screen.Policy(0, now=0)
    assert p.screen_on(10_000) and p.wait(10) is None


def test_no_busy_loop_once_the_screen_is_off(screen):
    p = screen.Policy(30, now=0)
    assert p.wait(40) is None  # nothing left to time: wait for a key


def test_sleeps_after_15_minutes_idle_with_the_lid_open(screen):
    p = screen.Policy(30, now=0, idle_sleep=900)
    assert p.wait(0) == 30 and p.wait(40) == 860
    assert not p.sleep_due(899) and p.sleep_due(900)
    p.postpone_sleep(900, 900)  # asleep; the monotonic clock stops meanwhile
    assert not p.sleep_due(901) and p.wait(901) == 899
    assert p.sleep_due(1800)  # woke without a key press (e.g. alarm): sleep again
    p.key(1000)
    assert not p.sleep_due(1899) and p.sleep_due(1900)


def test_lid_closed_leaves_sleep_to_logind(screen):
    p = screen.Policy(30, now=0, idle_sleep=900)
    p.lid(True, 1)
    assert not p.sleep_due(5000) and p.wait(5000) is None


def test_config(screen, tmp_path):
    missing = screen.load_config(tmp_path / "missing")
    assert missing == {"idle_off": 600, "idle_sleep": 900}
    assert screen.load_config(OVERLAY / "etc/flip/screen.conf") == missing
    bad = tmp_path / "screen.conf"
    bad.write_text("[screen]\nidle_off = soon\nidle_sleep = 0\n")
    assert screen.load_config(bad) == {"idle_off": 600, "idle_sleep": 0}


def test_usb_power_check(screen, tmp_path):
    d = tmp_path / "pm8916-lbc-chgr"
    d.mkdir()
    (d / "type").write_text("USB\n")
    (d / "online").write_text("0\n")
    assert not screen.usb_power_online(str(tmp_path))
    (d / "online").write_text("1\n")
    assert screen.usb_power_online(str(tmp_path))


def test_waits_for_the_startup_animation(screen, tmp_path):
    import os
    pid = tmp_path / "splash.pid"
    assert not screen.splash_running(str(pid))
    pid.write_text(f"{os.getpid()}\n")
    assert screen.splash_running(str(pid))
    pid.write_text("999999999\n")  # left behind by a crash
    assert not screen.splash_running(str(pid))


def test_switch_ioctl_number(screen):
    # EVIOCGSW(8) from <linux/input.h>
    assert screen.eviocgsw(8) == 0x8008451B


def test_lid_sleeps_unless_on_usb_power():
    conf = (OVERLAY / "etc/systemd/logind.conf.d/flip.conf").read_text()
    assert "HandleLidSwitch=suspend" in conf
    assert "HandleLidSwitchExternalPower=ignore" in conf  # flip's USB link stays up
    assert "HandlePowerKey=ignore" in conf


def test_kernel_can_sleep_and_sees_the_lid():
    from flip import config
    defconfig = config.DEFCONFIG.read_text()
    assert "CONFIG_SUSPEND=y" in defconfig.splitlines()
    patch = (config.PATCHES / "linux/0003-arm64-dts-weeknd-lid-switch.patch").read_text()
    assert "SW_LID" in patch and "msmgpio 44" in patch and "wakeup-source" in patch
    assert "flip-screen.service" in (Path(sd.SD_SRC) / "setup.sh").read_text()
