import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from flip import profile, sd
from flip.sd import Card, Ids
from flip.util import FlipError

GOOD = Card("/dev/mmcblk0", 15_931_539_456, "mmc", True, "", [], False)


def card(**change) -> Card:
    return Card(**{**GOOD.__dict__, **change})


def test_accepts_builtin_sd_slot_and_usb_reader():
    sd.check_card(GOOD, 2 << 30)
    sd.check_card(card(path="/dev/sdb", tran="usb", model="Card Reader"), 2 << 30)


@pytest.mark.parametrize("change,msg", [
    ({"model": profile.TOOLBOX_USB_MODEL, "tran": "usb", "size": 3_623_878_656},
     "PHONE'S OWN STORAGE"),
    ({"tran": "nvme", "hotplug": False}, "not an SD"),
    ({"tran": "sata"}, "not an SD"),
    ({"tran": "usb", "hotplug": False}, "not hot-pluggable"),
    ({"size": 1_000_000_000_000}, "between 4 and 128 GB"),
    ({"size": 2_000_000_000}, "between 4 and 128 GB"),
    ({"mounts": ["/"], "holds_system": True}, "this computer's system"),
])
def test_refuses_dangerous_targets(change, msg):
    with pytest.raises(FlipError, match=msg):
        sd.check_card(card(**change), 2 << 30)


def test_refuses_card_smaller_than_image():
    with pytest.raises(FlipError, match="smaller than the image"):
        sd.check_card(card(size=4_000_000_000), 5_000_000_000)


def test_inspect_card_reads_lsblk(monkeypatch):
    data = {"blockdevices": [{"name": "mmcblk0", "type": "disk", "size": 15931539456,
                              "tran": "mmc", "hotplug": True, "model": None,
                              "mountpoints": [None], "children": [
                                  {"name": "mmcblk0p1", "type": "part", "size": 1,
                                   "mountpoints": ["/run/media/u/E72E-B581"]}]}]}

    class R:
        stdout = json.dumps(data)

    monkeypatch.setattr(sd, "run", lambda *a, **k: R)
    c = sd.inspect_card("/dev/mmcblk0")
    assert (c.tran, c.hotplug, c.mounts, c.holds_system) == (
        "mmc", True, ["/run/media/u/E72E-B581"], False)
    sd.check_card(c, 1 << 30)  # mounted is fine: write_card unmounts it first


def test_inspect_card_rejects_partition(monkeypatch):
    class R:
        stdout = json.dumps({"blockdevices": [{"type": "part"}]})

    monkeypatch.setattr(sd, "run", lambda *a, **k: R)
    with pytest.raises(FlipError, match="not a whole disk"):
        sd.inspect_card("/dev/mmcblk0p1")


def test_ids_are_deterministic_and_distinct():
    a, b = Ids.derive("seed"), Ids.derive("seed")
    assert a == b
    assert len(set(a.__dict__.values())) == 5
    assert Ids.derive("other") != a


def test_boot_config_points_at_root_partition():
    ids = Ids.derive("x")
    conf = sd.extlinux_conf(ids, debug=False)
    assert f"root=PARTUUID={ids.root} " in conf and "loglevel=7" not in conf
    assert "linux /Image.gz" in conf and f"fdt /{profile.KERNEL_DTB_NAME}" in conf
    assert "loglevel=7" in sd.extlinux_conf(ids, debug=True)
    tab = sd.fstab(ids)
    assert f"PARTUUID={ids.root}  /      ext4" in tab and "x-systemd.growfs" in tab
    assert f"PARTUUID={ids.boot}  /boot  ext2" in tab


@pytest.mark.skipif(shutil.which("sfdisk") is None or shutil.which("mke2fs") is None,
                    reason="needs sfdisk and mke2fs")
