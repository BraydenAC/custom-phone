# Roadmap

Where the phone is headed, and what has been put off along the way. Nothing
here is scheduled: it's a list to choose from when deciding what to do next.
Items move out when they're picked up (into a feature branch) or dropped.

## Vision

A flip phone run mostly by an agent you talk to. Speech in, speech out, the
keypad for the rest. The phone stays small and frugal, and heavy work goes
to a home server you control.

- **The phone** (Debian on the SD card) handles calls, SMS, mobile data and
  Wi-Fi, audio, and a lightweight agent front end. It runs unprivileged, with
  no admin access.
- **The home storage server** holds documents, images and history, and runs
  analytics and the heavier AI/ML (speech recognition, larger models).
  `flip` becomes its client too.
- **Admin** happens only over the USB cable from trusted computers. There is
  no SSH and nothing listens on the network.
- **Independence:** every input is pinned and verified, and nothing depends
  on a server or branch we don't control.

## Next candidates

Things that are ready to pick up or close to it.

- **Unplugged sleep checks.** Confirm the 15-minute idle sleep happens on
  battery, and that Wi-Fi, the USB link, the modem and the panel come back
  after waking. Needs a battery run (`battery-measure` skill).
- **Protected settings.** Agree which settings are root-only and changed
  only through flipd, as opposed to what the agent may change. This is a
  prerequisite for the agent.
- **Phone agent harness.** The agent's own tool-based harness: tools, not
  code or a shell, running as user `phone`. Separate from the development
  harness in `.claude/`.
- **CI.** GitHub Actions running `uv run pytest` on a clean checkout.
- **Queue link sessions.** Only one `flip phone` session works at a time.
  Parallel chunk agents reading the phone can collide, and the guard then asks
  the user. `flip` could take a lock so a second session waits its turn
  instead of failing (the `phone-link` area).

## Waiting on something

- **Mobile data, SMS, calls, call audio:** waiting for a SIM in the phone.
  The modem already gets as far as ModemManager's `sim-missing`.
- **The home storage server:** waiting on the agent and the protected
  settings design.

## Put off

- **CPU frequency scaling:** only if battery logs show idle drain is worth it.
- **Number keys as wake sources:** only the power key and the lid wake the
  phone today.
- **Powering off while plugged in:** this has crashed the phone into
  crash-dump mode. Find the cause; until then, the shutdown screen asks to
  unplug.
- **Shutdown animation length:** decide how long it should run.
- **Handshake animation:** black silhouette hands on traffic-cone orange,
  trying mismatched gestures (handshake, heart, high five, fist bump) until
  they land a dap. Drawn in code. A candidate for the link handshake or the
  startup screen.
- **Progress on the phone while flashing:** lk2nd's fastboot has no screen
  output, so progress may only be possible on the computer.
