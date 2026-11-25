"""Build the kernel, lk2nd, the toolbox and the signed images."""

from __future__ import annotations

import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

from flip import backup, config, fetch, images, initramfs, keys, lock, profile
from flip.fingerprint import Stamp, fingerprint
from flip.util import FlipError, run, say, sha256_file, warn

HOST_TOOLS = ["gcc", "make", "git", "bc", "flex", "bison", "perl", "dtc", "openssl", "depmod"]


@dataclass(frozen=True)
class Outputs:
    kernel: Path = config.OUT / "Image.gz-dtb"  # toolbox: kernel + appended dtb
    kernel_image: Path = config.OUT / "Image.gz"  # SD card: kernel and dtb separate
    dtb: Path = config.OUT / "qm215-nokia-weeknd.dtb"
    modules: Path = config.OUT / "modules"  # lib/modules/<release>/ for the SD card
    kernel_release: Path = config.OUT / "kernel-release"
    initramfs: Path = config.OUT / "initramfs.cpio.gz"
    boot: Path = config.OUT / "boot.img"  # toolbox boot image (kaios-first layout)
    dtbo: Path = config.OUT / "dtbo.img"
    lk2nd_head: Path = config.OUT / "lk2nd-head.img"
    recovery: Path = config.OUT / "recovery.img"  # kaios-first: lk2nd + toolbox -> recovery
    # linux-first layout (profile.LAYOUTS)
    boot_lf: Path = config.OUT / "boot-linux-first.img"  # toolbox boot image
    lk2nd_head_lf: Path = config.OUT / "lk2nd-head-linux-first.img"
    linux_first_boot: Path = config.OUT / "linux-first-boot.img"  # lk2nd + toolbox -> boot
    # KaiOS -> recovery, made from your backup's boot and dtbo (not shareable)
    linux_first_recovery: Path = config.OUT / "linux-first-recovery.img"
    pkmd: Path = config.OUT / "pkmd.bin"
    vbmeta: Path = config.OUT / "vbmeta.img"


OUT = Outputs()


def lk2nd_build_dir(layout: str) -> Path:
    """lk2nd is built once per layout (its partition differs), each in its own dir.

    lk also copies emmc_appsboot.mbn two levels above BOOTLOADER_OUT (the build
    dir's parent): with lk2nd/out/<layout>/lk that is lk2nd/out, not this repo.
    """
    return config.LK2ND_DIR / "out" / layout / "lk" / f"build-{profile.LK2ND_TARGET}"


def missing_tools() -> list[str]:
    needed = [config.cross_compile() + "gcc", config.toolchain_prefix() + "gcc", *HOST_TOOLS]
    return [t for t in needed if shutil.which(t) is None]


def check_tools() -> None:
    missing = missing_tools()
    if missing:
        raise FlipError(
            "missing build tools: " + " ".join(missing) + "\n"
            "   On Arch: sudo pacman -S --needed aarch64-linux-gnu-gcc arm-none-eabi-gcc \\\n"
            "            base-devel git bc flex bison perl dtc openssl"
        )


def step(n: int, name: str) -> None:
    say(f"[{n}] {name}")


# --- kernel ------------------------------------------------------------------

def linux_patches() -> list[Path]:
    return sorted((config.PATCHES / "linux").glob("*.patch"))


def kernel_fingerprint(pins: lock.Lock) -> str:
    return fingerprint(
        f"kernel-v3 {pins.git['linux'].commit} {config.cross_compile()}",
        [config.DEFCONFIG, *linux_patches()],
    )


def apply_linux_patches() -> None:
    """Put the pinned kernel source back to its commit, then apply patches/linux/.

    Only tracked files are reset (git checkout rewrites just the ones that
    differ), so the in-tree build stays incremental. Local edits in linux/ are
    discarded: put changes in patches/linux/ instead.
    """
    run(["git", "checkout", "-q", "--", "."], cwd=config.LINUX_DIR)
    for patch in linux_patches():
        say(f"applying {patch.name}")
        run(["git", "apply", patch], cwd=config.LINUX_DIR)


