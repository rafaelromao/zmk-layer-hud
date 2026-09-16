# zmk-layer-hud

A keyboard HUD for a ZMK keyboard that shows the **real** active layers and lights the keys and
combos as they are typed. The keyboard reports its layer state itself, on every transition, so
nothing is guessed from the characters that arrive.

It grew out of the showcase HUD in [zmk-vim-mode](https://github.com/rafaelromao/zmk-vim-mode)
(`showcase/hud`), which had to infer layers because a host only sees keycodes. That kit stays as
it is; this repo is the standalone, reactive successor.

```
firmware/     ZMK module: zmk,layer-signal — announces the active layers in the HID report
host/         hudfeed.py (raw HID reader + key/mode feeds, WebSocket or stdout), macOS and Linux hosts
hud/          the pages (layer HUD, typed-keys strip) and keymap/build.py (keyboards repo → keymap.json)
docs/         keyboards-repo.md: the three edits the keymap repo needs
contrib/udev/ hidraw access rule for Linux
```

## How the keyboard talks to the host

There is no spare channel from a keyboard to a host that works over USB and BLE on every OS
without drivers, except the keyboard report itself. The HID Usage Tables reserve keyboard-page
usages 0xA5–0xDF: no operating system maps them to a key, so a report carrying them is delivered
to raw-HID readers and ignored by everything else. The module uses them as a data word:

- On every layer change (coalesced 3 ms), for each active layer id `L ≥ 1` the usage `0xC0 + L`
  and then the commit usage `0xDF` are added to the keyboard report, which is sent once; 10 ms
  later they are removed and the report is sent again. Layer 0 is always active and never sent.
- A report is a layer announcement if and only if it contains the commit usage, and then it
  carries the complete set. The host decodes exactly that report and discards every other one,
  so real keys pressed meanwhile cannot produce a torn state, and a missed report heals on the
  next change. With `heartbeat-ms` set, the set is repeated while idle so a HUD started
  mid-session converges.
- The report is written directly (`zmk_hid_keyboard_press` + `zmk_endpoint_send_report`), not
  raised as key events, so behaviours that watch key presses (auto-layer, adaptive keys, caps
  word, sticky keys) do not notice.

Costs: the report must have room (`CONFIG_ZMK_HID_KEYBOARD_REPORT_SIZE=12`, see the docs), and
Linux's evdev shows the usages as `KEY_UNKNOWN` events with no keysym. F-keys were considered and
rejected: F13–F15 are real keys in the author's keymap, F24 is the adaptive-key sentinel, macOS
has no keycodes above F20, and Linux maps F21–F23 to touchpad keysyms.

## Install

Keyboard: follow [docs/keyboards-repo.md](docs/keyboards-repo.md) (module, one devicetree node,
report size), build and flash the central/dongle.

Host, macOS:

```bash
brew install hidapi yq && python3 -m pip install hidapi
python3 hud/keymap/build.py            # needs ~/projects/keyboards (or KEYBOARDS_REPO)
host/macos/start.sh                    # Hammerspoon with require("hs.ipc") in init.lua
```

Host, Linux (Arch/Hyprland):

```bash
sudo pacman -S python-hidapi python-evdev python-websockets yq jq
sudo cp contrib/udev/60-zmk-layer-hud.rules /etc/udev/rules.d/ && sudo udevadm control --reload-rules && sudo udevadm trigger
bash host/linux/hud.sh                 # Chromium --app windows + hudfeed.py
```

## Pages

`hud/index.html` exposes `window.hud`:

- `hud.setLayers([ids])` — the keyboard's active ZMK layer ids. From the first call on, the HUD
  is *live*: the stack is exactly what the keyboard reports (drawer layers looked up through
  `keymap.json`'s `zmk_layers`, higher ids on top), the banner names the top layer and lists the
  set, activator thumbs light, and a typed key is resolved on that stack, combos included.
- `hud.key(event)` — `{type: keyDown|keyUp|flagsChanged, name, chars, code, flags, repeat}`,
  `name` spelled like Hammerspoon's `hs.keycodes.map`. Held modifier flags light the keys whose
  hold legend carries that modifier (home-row mods).
- `hud.setMode(code, mode, reason)` — the zmk-vim-mode daemon's decision. Shown as the banner's
  reason; before the first `setLayers` (old firmware, no reader) it also supplies the vim layers,
  and the page falls back to the showcase's character-based inference.
- A key that cannot be placed on the live stack (a synthesized key in a rehearsal, a legend the
  drawer spells differently) is still attributed by that inference and drawn **dashed**, so it is
  never mistaken for keyboard truth.

A WebSocket host opens the page as `index.html?ws=ws://127.0.0.1:8766` and sends
`{"kind":"layers","ids":[…]}`, `{"kind":"key",…}`, `{"kind":"mode",…}`; the ✕ button sends
`{"kind":"close"}`. `hud/keys.html` is the typed-keys strip (`window.keys.key(event)`).

## Host feed

`host/hudfeed.py` reads the keyboard's raw HID input reports with hidapi (VID:PID 1d50:615e by
default, `--name` to pick one keyboard), decodes the announcements, and never logs or forwards
any other report: your typing stays in the report it arrived in. Outputs: a WebSocket on
127.0.0.1:8766 and/or `--stdout` JSON lines (the macOS host runs it that way under Hammerspoon,
which already has Input Monitoring). On Linux it also feeds key events from evdev and the
daemon's decisions from `journalctl`.

```
--layers-only        raw HID only (macOS host)      --base/--commit   match a custom firmware node
--stdout / --no-ws   output selection               --no-report-id    firmware without HID report ids
--debug              log layer/mode messages to stderr
```

## Tests

```bash
make test            # firmware encode/decode policy (C, host-compiled) + host decoder (Python)
make keymap          # rebuild hud/keymap.json from the keyboards repo
```

The C and Python tests share their vectors; `firmware/src/layer_signal_policy.h` is the one
definition of the wire format.

## Known limits

- Layer ids must stay below 31 (30 with the default usages).
- Keys are still located by the character the OS reports, on the layer the keyboard reports.
  A key whose output the drawer spells differently from the OS (custom macros) is shown dashed.
- macOS: if Karabiner-Elements modifies the keyboard's events it seizes the device and the
  reader sees nothing; exclude the keyboard under Karabiner → Devices.
- Linux host and Hyprland window rules are ported from the showcase kit and not yet run on
  hardware.
