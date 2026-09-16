# zmk-layer-hud

An on-screen HUD for a ZMK keyboard that shows the **real** active layers and lights the keys and
combos as they are typed. The keyboard reports its layer state itself, on every transition, and
the picture comes from the keymap-drawer file you already draw your layout with. Nothing is
guessed from the characters that arrive.

Two pieces:

- a small **ZMK module** (`firmware/`) that announces the active layers inside the ordinary
  keyboard HID report, using usages no operating system maps to a key;
- a **host** (`host/`, Python plus a thin window host per OS) that reads those reports, converts
  your keymap-drawer YAML at runtime, and drives the HUD page (`hud/`).

The keyboard's HID reports are the **only source**: layers, key presses, releases and modifiers
are all read from them. No OS event tap, no evdev, no other daemon. It works with any ZMK
keyboard and any keymap-drawer file, and on macOS and Linux the same way.

```
firmware/     ZMK module: zmk,layer-signal
host/         hudfeed.py (feeds → WebSocket or stdout), keymap.py (keymap-drawer YAML → HUD keymap),
              macos/ (PyObjC overlay panel), linux/ (Hyprland layer-shell panel)
hud/          the pages: layer HUD and typed-keys strip
config/       example.yaml (any keyboard), diamond.yaml (the author's Diamond, with every option)
docs/         zmk-setup.md (firmware, generic), keyboards-repo.md (the author's own build system)
contrib/udev/ hidraw access rule for Linux
```

## Quick start

### 1. Firmware

Follow [docs/zmk-setup.md](docs/zmk-setup.md): add the module to your `west.yml`, put one node in
your keymap, set `CONFIG_ZMK_HID_KEYBOARD_REPORT_SIZE=12` on the central/dongle, build, flash.

```c
/ {
    layer_signal {
        compatible = "zmk,layer-signal";
        heartbeat-ms = <2000>;
    };
};
```

### 2. Host environment