def build_kernel(pins: lock.Lock, jobs: int) -> None:
    fetch.fetch_git(pins.git["linux"], config.LINUX_DIR)
    stamp = Stamp("kernel")
    fp = kernel_fingerprint(pins)
    outputs = [OUT.kernel, OUT.kernel_image, OUT.dtb, OUT.modules, OUT.kernel_release]
    if all(o.exists() for o in outputs) and stamp.matches(fp):
        say(f"OK       {OUT.kernel.name} (inputs unchanged)")
        return
    if OUT.kernel.exists():
        say("kernel inputs changed, rebuilding")
    apply_linux_patches()
    shutil.copyfile(config.DEFCONFIG, config.LINUX_DIR / ".config")
    env = {"ARCH": "arm64", "CROSS_COMPILE": config.cross_compile()}
    # defconfig was saved with GCC 12.2; fill in options a newer compiler adds
    # with their defaults instead of prompting.
    run(["make", "olddefconfig"], cwd=config.LINUX_DIR, env=env)
    run(["make", f"-j{jobs}"], cwd=config.LINUX_DIR, env=env)
    image = (config.LINUX_DIR / "arch/arm64/boot/Image.gz").read_bytes()
    dtb = (config.LINUX_DIR / profile.KERNEL_DTB).read_bytes()
    OUT.kernel.parent.mkdir(parents=True, exist_ok=True)
    OUT.kernel.write_bytes(image + dtb)
    OUT.kernel_image.write_bytes(image)
    OUT.dtb.write_bytes(dtb)

    # Modules (Wi-Fi, audio, modem, zram, ...) for the SD card system.
    release = run(["make", "-s", "kernelrelease"], cwd=config.LINUX_DIR, env=env,
                  capture=True, quiet=True).stdout.strip()
    if OUT.modules.exists():
        shutil.rmtree(OUT.modules)
    run(["make", "modules_install", f"INSTALL_MOD_PATH={OUT.modules}", "INSTALL_MOD_STRIP=1"],
        cwd=config.LINUX_DIR, env=env)
    # Drop the build/source symlinks back into this checkout.
    for link in ("build", "source"):
        (OUT.modules / "lib/modules" / release / link).unlink(missing_ok=True)
    OUT.kernel_release.write_text(release + "\n")
    stamp.record(fp)


# --- lk2nd -------------------------------------------------------------------

def lk2nd_patches() -> list[Path]:
    return sorted((config.PATCHES / "lk2nd").glob("*.patch"))


def lk2nd_fingerprint(pins: lock.Lock, layout: str) -> str:
    dts = sorted(p for p in config.LK2ND_DTS.iterdir() if p.is_file())
    return fingerprint(
        f"lk2nd-v1 {pins.git['lk2nd'].commit} {config.toolchain_prefix()} "
        + " ".join(profile.LK2ND_MAKE_ARGS[layout]),
        lk2nd_patches() + dts,
    )


def build_lk2nd(pins: lock.Lock, jobs: int, layout: str) -> Path:
    src = config.LK2ND_DIR
    fetch.fetch_git(pins.git["lk2nd"], src)
    build_dir = lk2nd_build_dir(layout)
    out = build_dir / "lk.bin-dtb"
    stamp = Stamp(f"lk2nd-{layout}")
    fp = lk2nd_fingerprint(pins, layout)
    if out.exists() and stamp.matches(fp):
        say(f"OK       lk.bin-dtb for {layout} (inputs unchanged)")
        return out
    if out.exists():
        say(f"lk2nd ({layout}) inputs changed, rebuilding")

    # Start from the pristine pinned source, so the result depends only on the
    # pin and files in this repo. Local edits in lk2nd/ are discarded: put
    # changes in patches/lk2nd/ instead.
    dts_link = src / "lk2nd/device/dts/msm8952"
    if dts_link.is_symlink():
        dts_link.unlink()
    run(["git", "checkout", "-q", "--", "."], cwd=src)
    for patch in lk2nd_patches():
        say(f"applying {patch.name}")
        run(["git", "apply", patch], cwd=src)
    shutil.rmtree(dts_link)
    dts_link.symlink_to(Path("../../../..") / config.LK2ND_DTS.name)

    # Clean build: compile-time options don't reliably trigger rebuilds in lk.
    if build_dir.exists():
        shutil.rmtree(build_dir)
    run(["make", f"-j{jobs}", profile.LK2ND_TARGET, f"BOOTLOADER_OUT={build_dir.parent}",
         *profile.LK2ND_MAKE_ARGS[layout]], cwd=src,
        env={"TOOLCHAIN_PREFIX": config.toolchain_prefix()})
    stamp.record(fp)
    return out


# --- toolbox -----------------------------------------------------------------

def build_reboot_mode() -> Path:
    """toolbox/reboot-mode.c, static, against the kernel tree's nolibc."""
    out = config.OUT / "reboot-mode"
    run([config.cross_compile() + "gcc", "-Os", "-static", "-nostdlib", "-s",
         "-fno-asynchronous-unwind-tables", "-fno-ident", "-Wl,--build-id=none",
         "-include", config.LINUX_DIR / "tools/include/nolibc/nolibc.h",
         "-o", out, config.REPO / "toolbox/reboot-mode.c", "-lgcc"])
    return out


def toolbox_entries(busybox: Path, reboot_mode: Path) -> list[initramfs.Entry]:
    # rootfs/sbin is generated by the old shell build; ignore it if present.
    entries = initramfs.tree_entries(config.ROOTFS, exclude={"sbin"})
    entries.append(initramfs.Entry("sbin", stat.S_IFDIR | 0o755))
    entries.append(initramfs.Entry("sbin/busybox", stat.S_IFREG | 0o755, busybox.read_bytes()))
    entries.append(initramfs.Entry("sbin/reboot-mode", stat.S_IFREG | 0o755,
                                   reboot_mode.read_bytes()))
    for applet in profile.BUSYBOX_APPLETS:
        entries.append(initramfs.Entry(f"sbin/{applet}", stat.S_IFLNK | 0o777, b"busybox"))
    return entries