def test_assemble_disk_image(tmp_path):
    ids = Ids.derive("test")
    (tmp_path / "b/extlinux").mkdir(parents=True)
    (tmp_path / "b/extlinux/extlinux.conf").write_text("default flip\n")
    (tmp_path / "r/etc").mkdir(parents=True)
    (tmp_path / "r/etc/hostname").write_text("flip\n")
    boot, root, img = tmp_path / "boot.ext2", tmp_path / "root.ext4", tmp_path / "sd.img"
    sd.make_ext("ext2", boot, 16 << 20, tmp_path / "b", "flipboot", ids.boot_fs, 0)
    sd.make_ext("ext4", root, 32 << 20, tmp_path / "r", "fliproot", ids.root_fs, 0)
    sd.assemble(img, boot, root, ids)

    table = json.loads(subprocess.run(["sfdisk", "--json", img], capture_output=True,
                                      text=True, check=True).stdout)["partitiontable"]
    assert table["label"] == "dos"  # lk2nd can't read GPT
    assert table["id"].lower() == f"0x{ids.disk}"
    p1, p2 = table["partitions"]
    assert (p1["type"], p1["start"]) == ("83", 2048)
    assert p2["type"] == "83" and p2["start"] == 2048 + (16 << 20) // 512
    # Linux names MBR partitions <disk id>-<number>; root=PARTUUID= relies on it
    blkid = subprocess.run(["blkid", "-p", "-o", "export", "-O", str(p2["start"] * 512), img],
                           capture_output=True, text=True).stdout
    assert "LABEL=fliproot" in blkid
    assert ids.root == f"{ids.disk}-02" and ids.boot == f"{ids.disk}-01"
    # the file systems are where the table says
    with open(img, "rb") as f:
        f.seek(p1["start"] * 512)
        assert f.read(16 << 20) == boot.read_bytes()
    # ext2 for lk2nd: no journal
    out = subprocess.run(["dumpe2fs", "-h", boot], capture_output=True, text=True).stdout
    assert "has_journal" not in out


def test_flipd_package_is_self_contained(tmp_path):
    """The copy installed on the phone must import with only stdlib + cryptography."""
    dest = tmp_path / "flip"
    shutil.copytree(Path(sd.__file__).parent / "link", dest / "link",
                    ignore=shutil.ignore_patterns("__pycache__", "client.py"))
    (dest / "__init__.py").write_text("")
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import flip.link.server; "
            "assert not [m for m in sys.modules if m.startswith('flip.') and "
            "not m.startswith('flip.link')], sys.modules.keys()")
    subprocess.run([sys.executable, "-I", "-c", code, str(tmp_path)], check=True)


def test_overlay_has_no_ssh_and_firewall_drops_input():
    overlay = Path(sd.SD_SRC) / "overlay"
    assert not any("ssh" in str(p) for p in overlay.rglob("*"))
    assert "openssh-server" not in profile.SD_PACKAGES and "sudo" not in profile.SD_PACKAGES
    nft = (overlay / "etc/nftables.conf").read_text()
    assert "hook input priority filter; policy drop;" in nft
    assert "policy drop" in nft.split("chain forward")[1].split("}")[0]


def _owners(img: Path, paths: list[str]) -> dict[str, str]:
    out = {}
    for p in paths:
        stat = subprocess.run(["debugfs", "-R", f"stat {p}", str(img)],
                              capture_output=True, text=True).stdout
        line = next(ln for ln in stat.splitlines() if "User:" in ln).split()
        out[p] = f"{line[line.index('User:') + 1]}:{line[line.index('Group:') + 1]}"
    return out


def test_staged_files_are_packed_as_root_with_standard_modes(tmp_path):
    src = tmp_path / "stage"
    (src / "etc/flipd").mkdir(parents=True)
    (src / "etc/flipd").chmod(0o700)
    (src / "etc/flipd/identity.pem").write_text("k")
    (src / "etc/flipd/identity.pem").chmod(0o600)
    (src / "usr/lib").mkdir(parents=True)
    (src / "usr/lib/run.sh").write_text("#!/bin/sh")
    (src / "usr/lib/run.sh").chmod(0o700)
    tar = sd.root_owned_tar(src, tmp_path / "s.tar")
    import tarfile
    with tarfile.open(tar) as tf:
        members = {m.name: m for m in tf}
    assert "." not in members and "./" not in members  # never touches / itself
    assert all(m.uid == 0 and m.gid == 0 and m.uname == "root" for m in members.values())
    assert members["etc"].mode == 0o755 and members["etc/flipd"].mode == 0o755
    assert members["usr/lib/run.sh"].mode == 0o755
    assert members["etc/flipd/identity.pem"].mode == 0o644  # setup.sh tightens it


def test_file_systems_are_root_owned_whoever_builds_them(tmp_path):
    if shutil.which("debugfs") is None:
        pytest.skip("debugfs not installed")
    src = tmp_path / "boot"
    (src / "extlinux").mkdir(parents=True)
    (src / "Image.gz").write_bytes(b"k")
    (src / "extlinux/extlinux.conf").write_text("default flip\n")
    img = tmp_path / "boot.ext2"
    sd.make_ext("ext2", img, 16 << 20, sd.root_owned_tar(src, tmp_path / "b.tar"),
                "flipboot", Ids.derive("x").boot_fs, 0)
    owners = _owners(img, ["/", "/Image.gz", "/extlinux", "/extlinux/extlinux.conf"])
    assert set(owners.values()) == {"0:0"}, owners