macOS (Homebrew Python; Apple's `/usr/bin/python3` has none of the packages):

```bash
git clone https://github.com/rafaelromao/zmk-layer-hud ~/projects/zmk-layer-hud
cd ~/projects/zmk-layer-hud
brew install hidapi && make venv          # .venv with hidapi, keymap-drawer, websockets, pyobjc
```

Linux (Arch/Hyprland shown; the panel needs the system GTK bindings, the feed runs in the venv):

```bash
git clone https://github.com/rafaelromao/zmk-layer-hud ~/projects/zmk-layer-hud
cd ~/projects/zmk-layer-hud
sudo pacman -S python-gobject webkit2gtk-4.1 gtk-layer-shell
make venv && .venv/bin/pip install websockets   # hidapi + keymap-drawer (+ the WebSocket server)
sudo cp contrib/udev/60-zmk-layer-hud.rules /etc/udev/rules.d/ && sudo udevadm control --reload-rules && sudo udevadm trigger
```

### 3. Point it at your keymap

```bash
mkdir -p ~/.config/zmk-layer-hud && cp config/example.yaml ~/.config/zmk-layer-hud/config.yaml
```

Edit the one required line:

```yaml
keymap: ~/path/to/my-keymap.yaml        # the YAML you draw with keymap-drawer
```

For a file produced by `keymap parse` from your `.keymap`, that is everything: the file's layer
order is the ZMK layer order. Check it converts:

```bash
.venv/bin/python3 host/keymap.py
```

### 4. Run

```bash
host/macos/start.sh        # macOS: transparent always-on-top panels (PyObjC + WKWebView)
bash host/linux/hud.sh     # Linux: layer-shell panels on the recording monitor
```

Both start `hudfeed.py` and open two windows on the recording display (the external one when
there is one): the layer HUD top-right and the typed-keys strip bottom-left. `stop` closes them,
`log` (macOS) tails the panel and feed logs. The first run on macOS asks for Input Monitoring for
the app you launched from (the terminal); grant it once and restart.

### 5. Test it

Watch the status line under the board:

1. **"waiting for the keymap…"** for more than a second means the config or the YAML failed;
   `host/macos/start.sh` already printed the reason, or run `.venv/bin/python3 host/keymap.py`.
2. **"waiting for the keyboard's layers…"** means the keymap is drawn but no announcement has
   arrived. Within two seconds of the keyboard being connected (heartbeat) it must flip to
   **"layers from the keyboard"**. If it does not, `run/hudfeed.log` says whether the keyboard
   opened at all (`reading <name>`), else see Troubleshooting.
3. Hold a layer key: the banner names the layer and lists the active ZMK layer names, the key
   you hold lights blue (activator), and the legends change. Release: back to the base layer.
4. Type: each key flashes orange on the layer the keyboard reports, and the strip shows the
   characters. A combo lights all its keys and draws its output in a pill above them. A dashed
   flash means the character is not on that layer in your YAML (a legend spelled differently).
5. Hold Shift: the keys whose hold legend carries ⇧ get a green inset (home-row mods).
6. Edit a legend in the keymap-drawer YAML and save: the HUD redraws within a second. Break the
   YAML on purpose: the HUD keeps the last good keymap and `run/hudfeed.log` names the error.

Without the keyboard, `.venv/bin/python3 host/hudfeed.py --stdout --no-ws --debug` prints
everything the pages would receive; `--raw` adds every report as hex.

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

Costs: the report must have room (`CONFIG_ZMK_HID_KEYBOARD_REPORT_SIZE=12`), and Linux's evdev
shows the usages as `KEY_UNKNOWN` events with no keysym. F-keys were considered and rejected:
they are real keys in many keymaps, macOS has no keycodes above F20, and Linux maps F21–F23 to
touchpad keysyms.

## The keymap comes from your keymap-drawer file, live

`hudfeed.py` converts the YAML named in the config at start and again whenever the file changes,
so editing your layout updates the HUD without restarting anything. Positions, sizes and rotation
of the keys come from keymap-drawer itself, so every layout kind it draws works:
`cols_thumbs_notation`, `ortho_layout`, `qmk_keyboard` / `zmk_keyboard` / `zmk_shared_layout`
from its database, `qmk_info_json` and `dts_layout` files. Layers, combos (including
`trigger_keys`), `$$glyph$$` legends and `▽` transparency are read the same way the drawer reads
them. Your keymap-drawer config (key sizes, glyphs) can be named with `drawer_config:`.

**Layer ids.** For a YAML produced by `keymap parse`, nothing is needed. A curated file whose
layers do not match ZMK's one to one (several ZMK layers drawn as one, layers not drawn at all)
gets a `layers:` section: a `dtsi` with the `#define NAME n` block and, per define, the drawer
layer that shows it, plus a label and banner class. [config/diamond.yaml](config/diamond.yaml)
is a complete example; `python3 host/keymap.py` explains what is wrong when a mapping is off.

All other config keys are optional and documented in the docstring of `host/keymap.py`:
`keyboard` (pick one of several ZMK boards), `signal` (non-default usages), `base`, `combos`
(combo layer coverage the drawer understates), `extras` (inference hints), `codes` (see below),
`title`.

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
  decoded from the same HID reports (`code` is the HID usage, `chars` the US-layout character,
  `name` spelled like Hammerspoon's `hs.keycodes.map`). Held modifier flags light the keys whose
  hold legend carries that modifier (home-row mods).
- A key that cannot be placed on the live stack (a legend the drawer spells differently from the
  character the usage maps to) is attributed by inference and drawn **dashed**, so it is never
  mistaken for keyboard truth.

A WebSocket host opens the page as `index.html?ws=ws://127.0.0.1:8766` and receives
`{"kind":"keymap",…}`, `{"kind":"layers","ids":[…]}` and `{"kind":"key",…}`; the ✕ button sends
`{"kind":"close"}`. `hud/keys.html` is the typed-keys strip (`window.keys.key(event)`). For
development, `index.html?keymap=keymap.json` loads a dumped message
(`python3 host/keymap.py --dump > hud/keymap.json`).

## Host feed

`host/hudfeed.py` sends the keymap (and re-sends it on change) and reads the keyboard's raw HID
input reports with hidapi (the keyboard named in the config, else any 1d50:615e). From each
keyboard report it derives the layer set (when the commit usage is present), key presses and
releases (the difference between consecutive reports) and modifier changes (the modifier byte).
Characters come from the usage through a US-layout table, which is what a ZMK keymap emits.
Outputs: a WebSocket on 127.0.0.1:8766 (both panels use it) and/or `--stdout` JSON lines.

```
--config PATH        config file (default $ZMKHUD_CONFIG, ~/.config/zmk-layer-hud/config.yaml)
--stdout / --no-ws   output selection               --vid/--pid/--name  override the config's keyboard
--base/--commit      override the config's signal   --no-report-id      firmware without HID report ids
--no-keys            layers only, no key events     --no-keymap         do not send the keymap
--debug              log layer messages             --raw               DEBUG: dump every report as hex
```

## Troubleshooting

- **`cannot open <keyboard>`** on macOS (`run/hudfeed.log`): the app you launched from needs
  Input Monitoring (System Settings → Privacy & Security), and
  Karabiner-Elements must not "modify events" for that keyboard, or it seizes the device.
  `python3 host/hiddiag.py` prints the raw IOKit code that names the blocker.
- **`cannot open`** on Linux: hidraw permissions; install `contrib/udev/60-zmk-layer-hud.rules`
  and replug (BLE: reconnect).
- **No `layers` lines** although the device opened: the firmware is not announcing. Run with
  `--raw`: 9-byte reports mean the report size was not raised (6 slots); no `df` byte means the
  node is missing from the build. Check `CONFIG_ZMK_LAYER_SIGNAL=y` in the build's `.config`.
- **Keys drawn dashed** while live: the key's usage maps to a character the drawer spells
  differently (macros, unknown `$$glyph$$` ids). Extend `GLYPHS` in `host/keymap.py` or the legend.
- **`python3 host/keymap.py` fails**: it says which layer, combo or mapping is wrong.

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
- Keys are located by the character their HID usage maps to (US layout), on the layer the
  keyboard reports; key positions themselves are not transmitted. Macros that type several keys
  light each key they type.
- Without keymap-drawer installed only `cols_thumbs_notation` layouts render, and combos given
  as `trigger_keys` are skipped.
- The Linux host is ported from an earlier kit and not yet run on hardware; the macOS panel is
  new and needs its first run on a real display (window levels, transparency, Input Monitoring).
