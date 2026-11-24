import pytest

from flip import build, device, flash, keys, profile
from flip.device import Mode
from flip.flash import Target
from flip.util import FlipError


@pytest.fixture
def outputs(tmp_path, monkeypatch):
    """Fake build outputs in a temp dir."""
    out = build.Outputs(
        pkmd=tmp_path / "pkmd.bin", vbmeta=tmp_path / "vbmeta.img",
        recovery=tmp_path / "recovery.img", dtbo=tmp_path / "dtbo.img",
        linux_first_boot=tmp_path / "linux-first-boot.img",
        linux_first_recovery=tmp_path / "linux-first-recovery.img",
    )
    out.pkmd.write_bytes(b"pkmd")
    out.vbmeta.write_bytes(b"v" * 4096)
    for p in (out.recovery, out.dtbo, out.linux_first_boot, out.linux_first_recovery):
        p.write_bytes(b"r" * 1024)
    monkeypatch.setattr(flash, "OUT", out)
    return out


@pytest.mark.parametrize("product,mode", [
    ("WND", Mode.STOCK_FASTBOOT),
    ("lk2nd-msm8952", Mode.LK2ND_FASTBOOT),
    ("something", Mode.OTHER_FASTBOOT),
])
def test_detect_fastboot_modes(fake_fastboot, product, mode):
    fake_fastboot(product)
    assert device.detect_mode() is mode


def test_detect_toolbox_and_none(fake_fastboot, monkeypatch):
    fake_fastboot(None)
    monkeypatch.setattr(device, "usb_ids", lambda: {profile.TOOLBOX_USB_ID})
    assert device.detect_mode() is Mode.TOOLBOX
    monkeypatch.setattr(device, "usb_ids", lambda: set())
    assert device.detect_mode() is Mode.NONE


def test_flash_refuses_lk2nd_fastboot(fake_fastboot, outputs, isolated_config):
    log = fake_fastboot("lk2nd-msm8952")
    with pytest.raises(FlipError, match="lk2nd's fastboot"):
        flash.flash(Target.TOOLBOX, confirm=lambda q: True)
    assert "flash" not in log.read_text()


def test_flash_refuses_without_phone(fake_fastboot, outputs, isolated_config, monkeypatch):
    fake_fastboot(None)
    monkeypatch.setattr(device, "usb_ids", lambda: set())
    with pytest.raises(FlipError, match="not in stock fastboot"):
        flash.flash(Target.TOOLBOX, confirm=lambda q: True)


def test_flash_aborts_without_confirmation(fake_fastboot, outputs, isolated_config):
    log = fake_fastboot("WND")
    with pytest.raises(FlipError, match="aborted"):
        flash.flash(Target.ALL, confirm=lambda q: False)
    assert "flash" not in log.read_text()


def test_flash_toolbox_only(fake_fastboot, outputs, isolated_config):
    log = fake_fastboot("WND")
    flash.flash(Target.TOOLBOX, confirm=lambda q: True)
    calls = [l for l in log.read_text().splitlines() if l.startswith(("oem", "flash"))]
    assert calls == ["oem sudo", f"flash recovery {outputs.recovery}"]
    assert keys.flashed_pkmd() is None  # key not flashed, nothing recorded


def test_flash_all_records_key(fake_fastboot, outputs, isolated_config):
    log = fake_fastboot("WND")
    flash.flash(Target.ALL, confirm=lambda q: True)
    calls = [l for l in log.read_text().splitlines() if l.startswith(("oem", "flash"))]
    assert calls == [
        "oem sudo",
        f"flash avb_custom_key {outputs.pkmd}",
        f"flash vbmeta {outputs.vbmeta}",
        f"flash recovery {outputs.recovery}",
    ]
    assert keys.flashed_pkmd() is not None
    assert (isolated_config / "flashed-pkmd.sha256").stat().st_mode & 0o077 == 0


def test_flash_requires_outputs(fake_fastboot, outputs, isolated_config):
    fake_fastboot("WND")
    outputs.recovery.unlink()
    with pytest.raises(FlipError, match="flip build"):
        flash.flash(Target.TOOLBOX, confirm=lambda q: True)


@pytest.fixture
def stock_backup(tmp_path, monkeypatch):
    b = tmp_path / "backups" / "2026-01-01-000000"
    (b / "parts").mkdir(parents=True)
    (b / "parts/boot.img").write_bytes(b"k" * 1024)
    (b / "parts/dtbo.img").write_bytes(b"d" * 1024)
    (b / "SHA256SUMS").write_text("")
    monkeypatch.setattr(flash.backup.config, "BACKUPS", tmp_path / "backups")
    return b


def _flashes(log):
    if not log.exists():  # fastboot never ran
        return []
    return [l for l in log.read_text().splitlines() if l.startswith("flash")]


def test_linux_first_moves_kaios_then_toolbox_and_remembers(fake_fastboot, outputs,
                                                            isolated_config):
    log = fake_fastboot("WND")
    flash.flash(Target.LINUX_FIRST, confirm=lambda q: True)
    # KaiOS reaches recovery before boot is overwritten: a stop halfway still starts KaiOS
    assert _flashes(log) == [
        f"flash recovery {outputs.linux_first_recovery}",
        f"flash dtbo {outputs.dtbo}",
        f"flash boot {outputs.linux_first_boot}",
    ]
    assert flash.flashed_layout() == "linux-first"
    log.write_text("")
    flash.flash(Target.TOOLBOX, confirm=lambda q: True)  # follows the layout
    assert _flashes(log) == [f"flash boot {outputs.linux_first_boot}"]


def test_linux_first_needs_kaios_from_backup(fake_fastboot, outputs, isolated_config):
    log = fake_fastboot("WND")
    outputs.linux_first_recovery.unlink()
    with pytest.raises(FlipError, match="backup"):
        flash.flash(Target.LINUX_FIRST, confirm=lambda q: True)
    assert _flashes(log) == []


def test_kaios_first_restores_stock_boot(fake_fastboot, outputs, isolated_config,
                                         stock_backup):
    log = fake_fastboot("WND")
    flash.record_layout("linux-first")
    flash.flash(Target.KAIOS_FIRST, confirm=lambda q: True)
    assert _flashes(log) == [
        f"flash boot {stock_backup / 'parts/boot.img'}",
        f"flash dtbo {stock_backup / 'parts/dtbo.img'}",
        f"flash recovery {outputs.recovery}",
    ]
    assert flash.flashed_layout() == "kaios-first"
