"""Android image tools (vendored in tools/) and the recovery image layout."""

from __future__ import annotations

import sys
from pathlib import Path

from flip import config, profile
from flip.util import FlipError, run


def _tool(script: str, *args, quiet: bool = False) -> None:
    run([sys.executable, "-I", config.TOOLS / script, *args], quiet=quiet, capture=quiet)


def mkbootimg(out: Path, kernel: Path, cmdline: str, *, ramdisk: Path | None = None,
              header_version: int | None = None, recovery_dtbo: Path | None = None) -> None:
    args = ["-o", out, "--kernel", kernel, "--cmdline", cmdline]
    if ramdisk:
        args += ["--ramdisk", ramdisk]
    if header_version is not None:
        args += ["--header_version", str(header_version)]
    if recovery_dtbo:
        args += ["--recovery_dtbo", recovery_dtbo]
    _tool("mkbootimg.py", *args)


def mkdtboimg(out: Path, dtbs: list[Path]) -> None:
    _tool("mkdtboimg.py", "create", out, *dtbs)


def avb_extract_public_key(key: Path, out: Path, quiet: bool = False) -> None:
    _tool("avbtool.py", "extract_public_key", "--key", key, "--output", out, quiet=quiet)


def avb_make_vbmeta(out: Path, key: Path, pkmd: Path, stock_vbmeta: Path) -> None:
    _tool(
        "avbtool.py", "make_vbmeta_image",
        "--padding_size", "4096",
        "--algorithm", "SHA512_RSA2048",
        "--key", key,
        "--public_key_metadata", pkmd,
        "--output", out,
        "--include_descriptors_from_image", stock_vbmeta,
        "--set_hashtree_disabled_flag",
    )


def assemble_lk2nd_toolbox(lk2nd_img: bytes, boot_img: bytes,
                           size: int = profile.RECOVERY_SIZE, part: str = "recovery") -> bytes:
    """lk2nd in the first LK2ND_SLOT bytes, the toolbox boot image after it."""
    if len(lk2nd_img) > profile.LK2ND_SLOT:
        raise FlipError(
            f"lk2nd image is {len(lk2nd_img)} bytes, larger than its "
            f"{profile.LK2ND_SLOT}-byte slot: the toolbox would overwrite it"
        )
    image = lk2nd_img.ljust(profile.LK2ND_SLOT, b"\0") + boot_img
    if len(image) > size:
        raise FlipError(
            f"lk2nd + toolbox image is {len(image)} bytes, larger than the "
            f"{size}-byte {part} partition"
        )
    return image


# Android boot image header v1 (system/tools/mkbootimg/include/bootimg/bootimg.h)
_CMDLINE = slice(64, 64 + 512)
_RECOVERY_DTBO = 1632  # u32 size, u64 offset, u32 header size
DTBO_MAGIC = 0xD7B7AB1E


def kaios_for_recovery(stock_boot: bytes, stock_dtbo: bytes, system_partuuid: str) -> bytes:
    """The stock KaiOS boot image, made to start from the recovery partition.

    The bootloader starts recovery with the image's own recovery_dtbo instead of
    the dtbo partition (which then holds lk2nd's overlays), so the stock
    overlays go in there. KaiOS has no ramdisk: its root is the system
    partition, named on the command line in case the bootloader only adds it
    for a normal boot.
    """
    import struct

    if stock_boot[:8] != b"ANDROID!":
        raise FlipError("the backup's boot.img is not an Android boot image")
    kernel_size, _, ramdisk_size, _, second_size, _, _, page, version = struct.unpack_from(
        "<9I", stock_boot, 8)
    if version != 1 or ramdisk_size or second_size:
        raise FlipError(f"unexpected stock boot image (header v{version}, ramdisk "
                        f"{ramdisk_size}, second {second_size}); not repacking it")
    magic, dtbo_size = struct.unpack_from(">II", stock_dtbo, 0)
    if magic != DTBO_MAGIC:
        raise FlipError("the backup's dtbo.img is not a device tree overlay image")

    def pages(n: int) -> int:
        return (n + page - 1) // page * page

    header = bytearray(stock_boot[:page])
    cmdline = bytes(header[_CMDLINE]).split(b"\0")[0].decode()
    root = f"root=PARTUUID={system_partuuid.lower()}"
    if "root=" not in cmdline:
        cmdline = f"{cmdline} {root} rootwait"
    if len(cmdline) >= 512:
        raise FlipError("KaiOS command line too long to add the root partition")
    header[_CMDLINE] = cmdline.encode().ljust(512, b"\0")
    kernel = stock_boot[page:page + kernel_size]
    dtbo_offset = page + pages(kernel_size)
    struct.pack_into("<IQ", header, _RECOVERY_DTBO, dtbo_size, dtbo_offset)
    return (bytes(header) + kernel.ljust(pages(kernel_size), b"\0")
            + stock_dtbo[:dtbo_size].ljust(pages(dtbo_size), b"\0"))
