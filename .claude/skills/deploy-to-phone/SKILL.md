---
name: deploy-to-phone
description: Get changed sd/overlay files (programs, units, settings) onto the running phone over the USB link and check they work; and diagnose the running phone (journal, failed units, sysfs for battery/lid/screen/input, Wi-Fi, modem, CPU use, what broke after waking from sleep). Use when testing a phone-side change live, when something on the phone misbehaves, or when the user asks how deploying or debugging on the phone works.
---

# Deploy to the phone, and diagnose it

The phone runs Debian from the SD card. That card is the live root file
system, so a file pushed over the link stays until it's changed again. New
cards get the same files from `sd/overlay/` through `flip sd build`. No
reflash is needed for overlay changes.

## Before you start

- Check that the phone is reachable: `uv run flip phone info`. If it isn't, the
  phone may be asleep (lid closed on battery), off, or in another OS. Ask the
  user rather than guessing.
- Only one `flip phone` session works at a time. Don't start one in the
  background while another is running, and run them one after another, not
  in parallel.
- Run `uv run pytest` first. The tests load the overlay scripts, so a syntax
  error shows up there instead of as a dead service.

## Deploying a file

1. Push it. The checksum is verified, and the file arrives owned by root:
   `uv run flip phone push sd/overlay/PATH /PATH`
2. Set owner and mode in one shell line. Mode follows the file type:
   - programs in `/usr/lib/flip/`, `/usr/lib/flipd/`, `system-shutdown/`: 755
   - units, settings, rules: 644
   - `/etc/flipd/*`: 600, directory 700

   ```sh
   uv run flip phone run -- sh -c 'chown root:root /PATH && chmod 755 /PATH'
   ```
3. Make it take effect:

   | What changed | Then |
   |---|---|
   | A program run by a service | `systemctl restart UNIT` |
   | A unit file | `systemctl daemon-reload && systemctl restart UNIT` |
   | A new unit | `daemon-reload`, then `systemctl enable --now UNIT`, and add it to `sd/setup.sh` (see `phone-service`) |
   | `/etc/flip/*.conf` | restart the service that reads it |
   | logind / journald drop-ins | `systemctl restart systemd-logind` (or `systemd-journald`) |
   | udev rules | `udevadm control --reload && udevadm trigger` |
   | splash | no restart: preview with `/usr/lib/flip/splash --demo 14` or `--shutdown poweroff --demo 3` |

4. Check it:
   `uv run flip phone run -- sh -c 'systemctl is-active UNIT; journalctl -b -u UNIT -n 30 --no-pager'`

Do several files in one `sh -c` line where you can. Each `flip phone run` is a
new session with its own handshake.

## Changes that cut the link

flipd, flip-gadget, `70-flip-link.rules` and the USB gadget configuration
carry the session you're using. Restarting them drops the link mid-command.
Push the file, then tell the user that it applies on the next boot (or
restart it as the last command and expect the connection to drop). If the
link doesn't come back, the fix is the card in the computer's reader. Say
that before making the change.

## Things that need the user

- Rebooting or powering off: never with USB plugged in (it has crashed this
  phone) unless the user asks. A reboot over the link is fine when the user
  asks for it.
- Anything that needs the cable out, like battery-only behaviour (idle sleep
  only happens unplugged). Explain what to do and what to check afterwards.
- Wi-Fi passwords: the user types them, for example with
  `flip phone shell` then `nmcli --ask dev wifi connect SSID`.

## Diagnosing

For recipes and the hardware's sysfs paths, see [diagnose.md](diagnose.md).
It covers the journal, failed units, battery, lid and screen state, input
events, Wi-Fi, the modem, CPU use of a service, and what to check after the
phone wakes from sleep.

For anything that means reading a lot of log output, delegate to the
`log-investigator` subagent with a specific question. It reads the logs and
returns only the answer and the lines that show it.
