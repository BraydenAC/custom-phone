"""The SD card system: build a Debian image, write it to a card, update a card.

`flip sd build` makes out/sd/sd.img without root:
  1. mmdebstrap installs pinned Debian packages for arm64 (ARM programs run
     under qemu emulation) into a tarball, adding sd/overlay/, the kernel
     modules, your phone's firmware and the flip-link keys, then runs
     sd/setup.sh inside.
  2. mke2fs -d turns the tarball into an ext4 root file system, and the boot
     files (kernel, device tree, extlinux.conf) into an ext2 one, the kind
     lk2nd can read.
  3. Both go into a disk image with an MBR partition table (lk2nd can't read
     GPT) and fixed partition IDs. `flip sd write` then extends the root
     partition to the end of the card; the file system grows on first boot.

The card can stay in the phone: without a device argument, `flip sd write` and
`update` ask the phone's Linux (over flipd) to restart into the toolbox, which
shares the card over USB as a disk named profile.TOOLBOX_SD_MODEL. After
writing, flip ejects it and the toolbox restarts into the card's Linux.

The image contains your phone's firmware and its flip-link identity key:
treat it like backups/ (private, never shared or committed).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tarfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from flip import backup, build, config, fetch, lock, phone, profile
from flip.link import keys as lkeys
from flip.link.secure import fingerprint
from flip.util import FlipError, run, say, sha256_file, warn

SD = config.OUT / "sd"
IMAGE = SD / "sd.img"
MANIFEST = SD / "manifest.json"
SD_SRC = config.REPO / "sd"
KEYRING = Path("/usr/share/keyrings/debian-archive-keyring.gpg")
HOST_TOOLS = ["perl", "h2ph", "apt", "dpkg", "mke2fs", "sfdisk", "mcopy", "debugfs", "newuidmap",
              "qemu-aarch64-static"]

MiB = 1 << 20
SECTOR = 512
MBR_TYPE_LINUX = "83"
# Namespace for the image's partition and file system IDs (derived, not random,
# so identical inputs give identical IDs).
_NS = uuid.UUID("6c1f3a52-8d0e-4b8e-9a51-6f6c6970736f")


def missing_tools() -> list[str]:
    missing = [t for t in HOST_TOOLS if shutil.which(t) is None]
    if not KEYRING.exists():
        missing.append("debian-archive-keyring")
    binfmt = Path("/proc/sys/fs/binfmt_misc/qemu-aarch64")
    text = binfmt.read_text() if binfmt.exists() else ""
    flags = next((ln.split(":", 1)[1] for ln in text.splitlines() if ln.startswith("flags:")), "")
    # F: the kernel opens qemu once at registration, so it also works inside
    # the build's chroot, where /usr/bin/qemu-aarch64-static doesn't exist.
    if "enabled" not in text or "F" not in flags:
        missing.append("qemu-user-static-binfmt (arm64 emulation not registered)")
    return missing


def check_tools() -> None:
    missing = missing_tools()
    if missing:
        raise FlipError(
            "missing tools for building the SD card: " + ", ".join(missing) + "\n"
            "   On Arch: sudo pacman -S --needed apt debian-archive-keyring mtools e2fsprogs \\\n"
            "            qemu-user-static qemu-user-static-binfmt")


# --- inputs ------------------------------------------------------------------

def phone_identity():
    """The phone's flip-link key. Kept (privately) in the config dir so that
    rebuilding a card keeps the identity your computers already know."""
    config.ensure_config_dir()
    path = config.config_dir() / "phone-identity.pem"
    key = lkeys.load_private(path) if path.exists() else lkeys.generate(path)
    pub = lkeys.encode_public(lkeys.public_bytes(key.public_key()))
    config.phone_key().write_text(pub + "\n")
    return key, path


def extract_firmware(backup_dir: Path, dest: Path) -> list[str]:
    """Copy the phone's own firmware out of a backup into dest (lib/firmware layout)."""
    parts = backup_dir / "parts"
    modem, persist = parts / "modem.img", parts / "persist.img"
    for p in (modem, persist):
        if not p.exists():
            raise FlipError(f"{p} missing: is {backup_dir.name} a complete backup?")
    dest.mkdir(parents=True, exist_ok=True)
    # The modem partition (FAT) holds the firmware for the modem, Wi-Fi
    # (wcnss), audio DSP (adsp) and more, as image/*.mdt + .bNN files.
    run(["mcopy", "-n", "-m", "-i", modem, "::image/*", dest], quiet=True,
        env={"MTOOLS_SKIP_CHECK": "1"})
    # Wi-Fi calibration data lives on the persist partition (ext4).
    nv = dest / "wlan/prima/WCNSS_qcom_wlan_nv.bin"
    nv.parent.mkdir(parents=True, exist_ok=True)
    run(["debugfs", "-R", f"dump /WCNSS_qcom_wlan_nv.bin {nv}", persist], quiet=True,
        capture=True)
    if not nv.exists() or nv.stat().st_size == 0:
        raise FlipError("WCNSS_qcom_wlan_nv.bin not found on the persist partition")
    # mcopy names: lowercase what FAT may report in upper case.
    for f in list(dest.iterdir()):
        if f.is_file() and f.name != f.name.lower():
            f.rename(f.with_name(f.name.lower()))
    files = sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file())
    for name in ("mba.mbn", "modem.mdt", "wcnss.mdt", "adsp.mdt"):
        if name not in files:
            warn(f"firmware {name} not found in the modem partition")
    return files


