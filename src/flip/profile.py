"""Fixed facts about the Nokia 2780 Flip and the toolbox image layout."""

# fastboot "product" as reported by each bootloader
PRODUCT_STOCK = "WND"
PRODUCT_LK2ND = "lk2nd-msm8952"

# The toolbox's USB mass storage gadget (rootfs/init)
TOOLBOX_USB_ID = ("05c6", "9092")
TOOLBOX_USB_MODEL = "File-Stor Gadget"
# When the toolbox shares the SD card (not the phone's own storage) it gives the
# USB disk this model name instead, so `flip sd write` can find it and tell it
# apart from the eMMC. Must match SD_INQUIRY in rootfs/init.
TOOLBOX_SD_MODEL = "flip SD card"

# Partition sizes (from `fastboot getvar all` on the reference phone)
RECOVERY_SIZE = 0x3000000  # 48 MiB
BOOT_SIZE = 0x2000000  # 32 MiB
DTBO_SIZE = 0x800000  # 8 MiB
VBMETA_SIZE = 0x10000  # 64 KiB

# lk2nd occupies the first 512 KiB of its partition (LK2ND_PARTITION_SIZE); the
# toolbox boot image follows at that offset.
LK2ND_SLOT = 512 * 1024

# Whole eMMC is 3623878656 bytes; accept a range so similar units still work.
EMMC_SIZE_RANGE = (3_000_000_000, 4_300_000_000)

KERNEL_DTB = "arch/arm64/boot/dts/qcom/qm215-nokia-weeknd.dtb"
KERNEL_DTB_NAME = "qm215-nokia-weeknd.dtb"
# flip_toolbox=N marks what the flashed toolbox can do; flip reads it back from
# the recovery partition (on the phone) before relying on a feature.
#   2: shares the SD card on request (sd.request_card_share) and restarts into
#      the card's Linux when the computer ejects it.
#   3: knows the boot layout (flip_layout=, below) and can start KaiOS.
TOOLBOX_VERSION = 3

# Where things live. The phone's own bootloader (signed, can't be changed)
# starts the boot partition normally and the recovery partition when volume-up
# is held at power-on (or after `reboot recovery`).
#   kaios-first: boot = KaiOS, recovery = lk2nd + toolbox (first install).
#   linux-first: boot = lk2nd + toolbox, recovery = KaiOS (from your backup).
# lk2nd boots the SD card's Linux if it finds one, else the toolbox.
LAYOUTS = ("kaios-first", "linux-first")


def boot_cmdline(layout: str) -> str:
    """The toolbox's kernel command line; flip and the toolbox read the markers."""
    return (f"console=tty1 rw loglevel=7 fbcon=font:10x18 flip_toolbox={TOOLBOX_VERSION} "
            f"flip_layout={layout}")

LK2ND_TARGET = "lk2nd-msm8952"
LK2ND_MAKE_ARGS = {
    "kaios-first": [
        "LK2ND_PARTITION_BASE=recovery",
        # patches/lk2nd/0001-*: boot the toolbox when no extlinux system is found
        "LK2ND_FALLBACK_RECOVERY=1",
    ],
    "linux-first": [
        # lk2nd's default: the toolbox is the rest of boot, its fallback anyway
        "LK2ND_PARTITION_BASE=boot",
        # patches/lk2nd/0003-*: volume-up as lk2nd starts = toolbox, not KaiOS
        "LK2ND_RECOVERY_KEY_TOOLBOX=1",
    ],
}

# Device tree overlays in the recovery_dtbo section. Kept because that image
# layout is the one proven to boot from recovery on this phone.
OVERLAYS = ["wnd1", "wnd2e", "wnd2"]

BUSYBOX_APPLETS = [
    "sh", "ls", "mkdir", "ln", "cat", "dd", "grep", "sed", "clear", "resize",
    "fold", "tail", "head", "sleep", "dmesg", "mount", "umount", "sync",
    "reboot", "poweroff", "mv",
]


# --- SD card system (flip sd) -------------------------------------------------

SD_BOOT_LABEL = "flipboot"  # ext2: kernel, device tree, extlinux.conf (read by lk2nd)
SD_ROOT_LABEL = "fliproot"  # ext4: Debian
SD_BOOT_SIZE = 256 << 20
SD_CMDLINE = "console=tty1 fbcon=font:10x18 loglevel=4"

# Debian packages on top of the minimal base (no recommends). No SSH server, no
# sudo: administration is flipd over USB only.
SD_PACKAGES = [
    # system
    "systemd-sysv", "udev", "dbus", "libpam-systemd", "kmod",
    "systemd-zram-generator", "procps", "less", "nano", "e2fsprogs", "fdisk", "kbd",
    "tzdata", "ca-certificates", "unattended-upgrades", "systemd-timesyncd", "iproute2",
    # flipd
    "python3", "python3-cryptography",
    # networking (outgoing only; nftables drops everything incoming)
    "network-manager", "wpasupplicant", "iw", "wireless-regdb", "rfkill", "nftables",
    "iputils-ping",
    # audio
    "alsa-utils", "alsa-ucm-conf",
    # modem (installed but switched off until it's brought up deliberately)
    "rmtfs", "tqftpserv", "qrtr-tools", "modemmanager", "libqmi-utils",
]
