/* SPDX-License-Identifier: MIT
 *
 * reboot-mode MODE: restart with a reboot reason, like `reboot MODE` on
 * Android. Busybox's reboot can't pass one. The phone's bootloader reads it
 * from the PMIC (pm8916 pon reboot-mode): "recovery" starts the recovery
 * partition (lk2nd in the kaios-first layout, KaiOS in linux-first);
 * "bootloader" is stock fastboot.
 *
 * Built against the kernel tree's nolibc (no C library needed):
 *   gcc -static -nostdlib -include tools/include/nolibc/nolibc.h
 */
#include <linux/reboot.h>

/* Kernels before 6.7 don't map a writable segment that has no bytes in the file
 * (only zeroed variables, like nolibc's environ): the program then crashes on
 * its first write. One initialised variable gives the segment file contents.
 * tests/test_images.py checks the built binary for this. */
__attribute__((used)) static volatile int data_in_file = 1;

int main(int argc, char **argv)
{
	if (argc != 2) {
		fprintf(stderr, "usage: reboot-mode recovery|bootloader\n");
		return 2;
	}
	my_syscall0(__NR_sync);
	int err = sys_reboot(LINUX_REBOOT_MAGIC1, LINUX_REBOOT_MAGIC2,
			     LINUX_REBOOT_CMD_RESTART2, argv[1]);
	fprintf(stderr, "reboot failed (error %d)\n", -err);
	return 1;
}
