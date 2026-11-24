"""Flash the phone, with guard rails."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Callable

from flip import backup, config, device, keys, profile
from flip.build import OUT
from flip.device import Mode
from flip.util import FlipError, say, sha256_file, warn, write_private


class Target(str, Enum):
    ALL = "all"  # signing key + vbmeta + toolbox (first install)
    TOOLBOX = "toolbox"  # lk2nd + toolbox only, wherever the current layout keeps it
    LINUX_FIRST = "linux-first"  # power-on starts lk2nd (Linux); volume-up starts KaiOS
    KAIOS_FIRST = "kaios-first"  # back to stock order: power-on KaiOS, volume-up lk2nd


BOOT_HELP = {
    "kaios-first": (
        "Power on: KaiOS. Toolbox / SD Linux: hold volume-up while powering on and "
        "release it when the 'custom operating system' warning appears."),
    "linux-first": (
        "Power on: lk2nd starts the SD card's Linux (or the toolbox if there's no card). "
        "KaiOS: hold volume-up while powering on, release it at the warning. Toolbox: "
        "press volume-up just after the warning disappears."),
}


def flashed_layout() -> str | None:
    try:
        layout = config.flashed_layout_record().read_text().strip()
    except FileNotFoundError:
        return None
    return layout if layout in profile.LAYOUTS else None


def record_layout(layout: str) -> None:
    config.ensure_config_dir()
    write_private(config.flashed_layout_record(), layout + "\n")


def layout_after(target: Target) -> str:
    if target is Target.LINUX_FIRST:
        return "linux-first"
    if target is Target.KAIOS_FIRST:
        return "kaios-first"
    # Nothing recorded: the phone has had the toolbox in recovery (the original layout).
    return flashed_layout() or "kaios-first"


def _toolbox(layout: str) -> tuple[str, Path, int]:
    if layout == "linux-first":
        return ("boot", OUT.linux_first_boot, profile.BOOT_SIZE)
    return ("recovery", OUT.recovery, profile.RECOVERY_SIZE)


def images(target: Target) -> list[tuple[str, Path, int]]:
    """(partition, image, partition size) in the order they're written."""
    if target is Target.LINUX_FIRST:
        # KaiOS goes to recovery first: if this stops halfway, power-on is still KaiOS.
        return [("recovery", OUT.linux_first_recovery, profile.RECOVERY_SIZE),
                ("dtbo", OUT.dtbo, profile.DTBO_SIZE),
                _toolbox("linux-first")]
    if target is Target.KAIOS_FIRST:
        stock = backup.resolve(None) / "parts"
        return [("boot", stock / "boot.img", profile.BOOT_SIZE),
                ("dtbo", stock / "dtbo.img", profile.DTBO_SIZE),
                _toolbox("kaios-first")]
    return [_toolbox(layout_after(target))]


def plan(target: Target) -> list[tuple[str, ...]]:
    """The fastboot commands a flash runs, in order."""
    steps = [("oem", "sudo")]
    if target is Target.ALL:
        steps += [
            ("flash", "avb_custom_key", str(OUT.pkmd)),
            ("flash", "vbmeta", str(OUT.vbmeta)),
        ]
    steps += [("flash", part, str(path)) for part, path, _ in images(target)]
    return steps


def preflight(target: Target) -> list[str]:
    """Check outputs, device mode and key consistency. Returns warnings."""
    parts = images(target)
    if target is Target.LINUX_FIRST and not OUT.linux_first_recovery.exists():
        raise FlipError(
            "linux-first-recovery.img (KaiOS for the recovery partition) is made from your "
            "backup: run `flip backup create` (toolbox: USB storage -> Whole eMMC), then "
            "`flip build`")
    needed = [p for _, p, _ in parts] + ([OUT.pkmd, OUT.vbmeta] if target is Target.ALL else [])
    missing = [p.name for p in needed if not p.exists()]
    if missing:
        raise FlipError(f"missing build outputs {missing}: run `flip build` first")

    for part, path, size in parts:
        if path.stat().st_size > size:
            raise FlipError(f"{path.name} is larger than the {part} partition")
    if target is Target.ALL and OUT.vbmeta.stat().st_size > profile.VBMETA_SIZE:
        raise FlipError("vbmeta.img is larger than the vbmeta partition")

    mode = device.detect_mode()
    if mode is Mode.LK2ND_FASTBOOT:
        raise FlipError(
            "the phone is in lk2nd's fastboot. Don't flash from here: lk2nd hides its "
            "own first 512 KiB, so partitions would land in the wrong place.\n"
            "   Power off, then hold volume-down while plugging in USB (stock fastboot)."
        )
    if mode is not Mode.STOCK_FASTBOOT:
        raise FlipError(
            f"phone not in stock fastboot (detected: {mode.value}).\n"
            "   Power off, then hold volume-down while plugging in USB."
        )

    warnings = []
    pkmd_sha = sha256_file(OUT.pkmd) if OUT.pkmd.exists() else None
    flashed = keys.flashed_pkmd()
    if target is not Target.ALL and pkmd_sha and flashed and pkmd_sha != flashed:
        warnings.append(
            "your current signing key differs from the one the phone trusts; "
            "the key isn't being flashed, so the phone keeps the old key"
        )
    if target is Target.ALL and flashed and pkmd_sha and pkmd_sha != flashed:
        warnings.append("this replaces the signing key the phone trusts with your current one")
    if target in (Target.LINUX_FIRST, Target.KAIOS_FIRST) and flashed_layout() is None:
        warnings.append("flip has no record of the phone's layout; assuming the toolbox "
                        "is in recovery (as after `flip flash all`)")
    return warnings


def flash(target: Target, confirm: Callable[[str], bool]) -> None:
    warnings = preflight(target)
    say(f"phone: {device.getvar('serialno') or 'unknown serial'} in stock fastboot")
    say("will run:")
    for cmd in plan(target):
        print("     fastboot " + " ".join(cmd))
    for w in warnings:
        warn(w)
    if not confirm("Flash the phone?"):
        raise FlipError("aborted, nothing was flashed")

    for cmd in plan(target):
        say("fastboot " + " ".join(cmd))
        print(device.fastboot_checked(*cmd), end="")
        if cmd[:2] == ("flash", "avb_custom_key"):
            keys.record_flashed(sha256_file(OUT.pkmd))
    layout = layout_after(target)
    record_layout(layout)
    say(f"flash complete. Layout: {layout}.")
    say(BOOT_HELP[layout])
