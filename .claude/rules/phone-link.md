---
paths:
  - "src/flip/phone.py"
  - "src/flip/link/**"
  - "sd/overlay/usr/lib/flipd/**"
  - "sd/overlay/etc/systemd/system/flipd.service"
  - "sd/overlay/etc/systemd/system/flip-gadget.service"
  - "sd/overlay/etc/udev/rules.d/70-flip-link.rules"
connects: [cli, sd-image, phone-overlay, config-and-profile, battery, harness, tests]
---

# flip-link: the wired admin connection

## Features

- `src/flip/link/` holds both ends of the protocol:
  - `frame.py`: frames over the serial byte stream, with CRC;
  - `secure.py`: an Ed25519 handshake, an X25519 key exchange, and a ChaCha20-Poly1305
    channel;
  - `messages.py`: JSON messages inside the channel;
  - `keys.py`: identity keys and the trusted-hosts list;
  - `client.py`: the computer side (find the port, connect, `run`, `shell`, `push`, `pull`);
  - `server.py`: flipd, the phone side. It runs commands (in a pty for `shell`), transfers
    files with checksums, and manages the trusted list. Higher-level steps, such as the
    toolbox-once restart, are shell commands that `sd.py` sends through it.
- `phone.py`: the computer's link identity (`~/.config/flip/host-key.pem`), pinning the
  phone's key, `session()` for every `flip phone` command, and `setup-host` (a udev rule so
  ModemManager leaves the port alone).
- On the phone, `flip-gadget.service` + `gadget.sh` create the USB ACM serial gadget,
  `flipd.service` runs `flipd.py`, and the udev rule names the port.

## Connected to

- sd-image copies `src/flip/link/` into the image. The same code runs on both ends, so a
  protocol change needs both a new flip on the computer and a new flipd on the phone
  (`PROTOCOL_VERSION`).
- `flip phone`, `flip battery` and `flip sd write/update` (with the card in the phone) all
  go through `phone.session()`. A link change affects all of them.
- `sd.py` builds flipd's keys and trusted list at image time. config-and-profile holds the
  key paths.
- The harness (guard, log-investigator) assumes one session at a time, and that
  `flip phone run -- CMD` takes the command as separate arguments.

## Coding notes

- **Server code runs on the phone's Python** (Debian 13's `python3` and
  `python3-cryptography`), not the computer's: stdlib plus `cryptography` only, no
  third-party imports, no features newer than the phone's Python. The computer pins a newer
  `cryptography` (≥ 50) than Debian ships. Use only APIs that exist in both.
- **This is the security boundary.** An unknown computer must get nothing: no shell, no
  login prompt, no partial information. Any change to the handshake, the trusted list or
  command execution needs tests for rejection (see `tests/test_link.py`), and the user
  should hear about it explicitly.
- Never add a network listener. The link is USB serial only.
- The phone refuses to remove its last trusted computer. Keep that invariant.
- Changing flipd or the gadget on a live phone cuts the session in use (see the
  `deploy-to-phone` skill). Without a working link, recovery means the card in the computer's
  reader.