def test_refuses_root_fs_with_user_owned_system_files(tmp_path):
    import tarfile

    def build(entries):
        path = tmp_path / "r.tar"
        with tarfile.open(path, "w") as tf:
            for name, uid in entries:
                info = tarfile.TarInfo(name)
                info.type, info.uid, info.gid = tarfile.DIRTYPE, uid, uid
                tf.addfile(info)
        return path

    ok = build([("./", 0), ("./etc", 0), ("./var/log/journal", 0), ("./home/phone", 1000),
                ("./home/phone/.cache", 1000)])
    sd.check_ownership(ok)
    for bad in ("./etc", "./usr/lib", "./", "./boot/Image.gz"):
        with pytest.raises(FlipError, match="owned by a regular user"):
            sd.check_ownership(build([(bad, 1000)]))


@pytest.mark.skipif(shutil.which("sfdisk") is None, reason="needs sfdisk")
def test_root_partition_grows_to_fill_card(tmp_path):
    ids = Ids.derive("grow")
    boot, root = tmp_path / "b.img", tmp_path / "r.img"
    boot.write_bytes(b"\0" * (4 << 20))
    root.write_bytes(b"\0" * (8 << 20))
    img = tmp_path / "sd.img"
    sd.assemble(img, boot, root, ids)
    with open(img, "r+b") as f:  # a bigger "card"
        f.truncate(64 << 20)
    sd.grow_root_partition(str(img), sudo=False)
    table = json.loads(subprocess.run(["sfdisk", "--json", img], capture_output=True,
                                      text=True, check=True).stdout)["partitiontable"]
    p1, p2 = table["partitions"]
    assert p1["size"] == (4 << 20) // 512  # boot untouched
    assert p2["start"] + p2["size"] == (64 << 20) // 512
    assert table["id"].lower() == f"0x{ids.disk}"  # same partition IDs after growing


# --- the card while it's in the phone -------------------------------------------

def _lsblk(*disks):
    return json.dumps({"blockdevices": [
        {"path": p, "type": "disk", "size": size, "model": model} for p, size, model in disks]})


def test_finds_the_card_the_toolbox_shares():
    sd_model = profile.TOOLBOX_SD_MODEL
    assert sd.find_shared_card(_lsblk(("/dev/sda", 500_000_000_000, "Samsung SSD"),
                                      ("/dev/sdb", 15_931_539_456, sd_model))) == "/dev/sdb"
    # the phone's own storage, or the gadget with nothing shared yet, is not it
    assert sd.find_shared_card(_lsblk(("/dev/sdb", 3_623_878_656,
                                       profile.TOOLBOX_USB_MODEL))) is None
    assert sd.find_shared_card(_lsblk(("/dev/sdb", 0, sd_model))) is None
    with pytest.raises(FlipError, match="several phones"):
        sd.find_shared_card(_lsblk(("/dev/sdb", 1 << 34, sd_model), ("/dev/sdc", 1 << 34, sd_model)))


def test_accepts_card_shared_by_phone_but_not_read_only():
    shared = card(path="/dev/sdb", tran="usb", model=profile.TOOLBOX_SD_MODEL)
    sd.check_card(shared, 2 << 30)
    with pytest.raises(FlipError, match="5. SD to computer"):
        sd.check_card(card(path="/dev/sdb", tran="usb", model=profile.TOOLBOX_SD_MODEL,
                           ro=True), 2 << 30)


def test_toolbox_and_flip_agree():
    init = (Path(sd.config.ROOTFS) / "init").read_text()
    inquiry = init.split('SD_INQUIRY="')[1].split('"')[0]
    assert len(inquiry) == 28  # SCSI vendor 8 + product 16 + revision 4
    assert inquiry[8:24].strip() == profile.TOOLBOX_SD_MODEL
    assert f"SD_REQUEST={sd.SHARE_REQUEST}\n" in init
    assert f"flip_toolbox={profile.TOOLBOX_VERSION}" in profile.BOOT_CMDLINE
    assert profile.TOOLBOX_VERSION >= 2


