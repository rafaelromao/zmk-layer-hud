# zmk-layer-hud

A keyboard HUD for a ZMK keyboard that shows the **real** active layers and lights the keys and
combos as they are typed. The keyboard reports its layer state itself, on every transition, so
nothing is guessed from the characters that arrive.

It grew out of the showcase HUD in [zmk-vim-mode](https://github.com/rafaelromao/zmk-vim-mode)
(`showcase/hud`), which had to infer layers because a host only sees keycodes. That kit stays as
it is; this repo is the standalone, reactive successor.

```
firmware/     ZMK module: zmk,layer-signal — announces the active layers in the HID report
host/         hudfeed.py (keymap + raw HID layers + key/mode feeds, WebSocket or stdout),
              keymap.py (keymap-drawer YAML → HUD keymap, live), macOS and Linux hosts
hud/          the pages: layer HUD and typed-keys strip (no keymap baked in)
config/       diamond.yaml (the author's Diamond), example.yaml (any keyboard from `keymap parse`)
docs/         keyboards-repo.md: the three edits the keymap repo needs
contrib/udev/ hidraw access rule for Linux
```

## The keymap comes from your keymap-drawer file, live

The HUD draws whatever `~/.config/zmk-layer-hud/config.yaml` points at:

```yaml
keymap: ~/path/to/my-keymap.yaml        # the YAML you draw with keymap-drawer
```

`hudfeed.py` converts it at start and again whenever the file changes, so editing your layout
updates the HUD without restarting anything. Positions, sizes and rotation of the keys come from
keymap-drawer itself (`pip install keymap-drawer`), so every layout kind it draws works:
`cols_thumbs_notation`, `ortho_layout`, `qmk_keyboard` / `zmk_keyboard` / `zmk_shared_layout`
from its database, `qmk_info_json` and `dts_layout` files. Layers, combos (including
`trigger_keys`), `$$glyph$$` legends and `▽` transparency are read the same way the drawer reads them.

Layer ids: for a YAML produced by `keymap parse` from a `.keymap`, the file's layer order is the
ZMK layer order and nothing else is needed. A curated file whose layers do not match one to one
(several ZMK layers drawn as one, layers not drawn at all) gets a `layers:` section naming the
`config.dtsi` with the `#define` block and, per define, the drawer layer that shows it, as in
[config/diamond.yaml](config/diamond.yaml). Every other key in the config is optional; see
[config/example.yaml](config/example.yaml) and the docstring of `host/keymap.py`.

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

Host, macOS (Homebrew Python; Apple's `/usr/bin/python3` has none of the packages):

```bash
brew install hidapi && make venv          # .venv with hidapi + keymap-drawer; the hosts pick it up
mkdir -p ~/.config/zmk-layer-hud && cp config/diamond.yaml ~/.config/zmk-layer-hud/config.yaml   # then edit the paths
host/macos/start.sh                    # Hammerspoon with require("hs.ipc") in init.lua
```

Host, Linux (Arch/Hyprland):

```bash
sudo pacman -S python-hidapi python-evdev python-websockets python-gobject webkit2gtk-4.1 gtk-layer-shell
pip install keymap-drawer              # or pipx; brings PyYAML
sudo cp contrib/udev/60-zmk-layer-hud.rules /etc/udev/rules.d/ && sudo udevadm control --reload-rules && sudo udevadm trigger
bash host/linux/hud.sh                 # layer-shell panel + hudfeed.py
```

`python3 host/keymap.py` checks the configured file converts and says why when it does not;
`--dump` prints the message the page receives.

## Pages

`hud/index.html` exposes `window.hud`:

- `hud.load(keymap)` — the `{"kind":"keymap", …}` message from `host/keymap.py`: physical layout,
  layers, combos, the ZMK layer table, hints. Re-sent when the drawer file changes; the page
  rebuilds the board and keeps the live layer set.
- `hud.setLayers([ids])` — the keyboard's active ZMK layer ids. From the first call on, the HUD
  is *live*: the stack is exactly what the keyboard reports (higher ids on top, undrawn layers
  skipped), the banner names the top layer and lists the set, activator keys light, and a typed
  key is resolved on that stack, combos included.
- `hud.key(event)` — `{type: keyDown|keyUp|flagsChanged, name, chars, code, flags, repeat}`,
  `name` spelled like Hammerspoon's `hs.keycodes.map`. Held modifier flags light the keys whose
  hold legend carries that modifier (home-row mods).
- `hud.setMode(code, mode, reason)` — the zmk-vim-mode daemon's decision. Shown as the banner's
  reason; when the config defines `codes`, it also supplies the base layers before the first
  `setLayers` (old firmware, no reader), and the page falls back to character-based inference
  guided by the config's `extras`.
- A key that cannot be placed on the live stack (a synthesized key in a rehearsal, a legend the
  drawer spells differently) is still attributed by that inference and drawn **dashed**, so it is
  never mistaken for keyboard truth.

A WebSocket host opens the page as `index.html?ws=ws://127.0.0.1:8766` and sends
`{"kind":"keymap",…}`, `{"kind":"layers","ids":[…]}`, `{"kind":"key",…}`, `{"kind":"mode",…}`;
the ✕ button sends `{"kind":"close"}`. `hud/keys.html` is the typed-keys strip
(`window.keys.key(event)`). For development, `index.html?keymap=keymap.json` loads a dumped message.

## Host feed

`host/hudfeed.py` sends the keymap (and re-sends it on change), reads the keyboard's raw HID
input reports with hidapi (the keyboard named in the config, else any 1d50:615e), decodes the
announcements, and never logs or forwards any other report: your typing stays in the report it
arrived in. Outputs: a WebSocket on 127.0.0.1:8766 and/or `--stdout` JSON lines (the macOS host
runs it that way under Hammerspoon, which already has Input Monitoring). On Linux it also feeds
key events from evdev and the daemon's decisions from `journalctl`.

```
--config PATH        config file (default $ZMKHUD_CONFIG, ~/.config/zmk-layer-hud/config.yaml)
--layers-only        keyboard feeds only: keymap + raw HID (macOS host)
--stdout / --no-ws   output selection               --vid/--pid/--name  override the config's keyboard
--base/--commit      override the config's signal   --no-report-id      firmware without HID report ids
--debug              log layer/mode messages        --raw               DEBUG: dump every report as hex
```

## Tests

```bash
make test            # firmware encode/decode policy (C) + host decoder and keymap conversion (Python)
make keymap          # check the configured keymap-drawer YAML converts
```

The C and Python decoder tests share their vectors; `firmware/src/layer_signal_policy.h` is the
one definition of the wire format. Without keymap-drawer installed the conversion tests cover the
built-in `cols_thumbs_notation` fallback only.

## Known limits

- Layer ids must stay below 31 (30 with the default usages).
- Keys are still located by the character the OS reports, on the layer the keyboard reports.
  A key whose output the drawer spells differently from the OS (custom macros, glyph legends the
  page does not know) is shown dashed. `GLYPHS` in `host/keymap.py` maps glyph ids to text.
- Without keymap-drawer installed only `cols_thumbs_notation` layouts render, and combos given
  as `trigger_keys` are skipped.
- macOS: if Karabiner-Elements modifies the keyboard's events it seizes the device and the
  reader sees nothing; exclude the keyboard under Karabiner → Devices.
- Linux host and Hyprland window rules are ported from the showcase kit and not yet run on
  hardware.