# --- image building --------------------------------------------------------------

@dataclass(frozen=True)
class Ids:
    disk: str  # MBR disk signature, 8 hex digits
    boot: str  # partition IDs as Linux shows them: <disk>-01
    root: str
    boot_fs: str
    root_fs: str

    @classmethod
    def derive(cls, seed: str) -> "Ids":
        u = lambda what: str(uuid.uuid5(_NS, f"{what}:{seed}"))  # noqa: E731
        disk = u("disk")[:8]
        return cls(disk, f"{disk}-01", f"{disk}-02", u("boot-fs"), u("root-fs"))


def extlinux_conf(ids: Ids, debug: bool) -> str:
    cmdline = f"root=PARTUUID={ids.root} rootwait rw {profile.SD_CMDLINE}"
    if debug:
        cmdline += " loglevel=7 systemd.log_level=debug"
    return (
        "# Written by `flip sd build`. lk2nd boots the default label.\n"
        "default flip\n"
        "timeout 1\n\n"
        "label flip\n"
        "    linux /Image.gz\n"
        f"    fdt /{profile.KERNEL_DTB_NAME}\n"
        f"    append {cmdline}\n"
    )


def fstab(ids: Ids) -> str:
    return (
        "# Written by `flip sd build`.\n"
        f"PARTUUID={ids.root}  /      ext4  rw,noatime,x-systemd.growfs          0 1\n"
        f"PARTUUID={ids.boot}  /boot  ext2  rw,noatime,nodev,nosuid,noexec      0 2\n"
    )


def stage(stage_dir: Path, ids: Ids, identity_pem: Path, firmware_from: Path,
          debug: bool) -> dict:
    """Assemble the files added on top of Debian (copied in by mmdebstrap)."""
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    shutil.copytree(SD_SRC / "overlay", stage_dir, symlinks=True)
    if debug:
        shutil.copytree(SD_SRC / "debug", stage_dir, symlinks=True, dirs_exist_ok=True)
    (stage_dir / "etc/fstab").write_text(fstab(ids))

    # flipd and its keys
    flipd = stage_dir / "usr/lib/flipd/flip"
    shutil.copytree(Path(__file__).parent / "link", flipd / "link",
                    ignore=shutil.ignore_patterns("__pycache__", "client.py"))
    (flipd / "__init__.py").write_text('"""flip-link server, installed by flip sd build."""\n')
    etc_flipd = stage_dir / "etc/flipd"
    etc_flipd.mkdir(parents=True)
    shutil.copyfile(identity_pem, etc_flipd / "identity.pem")
    host = phone.ensure_host_key()
    lkeys.write_trusted(etc_flipd / "trusted-hosts", [
        lkeys.TrustedHost(lkeys.public_bytes(host.public_key()), phone.host_label())])

    # kernel modules + firmware
    release = build.OUT.kernel_release.read_text().strip()
    shutil.copytree(build.OUT.modules / "lib/modules" / release,
                    stage_dir / "usr/lib/modules" / release, symlinks=True)
    firmware = extract_firmware(firmware_from, stage_dir / "usr/lib/firmware")
    # Display init sequences for panel-mipi-dbi (shared with the toolbox; no
    # console on the screen without them).
    for panel in sorted((config.ROOTFS / "lib/firmware").glob("*-panel.bin")):
        shutil.copyfile(panel, stage_dir / "usr/lib/firmware" / panel.name)
        firmware.append(panel.name)
    return {"kernel_release": release, "firmware": firmware}