def _fake_phone(tmp_path, monkeypatch, schedule_ok=True, dry_run_ok=True, reboot_ok=True):
    """sh, with /boot and the recovery partition redirected into tmp_path.

    systemd-run runs its command straight away; systemctl logs its arguments."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "mountpoint").write_text("#!/bin/sh\nexit 0\n")
    (bin_ / "systemd-run").write_text(
        "#!/bin/sh\n" + ('shift 2; "$@"; exit 0\n' if schedule_ok else "exit 1\n"))
    (bin_ / "systemctl").write_text(
        f'#!/bin/sh\necho "$@" >> {tmp_path}/systemctl\n'
        f'case "$*" in *--dry-run*) exit {0 if dry_run_ok else 1};; esac\n'
        f"exit {0 if reboot_ok else 1}\n")
    for f in bin_.iterdir():
        f.chmod(0o755)
    (tmp_path / "boot/extlinux").mkdir(parents=True)
    (tmp_path / "boot/extlinux/extlinux.conf").write_text("default flip\n")

    def sh(script):
        script = script.replace("/boot", str(tmp_path / "boot")).replace(
            "/dev/disk/by-partlabel/recovery", str(tmp_path / "recovery"))
        return subprocess.run(["sh", "-c", script], capture_output=True, text=True,
                              env={"PATH": f"{bin_}:/usr/bin:/bin"})
    return sh


REQ = sd.SHARE_REQUEST.split("/")[-1]


def _boot_state(tmp_path):
    ext = tmp_path / "boot/extlinux"
    return (ext / "extlinux.conf").exists(), (ext / REQ).exists()


def test_phone_reads_flashed_toolbox_version(tmp_path, monkeypatch):
    sh = _fake_phone(tmp_path, monkeypatch)
    script = sd.PHONE_TOOLBOX_VERSION.format(end=sd.TOOLBOX_HEADER_END)
    header = b"ANDROID!" + b"\0" * 56 + profile.BOOT_CMDLINE.encode()
    (tmp_path / "recovery").write_bytes(b"\x7fLK" * 1000 + b"\0" * (profile.LK2ND_SLOT - 3000)
                                        + header + b"\0" * 9000)
    assert sh(script).stdout.strip() == str(profile.TOOLBOX_VERSION)
    old = header.replace(f" flip_toolbox={profile.TOOLBOX_VERSION}".encode(), b"")
    (tmp_path / "recovery").write_bytes(b"\0" * profile.LK2ND_SLOT + old)
    assert sh(script).stdout.strip() == "0"


def test_phone_hides_boot_config_then_reboots_to_lk2nd(tmp_path, monkeypatch):
    sh = _fake_phone(tmp_path, monkeypatch)
    assert sh(sd.PHONE_REQUEST_SHARE.format(req=REQ)).returncode == 0
    assert _boot_state(tmp_path) == (False, True)
    assert (tmp_path / "boot/extlinux" / REQ).read_text() == "default flip\n"
    # systemd 257 rejects `systemctl reboot recovery`; the reason must be an option
    calls = (tmp_path / "systemctl").read_text().splitlines()
    assert calls == ["reboot --reboot-argument=recovery --dry-run",
                     "reboot --reboot-argument=recovery"]


@pytest.mark.parametrize("fail", ["dry_run_ok", "schedule_ok", "reboot_ok"])
def test_phone_keeps_boot_config_if_it_cant_restart(tmp_path, monkeypatch, fail):
    sh = _fake_phone(tmp_path, monkeypatch, **{fail: False})
    sh(sd.PHONE_REQUEST_SHARE.format(req=REQ))
    assert _boot_state(tmp_path) == (True, False)


def test_undo_puts_boot_config_back(tmp_path, monkeypatch):
    sh = _fake_phone(tmp_path, monkeypatch)
    ext = tmp_path / "boot/extlinux"
    (ext / "extlinux.conf").rename(ext / REQ)
    assert sh(sd.PHONE_UNDO_SHARE.format(req=REQ)).stdout.strip() == "restored"
    assert _boot_state(tmp_path) == (True, False)
    assert sh(sd.PHONE_UNDO_SHARE.format(req=REQ)).stdout.strip() == ""  # nothing to undo


class _OneOpSession:
    """Like the real phone link: one operation per session."""
    ran: list = []

    def __init__(self, replies):
        self.replies, self.used = replies, False

    def run(self, argv, stdin, out, err):
        assert not self.used, "second operation in one session: the phone ignores it"
        self.used = True
        self.ran.append(argv[-1])
        out.write(self.replies.pop(0))
        return 0


def _fake_link(monkeypatch, replies):
    from contextlib import contextmanager
    _OneOpSession.ran = []

    @contextmanager
    def session():
        yield _OneOpSession(replies)

    monkeypatch.setattr(sd, "find_shared_card", lambda: None)
    monkeypatch.setattr(sd.phone.client, "find_ports", lambda: ["/dev/ttyACM0"])
    monkeypatch.setattr(sd.phone, "session", session)


def test_refuses_to_restart_phone_with_old_toolbox(monkeypatch):
    _fake_link(monkeypatch, [b"0\n"])
    with pytest.raises(FlipError, match="can't share the SD card"):
        sd.phone_card(confirm=lambda q: True)
    assert len(_OneOpSession.ran) == 1  # only the version check; nothing renamed


def test_asks_phone_to_share_card_one_operation_per_session(monkeypatch):
    replies = [f"{profile.TOOLBOX_VERSION}\n".encode(), b""]
    _fake_link(monkeypatch, replies)
    found = iter([None, "/dev/sdb"])
    monkeypatch.setattr(sd, "find_shared_card", lambda: next(found))
    monkeypatch.setattr(sd.time, "sleep", lambda s: None)
    assert sd.phone_card(confirm=lambda q: True) == "/dev/sdb"
    assert len(_OneOpSession.ran) == 2 and sd.SHARE_REQUEST.split("/")[-1] in _OneOpSession.ran[1]


@pytest.mark.skipif(shutil.which(sd.config.cross_compile() + "gcc") is None,
                    reason="needs the aarch64 cross compiler")
def test_reboot_mode_helper_builds_reproducibly(tmp_path, monkeypatch):
    from flip import build
    monkeypatch.setattr(sd.config, "OUT", tmp_path)
    first = build.build_reboot_mode().read_bytes()
    assert build.build_reboot_mode().read_bytes() == first
    assert first[:4] == b"\x7fELF" and first[18] == 183  # EM_AARCH64
    # The phone's kernel (6.3) doesn't map a loadable segment with no file bytes:
    # the binary segfaulted on its first write to a variable.
    import struct
    phoff, = struct.unpack_from("<Q", first, 0x20)
    phentsize, phnum = struct.unpack_from("<HH", first, 0x36)
    for i in range(phnum):
        ptype, _flags, _off, _vaddr, _paddr, filesz, memsz = struct.unpack_from(
            "<IIQQQQQ", first, phoff + i * phentsize)
        if ptype == 1 and memsz:  # PT_LOAD
            assert filesz > 0, f"segment {i} has no bytes in the file"


def test_gives_up_and_undoes_if_phone_stays_in_linux(monkeypatch):
    replies = [f"{profile.TOOLBOX_VERSION}\n".encode(), b"", b"restored\n"]
    _fake_link(monkeypatch, replies)
    clock = iter(range(0, 10_000, 10))
    monkeypatch.setattr(sd.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(sd.time, "sleep", lambda s: None)
    with pytest.raises(FlipError, match="didn't restart.*back in place"):
        sd.phone_card(confirm=lambda q: True)
    assert "mv" in _OneOpSession.ran[2] and "extlinux.conf" in _OneOpSession.ran[2]


def test_waits_for_partition_labels_after_card_appears(monkeypatch):
    """The phone's card shows up before udev has read its labels."""
    views = [[None, None], [None, None], ["flipboot", "fliproot"]]

    class R:
        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run(cmd, **kw):
        if cmd[0] == "udevadm":
            return R("")
        assert "-l" in cmd  # flat: lsblk only nests partitions when NAME is shown
        labels = views.pop(0) if len(views) > 1 else views[0]
        return R(json.dumps({"blockdevices": [{"path": "/dev/sda", "type": "disk", "label": None}] + [
            {"path": f"/dev/sda{i + 1}", "type": "part", "label": lab}
            for i, lab in enumerate(labels)]}))

    monkeypatch.setattr(sd, "run", fake_run)
    monkeypatch.setattr(sd.time, "sleep", lambda s: None)
    parts = sd.partitions_by_label("/dev/sda")
    assert parts["fliproot"]["path"] == "/dev/sda2" and parts["flipboot"]["path"] == "/dev/sda1"


@pytest.mark.skipif(shutil.which("sfdisk") is None or shutil.which("losetup") is None,
                    reason="needs util-linux")
def test_partitions_reads_real_lsblk_output(tmp_path):
    """Against lsblk itself, on whatever disk this computer has."""
    disk = json.loads(subprocess.run(["lsblk", "-J", "-d", "-o", "PATH,TYPE"], capture_output=True,
                                     text=True, check=True).stdout)["blockdevices"]
    disks = [d["path"] for d in disk if d["type"] == "disk"]
    if not disks:
        pytest.skip("no disks")
    for d in disks:
        for part in sd.partitions(d):
            assert part["path"].startswith(d) and part["path"] != d