def build_toolbox(pins: lock.Lock) -> None:
    busybox = fetch.fetch_file(pins.file["busybox"])
    OUT.initramfs.write_bytes(initramfs.build(toolbox_entries(busybox, build_reboot_mode())))
    for layout, out in (("kaios-first", OUT.boot), ("linux-first", OUT.boot_lf)):
        images.mkbootimg(out, OUT.kernel, profile.boot_cmdline(layout), ramdisk=OUT.initramfs)


def build_dtbo() -> None:
    dtbs = []
    for name in profile.OVERLAYS:
        dtb = config.OUT / f"overlay-{name}.dtb"
        run(["dtc", "-q", "-o", dtb, config.REPO / f"overlay-{name}.dts"])
        dtbs.append(dtb)
    images.mkdtboimg(OUT.dtbo, dtbs)


def build_lk2nd_image(lk_bin: Path, layout: str) -> None:
    """lk2nd + toolbox, for recovery (kaios-first) or boot (linux-first)."""
    head, toolbox, out, size, part = {
        "kaios-first": (OUT.lk2nd_head, OUT.boot, OUT.recovery, profile.RECOVERY_SIZE,
                        "recovery"),
        "linux-first": (OUT.lk2nd_head_lf, OUT.boot_lf, OUT.linux_first_boot,
                        profile.BOOT_SIZE, "boot"),
    }[layout]
    images.mkbootimg(head, lk_bin, "lk2nd", header_version=1, recovery_dtbo=OUT.dtbo)
    image = images.assemble_lk2nd_toolbox(head.read_bytes(), toolbox.read_bytes(), size, part)
    out.write_bytes(image)
    say(f"{out.name} ({part}): lk2nd {head.stat().st_size}/{profile.LK2ND_SLOT} bytes, "
        f"total {len(image)}/{size} bytes")


def build_kaios_recovery() -> None:
    """KaiOS for the recovery partition (linux-first), from the newest backup."""
    backups = backup.list_backups()
    if not backups:
        say("no backup yet: skipping linux-first-recovery.img (only needed for "
            "`flip flash linux-first`)")
        return
    src = backups[-1]
    image = images.kaios_for_recovery(
        (src / "parts/boot.img").read_bytes(), (src / "parts/dtbo.img").read_bytes(),
        backup.system_partuuid(src))
    if len(image) > profile.RECOVERY_SIZE:
        raise FlipError("KaiOS image is larger than the recovery partition")
    OUT.linux_first_recovery.write_bytes(image)
    OUT.linux_first_recovery.chmod(0o600)
    say(f"{OUT.linux_first_recovery.name}: KaiOS from backup {src.name}, {len(image)} bytes")


# --- vbmeta ------------------------------------------------------------------

def stock_vbmeta(pins: lock.Lock, override: Path | None) -> Path:
    author = pins.file["author-vbmeta"]
    if override:
        if not override.is_file():
            raise FlipError(f"stock vbmeta {override} does not exist")
        path = override
    elif config.own_vbmeta().is_file():
        path = config.own_vbmeta()
    else:
        path = fetch.fetch_file(author)
        warn("using the original author's stock vbmeta (firmware 00.2238.20.13). "
             "Run `flip backup adopt-vbmeta` after a backup to use your own.")
        return path
    sha = sha256_file(path)
    same = " (identical to the author's copy)" if sha == author.sha256 else ""
    say(f"stock vbmeta: {path}{same}")
    return path


def build_vbmeta(pins: lock.Lock, stock_override: Path | None) -> None:
    stock = stock_vbmeta(pins, stock_override)
    key = keys.ensure_key()
    pkmd_sha = keys.derive_pkmd(key, OUT.pkmd)
    say(f"signing key pkmd sha256: {pkmd_sha}")
    flashed = keys.flashed_pkmd()
    if flashed and flashed != pkmd_sha:
        warn("this signing key differs from the one last flashed to the phone. "
             "`flip flash` (all) installs it; until then the phone rejects these images.")
    images.avb_make_vbmeta(OUT.vbmeta, key, OUT.pkmd, stock)


# --- all ---------------------------------------------------------------------

def build_all(jobs: int, stock_override: Path | None = None) -> None:
    check_tools()
    pins = lock.load()
    config.ensure_config_dir()  # before looking up the key / own vbmeta in it
    config.OUT.mkdir(exist_ok=True)
    step(1, "kernel")
    build_kernel(pins, jobs)
    step(2, "lk2nd")
    lk_bins = {layout: build_lk2nd(pins, jobs, layout) for layout in profile.LAYOUTS}
    step(3, "toolbox (initramfs + boot image)")
    build_toolbox(pins)
    step(4, "device tree overlays")
    build_dtbo()
    step(5, "lk2nd + toolbox images")
    for layout in profile.LAYOUTS:
        build_lk2nd_image(lk_bins[layout], layout)
    build_kaios_recovery()
    step(6, "vbmeta")
    build_vbmeta(pins, stock_override)
    say("done. Outputs in out/: recovery.img, vbmeta.img, pkmd.bin, and for the "
        "linux-first layout linux-first-boot.img, dtbo.img, linux-first-recovery.img")