def perl_headers(dest: Path) -> Path:
    """Perl versions of the C headers mmdebstrap requires (syscall.ph, sys/ioctl.ph).

    Debian ships these with perl; Arch doesn't, so generate them from this
    computer's own headers with h2ph (part of perl).
    """
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    # h2ph exits non-zero over a few headers it can't translate; we only need
    # the two below, which are checked afterwards.
    run(["h2ph", "-d", dest, "-a", "syscall.h", "sys/ioctl.h"], cwd=Path("/usr/include"),
        capture=True, check=False, quiet=True)
    # A C `#warning` that h2ph turns into a runtime warning on every use.
    features = dest / "features.ph"
    if features.exists():
        features.write_text("\n".join(
            line for line in features.read_text().splitlines()
            if "requires\\ compiling\\ with\\ optimization" not in line) + "\n")
    check = run(["perl", f"-I{dest}", "-e",
                 'require "syscall.ph"; require "sys/ioctl.ph"; '
                 'print &SYS_unshare, " ", &TIOCGWINSZ'],
                capture=True, check=False, quiet=True)
    if check.returncode != 0:
        raise FlipError(f"could not generate perl's syscall headers for mmdebstrap:\n"
                        f"{check.stderr.strip()}")
    return dest


def build_rootfs(pins: lock.Lock, stage_tar: Path, tarball: Path, debug: bool) -> None:
    mmdebstrap = fetch.fetch_file(pins.file["mmdebstrap"])
    headers = perl_headers(SD / "perl-headers")
    deb = pins.debian
    epoch = _snapshot_epoch(deb.snapshot)
    setup = SD_SRC / "setup.sh"
    cmd = [
        "perl", f"-I{headers}", mmdebstrap,
        "--mode=unshare", "--variant=apt", "--architectures=arm64",
        # Its emulation check needs arch-test (not packaged for Arch);
        # missing_tools() checks the same thing.
        "--skip=check/qemu",
        f"--keyring={KEYRING}",
        "--aptopt=Acquire::Check-Valid-Until \"false\"",  # snapshots are old by design
        "--aptopt=Acquire::Retries \"3\"",
        "--aptopt=APT::Install-Recommends \"false\"",
        "--include=" + ",".join(profile.SD_PACKAGES),
        f"--customize-hook=tar-in {stage_tar} /",
        f"--customize-hook=copy-in {setup} /tmp",
        f"--customize-hook=chroot \"$1\" /bin/sh /tmp/setup.sh {1 if debug else 0}",
        "--customize-hook=rm \"$1/tmp/setup.sh\"",
        deb.suite, tarball, *deb.mirrors,
    ]
    run(cmd, env={"SOURCE_DATE_EPOCH": str(epoch)})


def _snapshot_epoch(snapshot: str) -> int:
    from datetime import datetime, timezone
    return int(datetime.strptime(snapshot, "%Y%m%dT%H%M%SZ")
               .replace(tzinfo=timezone.utc).timestamp())


