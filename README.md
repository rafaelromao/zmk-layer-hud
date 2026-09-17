# zmk-layer-hud

An on-screen HUD for ZMK keyboards. It shows the layer you are on and lights the keys, combos
and macros as you press them, drawn from the same keymap-drawer file you document your layout
with. The keyboard itself reports its layers and key positions, so nothing is guessed.

![The HUD following a keyboard through its vim layers: typing on the base layer, a combo into vim
mode, NORMAL with h j k l lit one at a time, v into VISUAL to select a word, yank and put it back,
then i into INSERT and Esc out](docs/hud.gif)

*A [Diamond](https://github.com/rafaelromao/keyboards) running
[zmk-vim-mode](https://github.com/rafaelromao/zmk-vim-mode), whose daemon moves the keyboard
between the vim layers. Rendered by `docs/make-gif.sh` from `docs/demo-vim.json`.*

- **One source**: the keyboard's HID reports. No OS event tap, no daemon, no per-app plugin.
- **Any ZMK keyboard**: a small ZMK module on the keyboard, a keymap-drawer YAML on the host.
- **Live**: edit the YAML and the HUD redraws; every size and timing lives in one config file.
- macOS (native overlay panel) and Linux (Hyprland layer-shell panel).

## Quick start

**Keyboard.** Add the module to your ZMK config and one node to your keymap, set
`CONFIG_ZMK_HID_KEYBOARD_REPORT_SIZE=12` on the central/dongle, build, flash. Step by step in
[docs/zmk-setup.md](docs/zmk-setup.md).

```c
/ {
    layer_signal {
        compatible = "zmk,layer-signal";
        heartbeat-ms = <2000>;
        positions;
    };
};
```

**Host.**

```bash
git clone https://github.com/rafaelromao/zmk-layer-hud ~/projects/zmk-layer-hud
cd ~/projects/zmk-layer-hud
brew install hidapi && make venv           # macOS (Homebrew Python); Linux: see below
mkdir -p ~/.config/zmk-layer-hud && cp config/example.yaml ~/.config/zmk-layer-hud/config.yaml
```

Edit the one required line of the config, `keymap:`, to point at your keymap-drawer YAML, check
it converts, and run:

```bash
.venv/bin/python3 host/keymap.py
host/macos/start.sh                        # macOS;  Linux: bash host/linux/hud.sh
```

The panel opens on the screen with keyboard focus (drag it anywhere; it remembers). Within two
seconds the status line disappears and the banner follows your keyboard. `start.sh stop` closes
it, `start.sh log` tails the logs.

Linux needs the system GTK bindings for the panel and the udev rule for hidraw:

```bash
sudo pacman -S python-gobject webkit2gtk-4.1 gtk-layer-shell
make venv && .venv/bin/pip install websockets
sudo cp contrib/udev/60-zmk-layer-hud.rules /etc/udev/rules.d/ && sudo udevadm control --reload-rules && sudo udevadm trigger
```

### Try it without a keyboard

`examples/` holds two keymaps from keymap-drawer's own examples with ready configs: a 3x5+3
split (`config/example-3x5.yaml`) and a 4x12 ortho board (`config/example-4x12.yaml`).

```bash
.venv/bin/python3 host/keymap.py --config config/example-3x5.yaml --dump > hud/keymap.json
python3 -m http.server -d hud 8765         # open http://localhost:8765/index.html?keymap=keymap.json
```

In the browser console, `hud.setLayers([1])` switches layers, `hud.pressAt(13)` lights a key and
`hud.releaseAt(13)` lets it go, so the whole page can be exercised without hardware.

The same calls can be scripted: `&demo=N` renders step N of a JSON demo script (`&script=<url>`,
or `demo.json` beside the page) and stops there, and `bash docs/make-gif.sh` screenshots every
step with a headless browser and assembles a GIF:

```bash
bash docs/make-gif.sh --config config/diamond.yaml --script docs/demo-vim.json --out docs/hud.gif
```

That is the animation at the top of this page; `docs/demo-3x5.json` is the default and renders the
3x5 sample instead. Both show the script's shape, which is documented above `demoFrame` in
`hud/hud.js`. It needs a Chromium-family browser and cannot run inside a sandbox that denies unix
sockets. Headless `--screenshot` is uneven across browsers — Brave exits without writing a frame,
Edge writes one and keeps running — so the script waits for each file and stops the browser itself.

## Configuration

`~/.config/zmk-layer-hud/config.yaml` (or `--config` / `ZMKHUD_CONFIG`). Paths may be relative to
the file. Only `keymap:` is required; [config/diamond.yaml](config/diamond.yaml) shows every key
with its default and a comment:

| key | what |
|---|---|
| `keymap`, `drawer_config` | the keymap-drawer YAML, and your drawer config (key sizes, glyphs) |
| `layers` | which drawer layer shows which ZMK layer, when the YAML is curated (`map`) |
| `positions` | ZMK position of each drawer key when the YAML's key order is not the keymap's |
| `combo_term_ms` | the keymap's combo timeout, so simultaneous presses form a combo |
| `combos` | layer coverage for a combo the import gets wrong |
| `keyboard`, `signal` | pick one of several ZMK boards; non-default announcement usages |
| `title` | corner text (default: the name of the keyboard that is typing) |
| `hud`, `feed` | every size and timing: panel width and opacity, flash and pill durations, combo slack, dead-key window … |
| `extras` | inference hints, used only with firmware that reports no positions |

For a YAML produced by `keymap parse`, layer order and key order already match the keymap and
none of the mapping keys are needed.

### Taking it from your keyboard's repo

A keymap-drawer file does not carry three things the HUD needs: the id of each ZMK layer, the key
position of each drawn key, and which layers a combo really fires on — a combo's `layers:` there is
a drawing choice, drawn once on the diagram that explains it, where the HUD needs the firmware's
gate. `import` takes them out of the keyboard's own ZMK keymap, once:

```bash
./zmk-layer-hud import github.com/you/keyboards          # or a path to a working copy
./zmk-layer-hud import ~/projects/keyboards --keyboard diamond
./zmk-layer-hud sync                                     # read it again, and say what changed
```

What it derives goes in a file named after the config (`config.yaml` → `config.imported.yaml`), so
the config stays yours: anything set there wins, and a sync never touches it. The one thing import
cannot know is which drawn layer shows which ZMK layer — your names, not the keymap's — so it
drafts that mapping and marks the lines it had to leave undecided. Correct them once in
`config.yaml`; sync will not overwrite them.

A URL is cloned into `~/.cache/zmk-layer-hud` (`$ZMKHUD_CACHE` moves it) and fetched on every
later sync, so a sync sees what you pushed; a path is read where it is, so it sees what you have
not pushed yet. [config/diamond.imported.yaml](config/diamond.imported.yaml) is what it writes for the Diamond.

## How it works

**The channel.** The HID Usage Tables reserve keyboard-page usages 0xA5–0xDF; no OS maps them
to a key (macOS emits no event, Linux only `KEY_UNKNOWN`), so a report carrying them reaches
raw-HID readers and nothing else, over USB and BLE alike. The firmware module writes them straight
into the keyboard report (behaviours that watch key presses never notice) on every layer change:
one usage per active layer plus a commit usage that marks the report as a complete set. With
`positions;` each key press and release also carries its physical position as a usage pair whose
order tells press from release. Details and limits in [docs/zmk-setup.md](docs/zmk-setup.md).

**The host.** `host/hudfeed.py` reads the raw reports with hidapi, decodes layers, positions,
keys and modifiers (US layout, dead keys composed), converts the keymap-drawer YAML with the
drawer's own layout generators and glyphs, and re-sends it when the file changes. The macOS panel
(`host/macos/panel.py`, PyObjC) runs it in-process; the Linux panel talks to it over a WebSocket.

**The page** (`hud/`) draws the physical layout, lights the exact key for each position while it
is held, groups positions pressed within the combo term into the combo the drawer defines, keeps
a one-shot layer on screen through its key's flash, and shows typed characters in a strip below.

## Troubleshooting

- **`cannot open <keyboard>`** (macOS, `run/hudfeed.log`): grant Input Monitoring to the app you
  launch from, and untick the keyboard under Karabiner-Elements → Devices ("modify events"
  seizes it). `host/hiddiag.py` prints the raw IOKit code. Linux: hidraw permissions (udev rule).
- **No `layers` lines**: the firmware isn't announcing. `hudfeed.py --raw` shows the reports:
  9-byte reports mean the report size was not raised; no `df` byte means the node is missing.
- **Wrong keys light** on a curated keymap: `positions:` is missing or wrong; the feed logs
  `key position N is not in the keymap's … drawer keys`.
- **A key stays lit ~5 s**: the firmware reports presses but not releases (rebuild with the
  current module).
- `ZMKHUD_DEBUG=1 host/macos/start.sh` logs every layer and position message with timestamps.

## Development

```bash
make test        # firmware wire policy (C) + host decoder and keymap conversion (Python) + the page (node)
make test-hud    # just the page; KEYMAP=hud/keymap.json runs it against your own board
make keymap      # check the configured keymap-drawer YAML converts
make fixture     # rebuild the committed test keymap from the configured one
```

`firmware/src/layer_signal_policy.h` is the single definition of the wire format; the C and
Python tests share its vectors.

`make test-hud` sweeps the page: every key on every layer it can be shown on, every combo on every
layer it is declared on and in four press orders, every press that must draw no combo, and the
typed-keys strip against what a keyboard would have sent to type each legend. Over 5000 checks in
about a third of a second. The cases are generated from the keymap message, so pointing it at
another board sweeps that board.

`hud/tests/dom.js` is a browser small enough to read — the DOM the page touches and a clock the
test drives by hand — so `hud/hud.js` and `hud/keys.js` run under node exactly as they ship, with
no npm and nothing to build. A DOM that small can also be wrong, so the same cases run in the real
page: serve `hud/`, open `index.html?keymap=tests/fixtures/diamond.json`, and

```js
await import("./tests/browser.js"); await hudBrowserSweep("tests/fixtures/diamond.json")
```

prints the same count and the same failure digest that `node hud/tests/hud_test.js --signature`
does. A case the two disagree about is a hole in the shim.

## Limits

- Layer ids below 31, key positions below 136.
- Without keymap-drawer installed, only `cols_thumbs_notation` and `ortho_layout` layouts render
  and combos given as `trigger_keys` are skipped.
- The Linux panel is ported from an earlier kit and not yet run on hardware.
