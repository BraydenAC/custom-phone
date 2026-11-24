"""The phone's startup animation (sd/overlay/usr/lib/flip/splash)."""

import importlib.machinery
import importlib.util
from pathlib import Path

import pytest

from flip import sd

OVERLAY = Path(sd.SD_SRC) / "overlay"
W, H = 240, 320


@pytest.fixture(scope="module")
def splash():
    import sys
    sys.dont_write_bytecode = True  # no __pycache__ in the overlay (it goes on the card)
    loader = importlib.machinery.SourceFileLoader(
        "splash", str(OVERLAY / "usr/lib/flip/splash"))
    spec = importlib.util.spec_from_loader("splash", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def screen(splash):
    return splash.Screen(bytearray(W * H * 4), W, H, W * 4)


def pixel(scr, x, y):
    o = y * scr.stride + x * 4
    return int.from_bytes(scr.buf[o:o + 4], "little")


def test_config_defaults_to_bloom_and_shipped_file_agrees(splash, tmp_path):
    assert splash.load_config(tmp_path / "missing.conf")["style"] == "bloom"
    shipped = splash.load_config(OVERLAY / "etc/flip/splash.conf")
    assert shipped["style"] == "bloom" and shipped["hex_size"] == 15


@pytest.mark.parametrize("text,style", [("rise", "rise"), ("RISE ", "rise"),
                                        ("sparkles", "bloom")])
def test_config_style_switch(splash, tmp_path, text, style):
    conf = tmp_path / "splash.conf"
    conf.write_text(f"[splash]\nstyle = {text}\nhex_size = banana\nfps = 99\n")
    cfg = splash.load_config(conf)
    assert cfg["style"] == style
    assert cfg["hex_size"] == 15  # bad numbers fall back, out-of-range ones clamp
    assert cfg["fps"] == 30


@pytest.mark.parametrize("style", ["bloom", "rise"])
def test_hexagons_fill_the_whole_screen(splash, style):
    scr = screen(splash)
    scr.fill(0x123456)
    cells = splash.layout(style, 15, W, H)
    splash.paint_cells(scr, cells, [splash.BLACK] * len(cells))
    stray = sum(1 for y in range(H) for x in range(W) if pixel(scr, x, y) == 0x123456)
    covered = sum(1 for y in range(H) for x in range(W) if pixel(scr, x, y) == splash.BLACK)
    # everything except the thin gaps between hexagons
    assert covered > 0.8 * W * H and stray < 0.2 * W * H


def test_bloom_starts_in_the_middle_rise_at_the_bottom(splash):
    for style, inside, outside in (("bloom", (120, 150), (120, 5)),
                                   ("rise", (120, 315), (120, 5))):
        scr = screen(splash)
        scr.fill(splash.ORANGE)
        cells = splash.layout(style, 15, W, H)
        splash.paint_cells(scr, cells, splash.frame_colors(cells, 0.2, 0, None))
        near = [pixel(scr, inside[0] + dx, inside[1]) for dx in range(-12, 13)]
        assert splash.BLACK in near, style
        assert pixel(scr, *outside) == splash.ORANGE, style


def test_runs_to_ready_then_stops(splash):
    class FakeProgress:
        def __init__(self):
            self.p = 0.0

        def update(self):
            self.p = min(1.0, self.p + 0.05)
            return self.p

        def label(self):
            return "WI-FI..."

    now = [0.0]

    def sleep(s):
        now[0] += max(s, 1 / 15)

    cfg = splash.load_config("/nonexistent")
    scr = screen(splash)
    frames = splash.run(scr, cfg, FakeProgress(), sleep=sleep, clock=lambda: now[0])
    assert 20 < frames < 100  # 20 frames to finish, then the finish and the hold
    assert pixel(scr, W // 2, H // 2) == splash.BLACK or pixel(scr, W // 2, H // 2) == splash.ORANGE


def test_font_has_every_label(splash):
    for name, _unit in splash.STEPS:
        assert set(name + "...READY") <= set(splash.FONT), name
    for glyph in splash.FONT.values():
        rows = glyph.split("|")
        assert len(rows) == 7 and all(len(r) == 5 for r in rows)


def test_screen_starts_before_slow_mounts():
    """The panel only needs / (mounted by the kernel); waiting for local-fs.target
    meant waiting for /boot on the card, ~6 s, with the screen off."""
    panel = (OVERLAY / "etc/systemd/system/flip-panel.service").read_text()
    settings = [l for l in panel.splitlines() if not l.startswith("#")]
    assert [l for l in settings if l.startswith("After=")] == ["After=flip-splash.service"]
    unit = (OVERLAY / "etc/systemd/system/flip-splash.service").read_text()
    # the console must be off the screen before the screen starts
    assert "DefaultDependencies=no" in unit and "Type=notify" in unit
    assert "After=" not in unit and "TimeoutStartSec=" in unit
    assert "flip-splash.service" in (Path(sd.SD_SRC) / "setup.sh").read_text()


def test_redraws_everything_after_the_console_took_the_screen(splash):
    class Done:
        def update(self):
            return 1.0

        def label(self):
            return ""

    now = [0.0]
    scr = screen(splash)
    taken = iter([False, False, True] + [False] * 200)

    def sleep(s):
        now[0] += 1 / 15
        if now[0] > 0.1 and now[0] < 0.2:  # the console draws something
            scr.rect(0, 0, 50, 50, 0x00FF00)

    cfg = dict(splash.load_config("/nonexistent"), hold=0)
    splash.run(scr, cfg, Done(), sleep=sleep, clock=lambda: now[0],
               keep_screen=lambda: next(taken))
    assert pixel(scr, 10, 10) != 0x00FF00  # repainted over the console's text


def test_login_prompt_doesnt_reset_the_screen():
    """systemd's tty reset for getty@tty1 switches tty1 to text mode, and the
    login prompt then drew over the animation."""
    dropin = (OVERLAY / "etc/systemd/system/getty@tty1.service.d/flip-splash.conf").read_text()
    assert "TTYReset=no" in dropin and "TTYVTDisallocate=no" in dropin


def test_label_leaves_nothing_behind_at_the_end(splash):
    """Cells don't cover the gaps between them: the label's box and letters
    stayed in the gaps after the final repaint."""
    class Steps:
        def __init__(self):
            self.n = 0

        def update(self):
            self.n += 1
            return 0.6 if self.n < 5 else 1.0

        def label(self):
            return "WI-FI..."

    now = [0.0]

    def sleep(s):
        now[0] += 1 / 15

    scr = screen(splash)
    cfg = dict(splash.load_config("/nonexistent"), hold=0)
    splash.run(scr, cfg, Steps(), sleep=sleep, clock=lambda: now[0])
    x, y, w, h = splash.label_box(scr)
    cells = splash.layout(cfg["style"], cfg["hex_size"], W, H)
    in_cells = {(xx, yy) for c in cells for yy, x0, x1 in c["spans"] for xx in range(x0, x1)}
    gaps = [(xx, yy) for yy in range(y, y + h) for xx in range(x, x + w)
            if (xx, yy) not in in_cells]
    assert gaps and all(pixel(scr, *g) == splash.ORANGE for g in gaps)


# --- shutdown -----------------------------------------------------------------

class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.now += max(s, 1 / 15)


def shutdown(splash, kind, stopped_after=1.0, usb=lambda: False, **cfg):
    import random
    clock = Clock()
    conf = dict(splash.load_config("/nonexistent"), **cfg)
    scr = screen(splash)
    seen = []

    def stopped():
        seen.append(clock.now)
        return clock.now >= stopped_after

    ending = splash.run_shutdown(scr, conf, kind, random.Random(1), sleep=clock.sleep,
                                 clock=clock, stopped=stopped, usb=usb)
    return ending, scr, clock


def test_reboot_says_restarting_and_never_asks_about_usb(splash):
    asked = []
    ending, scr, _ = shutdown(splash, "reboot", usb=lambda: asked.append(1) or True)
    assert ending == "RESTARTING" and not asked


def test_poweroff_without_cable_says_off(splash):
    ending, scr, clock = shutdown(splash, "poweroff")
    assert ending == "OFF"
    assert clock.now < 6  # shutdown_length 3 s, plus the 0.2 s gap


def test_poweroff_waits_for_the_cable_to_come_out(splash):
    plugged = iter([True] * 20 + [False] * 100)
    ending, _, clock = shutdown(splash, "poweroff", usb=lambda: next(plugged))
    assert ending == "OFF"


def test_poweroff_gives_up_on_the_cable_after_unplug_wait(splash):
    ending, _, clock = shutdown(splash, "poweroff", usb=lambda: True, unplug_wait=8)
    assert ending == "OFF (STILL PLUGGED IN)"
    assert 8 <= clock.now < 14


def test_last_hexagons_wait_until_services_have_stopped(splash):
    _, _, clock = shutdown(splash, "reboot", stopped_after=9.0)
    assert clock.now >= 9.0  # not done at shutdown_length (3 s): services still stopping


def test_dark_hexagons_match_the_gaps(splash):
    """Each hexagon goes to the same black as the gaps between them."""
    _, scr, _ = shutdown(splash, "reboot")
    corner = [pixel(scr, x, y) for y in range(0, 60) for x in range(0, 60)]
    assert set(corner) == {splash.BLACK}


def test_spread_order_is_random_without_clumps(splash):
    import random
    cells = [{"x": x * 26.0, "y": y * 22.0} for y in range(15) for x in range(10)]
    a = splash.spread_order(cells, random.Random(1))
    b = splash.spread_order(cells, random.Random(2))
    assert sorted(map(id, a)) == sorted(map(id, cells)) and a != b
    close = sum(1 for p, q in zip(a, a[1:]) if abs(p["x"] - q["x"]) + abs(p["y"] - q["y"]) < 30)
    assert close < len(a) * 0.05  # next to the previous one only rarely


def test_usb_power_is_the_charger_input(splash, tmp_path):
    def supply(name, kind, online):
        d = tmp_path / name
        d.mkdir()
        (d / "type").write_text(kind + "\n")
        (d / "online").write_text(online + "\n")

    supply("pm8916-bms-vm", "Battery", "1")
    supply("pm8916-lbc-chgr", "USB", "0")
    assert not splash.usb_power_online(str(tmp_path))
    (tmp_path / "pm8916-lbc-chgr/online").write_text("1\n")
    assert splash.usb_power_online(str(tmp_path))


def test_shutdown_units_and_hook():
    import subprocess
    unit = (OVERLAY / "etc/systemd/system/flip-splash-shutdown@.service").read_text()
    # "@" argv[0]: spared by systemd-shutdown's final killing spree
    assert "ExecStart=@/usr/bin/python3 @flip-splash " in unit
    assert "WantedBy=%i.target" in unit
    assert "Slice=system.slice" in unit  # a template's own slice stops at shutdown
    hook = OVERLAY / "usr/lib/systemd/system-shutdown/flip-splash"
    assert "@flip-splash" in hook.read_text() and hook.stat().st_mode & 0o111
    assert subprocess.run(["sh", "-n", str(hook)]).returncode == 0
    setup = (Path(sd.SD_SRC) / "setup.sh").read_text()
    assert "flip-splash-shutdown@poweroff.service flip-splash-shutdown@reboot.service" in setup