def root_owned_tar(src: Path, out: Path) -> Path:
    """Pack a directory into a tarball whose entries are all owned by root.

    Files staged here belong to whoever runs flip (often uid 1000, the same
    number as the phone's `phone` user); copied as-is they would let that user
    rewrite /etc, /usr or the kernel. The top directory itself is left out so
    unpacking it never changes / on the phone. Modes are normalized too.
    """
    def as_root(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        # Standard modes whatever this computer's umask; setup.sh makes the
        # few private files private.
        if info.isdir() or (info.isfile() and info.mode & 0o111):
            info.mode = 0o755
        elif info.isfile():
            info.mode = 0o644
        return info

    out.unlink(missing_ok=True)
    with tarfile.open(out, "w", format=tarfile.PAX_FORMAT) as tf:
        for entry in sorted(src.iterdir()):
            tf.add(entry, arcname=entry.name, filter=as_root)
    return out


# The phone's regular users (the `phone` agent account) start here; nothing
# outside their home directories may belong to one.
FIRST_USER_UID = 1000


def check_ownership(tarball: Path, allowed: tuple[str, ...] = ("home/",)) -> None:
    """Refuse a root file system where a regular user owns system files."""
    bad = []
    with tarfile.open(tarball) as tf:
        for m in tf:
            name = m.name.removeprefix("./")
            if any(name.startswith(a) for a in allowed):
                continue
            if FIRST_USER_UID <= m.uid < 65534 or FIRST_USER_UID <= m.gid < 65534:
                bad.append(f"/{name} ({m.uid}:{m.gid})")
    if bad:
        shown = "\n  ".join(bad[:10]) + ("\n  ..." if len(bad) > 10 else "")
        raise FlipError("the root file system has system files owned by a regular "
                        f"user, which would let the agent account change them:\n  {shown}")


def _round_up(n: int, to: int) -> int:
    return (n + to - 1) // to * to


def root_fs_size(tarball: Path) -> int:
    with tarfile.open(tarball) as tf:
        content = sum(m.size for m in tf.getmembers() if m.isfile())
    # room for file system overhead plus some headroom; the partition and file
    # system grow to fill the card on first boot.
    return _round_up(int(content * 1.25) + 384 * MiB, 64 * MiB)


def make_ext(kind: str, out: Path, size: int, source: Path, label: str, fs_uuid: str,
             epoch: int) -> None:
    out.unlink(missing_ok=True)
    with open(out, "wb") as f:
        f.truncate(size)
    # root_owner: otherwise / belongs to whoever runs mke2fs.
    args = ["mke2fs", "-q", "-F", "-t", kind, "-L", label, "-U", fs_uuid,
            "-E", f"hash_seed={fs_uuid},root_owner=0:0", "-d", source, out]
    run(args, env={"E2FSPROGS_FAKE_TIME": str(epoch)})


def assemble(out: Path, boot_img: Path, root_img: Path, ids: Ids) -> None:
    boot_size, root_size = boot_img.stat().st_size, root_img.stat().st_size
    start_boot = MiB
    start_root = start_boot + boot_size
    total = start_root + root_size
    out.unlink(missing_ok=True)
    with open(out, "wb") as f:
        f.truncate(total)
    # MBR, not GPT: lk2nd 23.1 only reads MBR partition tables.
    script = (
        "label: dos\n"
        f"label-id: 0x{ids.disk}\n"
        f"start={start_boot // SECTOR}, size={boot_size // SECTOR}, type={MBR_TYPE_LINUX}\n"
        f"start={start_root // SECTOR}, size={root_size // SECTOR}, type={MBR_TYPE_LINUX}\n"
    )
    run(["sfdisk", "-q", "--no-reread", "--no-tell-kernel", out], input_=script)
    with open(out, "r+b") as dst:
        for img, offset in ((boot_img, start_boot), (root_img, start_root)):
            dst.seek(offset)
            with open(img, "rb") as src:
                shutil.copyfileobj(src, dst, 4 * MiB)


def build_image(debug: bool = False, backup_name: str | None = None) -> Path:
    check_tools()
    pins = lock.load()
    if pins.debian is None:
        raise FlipError("pins.toml has no [debian] section")
    for out in (build.OUT.kernel_image, build.OUT.dtb, build.OUT.kernel_release):
        if not out.exists():
            raise FlipError(f"{out.name} missing: run `flip build` first")
    firmware_from = backup.resolve(backup_name)

    SD.mkdir(parents=True, exist_ok=True)
    os.chmod(SD, 0o700)
    identity, identity_path = phone_identity()
    phone_pub = lkeys.public_bytes(identity.public_key())
    seed_src = json.dumps([pins.debian.snapshot, profile.SD_PACKAGES, debug,
                           lkeys.encode_public(phone_pub)])
    ids = Ids.derive(hashlib.sha256(seed_src.encode()).hexdigest())
    epoch = _snapshot_epoch(pins.debian.snapshot)

    say("[1] staging files (modules, firmware from backup "
        f"{firmware_from.name}, flip-link keys)")
    stage_dir = SD / "stage"
    staged = stage(stage_dir, ids, identity_path, firmware_from, debug)

    say(f"[2] Debian {pins.debian.suite} (snapshot {pins.debian.snapshot}) for arm64; "
        "this downloads ~200 MB and takes a while under emulation")
    tarball = SD / "rootfs.tar"
    stage_tar = root_owned_tar(stage_dir, SD / "stage.tar")
    build_rootfs(pins, stage_tar, tarball, debug)
    check_ownership(tarball)

    say("[3] file systems")
    boot_dir = SD / "boot"
    if boot_dir.exists():
        shutil.rmtree(boot_dir)
    (boot_dir / "extlinux").mkdir(parents=True)
    shutil.copyfile(build.OUT.kernel_image, boot_dir / "Image.gz")
    shutil.copyfile(build.OUT.dtb, boot_dir / profile.KERNEL_DTB_NAME)
    (boot_dir / "extlinux/extlinux.conf").write_text(extlinux_conf(ids, debug))
    boot_img, root_img = SD / "boot.ext2", SD / "root.ext4"
    boot_tar = root_owned_tar(boot_dir, SD / "boot.tar")
    make_ext("ext2", boot_img, profile.SD_BOOT_SIZE, boot_tar, profile.SD_BOOT_LABEL,
             ids.boot_fs, epoch)
    make_ext("ext4", root_img, root_fs_size(tarball), tarball, profile.SD_ROOT_LABEL,
             ids.root_fs, epoch)

    say("[4] disk image")
    assemble(IMAGE, boot_img, root_img, ids)
    os.chmod(IMAGE, 0o600)
    for tmp in (boot_img, root_img, tarball, stage_tar, boot_tar):
        tmp.unlink()
    shutil.rmtree(stage_dir)
    digest = sha256_file(IMAGE)
    manifest = {
        "image": IMAGE.name, "size": IMAGE.stat().st_size, "sha256": digest,
        "debug": debug, "debian": {"suite": pins.debian.suite,
                                   "snapshot": pins.debian.snapshot},
        "packages": profile.SD_PACKAGES, "kernel_release": staged["kernel_release"],
        "firmware_from_backup": firmware_from.name, "firmware": staged["firmware"],
        "ids": ids.__dict__, "phone_key": fingerprint(phone_pub),
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    say(f"done: {IMAGE.relative_to(config.REPO)} ({IMAGE.stat().st_size / 2**30:.2f} GiB, "
        f"sha256 {digest[:16]}...)")
    if debug:
        warn("DEBUG image: root shell for anyone on the USB serial line")
    return IMAGE


def read_manifest() -> dict:
    try:
        return json.loads(MANIFEST.read_text())
    except FileNotFoundError:
        raise FlipError("no SD image yet: run `flip sd build`") from None


# --- writing a card ------------------------------------------------------------

@dataclass
class Card:
    path: str
    size: int
    tran: str
    hotplug: bool
    model: str
    mounts: list[str]
    holds_system: bool  # contains / or /boot of this computer
    ro: bool = False


SD_SIZE_RANGE = (3_800_000_000, 130_000_000_000)  # "4 GB" .. "128 GB" cards
SYSTEM_MOUNTS = {"/", "/boot", "/boot/efi", "/efi", "/home", "/usr", "/var", "[SWAP]"}


def inspect_card(dev: str) -> Card:
    out = run(["lsblk", "-J", "-b", "-o", "NAME,TYPE,SIZE,TRAN,HOTPLUG,MODEL,RO,MOUNTPOINTS",
               dev], capture=True, quiet=True).stdout
    node = json.loads(out)["blockdevices"][0]
    if node.get("type") != "disk":
        raise FlipError(f"{dev} is a {node.get('type')}, not a whole disk (use e.g. "
                        "/dev/mmcblk0, not /dev/mmcblk0p1)")
    mounts: list[str] = []

    def walk(n: dict) -> None:
        mounts.extend(m for m in (n.get("mountpoints") or []) if m)
        for c in n.get("children", []):
            walk(c)

    walk(node)
    return Card(dev, int(node["size"]), (node.get("tran") or "").lower(),
                node.get("hotplug") in (True, "1", 1),
                (node.get("model") or "").strip(), mounts,
                any(m in SYSTEM_MOUNTS or m.startswith("/boot") for m in mounts),
                node.get("ro") in (True, "1", 1))


def check_card(card: Card, image_size: int) -> None:
    if card.model == profile.TOOLBOX_USB_MODEL:
        raise FlipError(f"{card.path} is the PHONE'S OWN STORAGE shared by the toolbox, not an "
                        "SD card. Writing it would wipe the phone. Refusing.")
    if card.holds_system:
        raise FlipError(f"{card.path} holds this computer's system ({', '.join(card.mounts)}). "
                        "Refusing.")
    if card.tran not in ("mmc", "usb"):
        raise FlipError(f"{card.path} is connected by '{card.tran or 'unknown'}', not an SD "
                        "slot or USB card reader. Refusing.")
    if card.tran == "usb" and not card.hotplug:
        raise FlipError(f"{card.path} is not hot-pluggable; doesn't look like a card reader")
    lo, hi = SD_SIZE_RANGE
    if not lo <= card.size <= hi:
        raise FlipError(f"{card.path} is {card.size / 1e9:.1f} GB; expected an SD card "
                        "between 4 and 128 GB")
    if card.size < image_size:
        raise FlipError(f"{card.path} ({card.size} bytes) is smaller than the image "
                        f"({image_size} bytes)")
    if card.ro:
        hint = (" The phone is sharing it read-only: in the toolbox use '5. SD to computer'."
                if card.model == profile.TOOLBOX_SD_MODEL else "")
        raise FlipError(f"{card.path} is read-only (write-protect switch?).{hint}")


def partitions(dev: str) -> list[dict]:
    """dev's partitions as lsblk entries: path, label, mountpoints.

    -l (a flat list, filtered by type): lsblk only nests partitions under their
    disk when the NAME column is shown, so don't rely on "children".
    """
    out = run(["lsblk", "-J", "-l", "-o", "PATH,TYPE,LABEL,MOUNTPOINTS", dev], capture=True,
              quiet=True).stdout
    return [n for n in json.loads(out)["blockdevices"] if n.get("type") == "part"]


def unmount_all(card: Card) -> None:
    for n in partitions(card.path):
        if any(n.get("mountpoints") or []):
            run(["udisksctl", "unmount", "-b", n["path"]])


def write_card(dev: str | None, confirm) -> None:
    manifest = read_manifest()
    if not IMAGE.exists() or IMAGE.stat().st_size != manifest["size"]:
        raise FlipError("out/sd/sd.img is missing or incomplete: run `flip sd build`")
    in_phone = dev is None
    if dev is None:
        dev = phone_card(confirm)
    if not Path(dev).is_block_device():
        raise FlipError(f"{dev} is not a block device (see lsblk)")
    card = inspect_card(dev)
    say(f"{dev}: {card.model or 'SD card'}, {card.size / 1e9:.1f} GB, via {card.tran}")
    check_card(card, manifest["size"])
    say("verifying the image ...")
    if sha256_file(IMAGE) != manifest["sha256"]:
        raise FlipError("sd.img doesn't match its manifest (changed or corrupted): rebuild it")
    if manifest["debug"]:
        warn("this is a DEBUG image (root shell on the USB serial line, no flipd)")
    if not confirm(f"ERASE everything on {dev} ({card.size / 1e9:.1f} GB) and write the "
                   "flip SD image?"):
        raise FlipError("aborted")
    if card.mounts:
        unmount_all(card)
    say(f"writing {IMAGE.name} -> {dev} (sudo is needed for raw device access)")
    run(["sudo", "dd", f"if={IMAGE}", f"of={dev}", "bs=4M", "conv=fsync", "oflag=direct",
         "status=progress"])
    say("reading it back to verify ...")
    run(["sudo", "blockdev", "--flushbufs", dev])  # compare the card, not the page cache
    result = run(["sudo", "cmp", "-n", str(manifest["size"]), IMAGE, dev], check=False,
                 capture=True)
    if result.returncode != 0:
        raise FlipError(f"the card doesn't read back what was written ({result.stdout.strip()}"
                        "). The card may be faulty or fake-capacity.")
    say("extending the root partition to the end of the card ...")
    grow_root_partition(dev)
    if in_phone:
        release_card(dev)
        say("card written and verified. The phone is starting Linux from it.")
    else:
        say("card written and verified. Eject it, put it in the phone and power on.")


def grow_root_partition(dev: str, sudo: bool = True) -> None:
    """Extend partition 2 to the end of the disk (the file system follows on first boot)."""
    run([*(["sudo"] if sudo else []), "sfdisk", "-q", "--no-reread", "-N", "2", dev],
        input_=", +\n")
    if sudo:
        run(["sudo", "partx", "-u", dev], check=False, quiet=True)


# --- updating a card in place -----------------------------------------------------

def partitions_by_label(dev: str, wait: float = 15) -> dict[str, dict]:
    """{label: lsblk entry} for dev's partitions.

    Labels come from udev, which reads them a moment after a disk appears (a card
    the phone just shared, or one just plugged in): wait until every partition
    has one, up to `wait` seconds.
    """
    run(["udevadm", "settle", f"--timeout={int(wait)}"], check=False, quiet=True)
    deadline = time.monotonic() + wait
    while True:
        children = partitions(dev)
        if all(c.get("label") for c in children) or time.monotonic() > deadline:
            return {c.get("label"): c for c in children if c.get("label")}
        time.sleep(0.5)

def update_card(dev: str | None, confirm) -> None:
    """Replace kernel, device tree and modules on a flip card; keep everything else."""
    in_phone = dev is None
    if dev is None:
        dev = phone_card(confirm)
    if not Path(dev).is_block_device():
        raise FlipError(f"{dev} is not a block device (see lsblk)")
    card = inspect_card(dev)
    check_card(card, 0)
    parts = partitions_by_label(dev)
    if profile.SD_BOOT_LABEL not in parts or profile.SD_ROOT_LABEL not in parts:
        raise FlipError(f"{dev} is not a flip SD card (no {profile.SD_BOOT_LABEL}/"
                        f"{profile.SD_ROOT_LABEL} partitions)")
    release = build.OUT.kernel_release.read_text().strip()
    if not confirm(f"update the kernel ({release}), device tree and modules on {dev}?"):
        raise FlipError("aborted")
    if card.mounts:
        unmount_all(card)
    mnt = SD / "mnt"
    boot_mnt, root_mnt = mnt / "boot", mnt / "root"
    boot_mnt.mkdir(parents=True, exist_ok=True)
    root_mnt.mkdir(parents=True, exist_ok=True)
    say("mounting the card (sudo)")
    run(["sudo", "mount", parts[profile.SD_BOOT_LABEL]["path"], boot_mnt])
    try:
        run(["sudo", "mount", parts[profile.SD_ROOT_LABEL]["path"], root_mnt])
        try:
            run(["sudo", "install", "-m", "644", build.OUT.kernel_image, boot_mnt / "Image.gz"])
            run(["sudo", "install", "-m", "644", build.OUT.dtb,
                 boot_mnt / profile.KERNEL_DTB_NAME])
            dest = root_mnt / "usr/lib/modules" / release
            run(["sudo", "rm", "-rf", dest])
            run(["sudo", "cp", "-a", build.OUT.modules / "lib/modules" / release, dest])
            run(["sudo", "chown", "-R", "root:root", dest])
            run(["sync"])
        finally:
            run(["sudo", "umount", root_mnt])
    finally:
        run(["sudo", "umount", boot_mnt])
    if in_phone:
        release_card(dev)
        say("card updated. The phone is starting Linux from it.")
    else:
        say("card updated")


# --- the card while it's in the phone ------------------------------------------

# rootfs/init SD_REQUEST: renaming extlinux.conf to this makes lk2nd start the
# toolbox instead (it finds nothing to boot), and tells the toolbox to share the
# card. The toolbox renames it back first thing, so this is one-shot.
SHARE_REQUEST = "extlinux/extlinux.conf.toolbox-once"
SHARE_WAIT = 180  # seconds: Linux shutdown + bootloader warning + toolbox start
# lk2nd, then the toolbox boot image (with its command line in the header).
TOOLBOX_HEADER_END = profile.LK2ND_SLOT + 4096

PHONE_TOOLBOX_VERSION = (
    "v=$(head -c {end} /dev/disk/by-partlabel/recovery | grep -ao 'flip_toolbox=[0-9]*' "
    "| head -n 1 | cut -d= -f2); echo \"${{v:-0}}\"")
# Rename, then reboot with the reason that makes the bootloader start lk2nd
# (pm8916 pon reboot-mode). The reboot is checked first (--dry-run), runs 2 s
# later so the reply gets back, and puts the file back if it fails after all.
REBOOT_TO_LK2ND = "systemctl reboot --reboot-argument=recovery"
PHONE_REQUEST_SHARE = (
    "set -eu; mountpoint -q /boot; cd /boot/extlinux; test -f extlinux.conf; "
    f"{REBOOT_TO_LK2ND} --dry-run; "
    "mv extlinux.conf {req}; sync; "
    "systemd-run --quiet --on-active=2 sh -c "
    f"'{REBOOT_TO_LK2ND} || {{{{ mv /boot/extlinux/{{req}} /boot/extlinux/extlinux.conf; sync; }}}}' "
    "|| {{ mv {req} extlinux.conf; sync; exit 1; }}")
PHONE_UNDO_SHARE = ("cd /boot/extlinux; if [ -f {req} ]; then mv {req} extlinux.conf; sync; "
                    "echo restored; fi")
RESTART_GRACE = 60  # seconds: if the phone's Linux link is still up by then, it didn't restart


def find_shared_card(lsblk_json: str | None = None) -> str | None:
    """The phone's SD card if the toolbox is sharing it over USB right now."""
    if lsblk_json is None:
        lsblk_json = run(["lsblk", "-J", "-d", "-b", "-o", "PATH,TYPE,SIZE,MODEL"],
                         capture=True, quiet=True).stdout
    found = [d["path"] for d in json.loads(lsblk_json)["blockdevices"]
             if d.get("type") == "disk" and int(d.get("size") or 0) > 0
             and (d.get("model") or "").strip() == profile.TOOLBOX_SD_MODEL]
    if len(found) > 1:
        raise FlipError(f"several phones are sharing SD cards ({', '.join(found)}); "
                        "give the device")
    return found[0] if found else None


def _phone_sh(script: str) -> str:
    """Run a shell script on the phone as root (its own session: one operation each)."""
    out, err = io.BytesIO(), io.BytesIO()
    with phone.session() as s:
        code = s.run(["sh", "-c", script], None, out, err)
    if code != 0:
        raise FlipError(f"on the phone: {err.getvalue().decode(errors='replace').strip()} "
                        f"(exit {code})")
    return out.getvalue().decode().strip()


def phone_card(confirm) -> str:
    """Get the SD card in the phone shared with this computer; return its device."""
    dev = find_shared_card()
    if dev:
        say(f"{dev} is the phone's SD card, shared by the toolbox")
        return dev
    if not phone.client.find_ports():
        raise FlipError(
            "the phone isn't connected. Either:\n"
            "   - start Linux from the SD card and plug in USB (flip restarts it into "
            "the toolbox), or\n"
            "   - start the toolbox (volume-up at power-on, held past the warning) and "
            "choose '5. SD to computer', or\n"
            "   - put the card in this computer and give its device, e.g. /dev/mmcblk0")
    version = _phone_sh(PHONE_TOOLBOX_VERSION.format(end=TOOLBOX_HEADER_END))
    if not version.isdigit() or int(version) < 2:
        raise FlipError(
            "the toolbox flashed on the phone can't share the SD card on request yet. "
            "Flash the current one first (`flip build`, then `flip flash`), or take "
            "the card out and give its device.")
    if not confirm("The phone will restart into its toolbox and share its SD card "
                   "with this computer. Continue?"):
        raise FlipError("aborted")
    req = SHARE_REQUEST.split("/")[-1]
    _phone_sh(PHONE_REQUEST_SHARE.format(req=req))
    say("the phone is restarting into the toolbox; waiting for its SD card ...")
    start = time.monotonic()
    while time.monotonic() < start + SHARE_WAIT:
        time.sleep(2)
        dev = find_shared_card()
        if dev:
            say(f"{dev} is the phone's SD card")
            time.sleep(2)  # let the desktop's automounter finish; write_card unmounts
            return dev
        if time.monotonic() > start + RESTART_GRACE and phone.client.find_ports():
            try:
                undone = _phone_sh(PHONE_UNDO_SHARE.format(req=req))
            except FlipError:
                continue  # the link is going down after all: keep waiting
            raise FlipError(
                "the phone didn't restart (its Linux is still running)"
                + ("; its boot configuration is back in place" if undone else "")
                + ". See `flip phone run -- journalctl -b -n 50`.")
    raise FlipError(
        f"the phone didn't share its SD card within {SHARE_WAIT} s. If it shows KaiOS, it "
        "didn't restart into lk2nd: power it off, then hold volume-up while powering on and "
        "release it at the warning. The toolbox then shares the card by itself; run this "
        "again.")


def release_card(dev: str) -> None:
    """Eject the phone's card; the toolbox sees that and starts the card's Linux."""
    say(f"ejecting {dev} (the phone then restarts into Linux)")
    run(["sudo", "eject", dev])
