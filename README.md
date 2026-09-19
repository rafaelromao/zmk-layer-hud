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

- **One source**: the keyboard, on a channel of its own. No OS event tap, no daemon, no per-app plugin.
- **Any ZMK keyboard**: a small ZMK module on the keyboard, a keymap-drawer YAML on the host.
- **Live**: edit the YAML and the HUD redraws; every size and timing lives in one config file.
- macOS (native overlay panel) and Linux (Hyprland layer-shell panel).

## Quick start

**Keyboard.** Add the module to your ZMK config and one node to your keymap, build with the
`layer-hud-usb-uart` snippet (`west build … -S layer-hud-usb-uart`), flash. Step by step in
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

**Host.** One line, and nothing to clone:

```bash
curl -fsSL https://raw.githubusercontent.com/rafaelromao/zmk-layer-hud/main/install.sh | sh
```

That puts the tree in `~/.local/share/zmk-layer-hud` and the command in `~/.local/bin`, then hands
over to `zmk-layer-hud setup` for the machine itself: Homebrew's hidapi on macOS, the GTK and
layer-shell packages and the udev rule on Linux, the virtualenv, and a config to start from.
Nothing runs as root without printing the command and asking first, so a piped `curl` never
quietly acquires it.

Then point the one required line of the config, `keymap:`, at your keymap-drawer YAML, check it
converts, and start:

```bash
zmk-layer-hud config edit
zmk-layer-hud keymap
zmk-layer-hud start
```

The panel opens on the screen with keyboard focus (drag it anywhere; it remembers). Within two
seconds the status line disappears and the banner follows your keyboard. `zmk-layer-hud stop`
closes it and `zmk-layer-hud log` tails the logs — and when it does not come up,
**`zmk-layer-hud doctor`** checks the interpreter, the packages, the config, the permissions and
the two keyboard channels, and names what is missing.

On Linux the HUD is an overlay: it floats over whatever is on screen and takes no room from it.
`zmk-layer-hud start --reserve` instead gives it an exclusive zone on the right, so the compositor
tiles windows beside the HUD rather than under it. That is for recording — where an editor must
never end up behind the board — and it rearranges every window on that output, which is more than
a HUD should do merely because it was started.

### Try it without a keyboard

`examples/` holds two keymaps from keymap-drawer's own examples with ready configs: a 3x5+3
split (`config/example-3x5.yaml`) and a 4x12 ortho board (`config/example-4x12.yaml`).

```bash
zmk-layer-hud demo                                      # the 3x5 sample
zmk-layer-hud demo --config config/example-4x12.yaml    # the ortho board
```

That converts the keymap, serves the pages and opens them. In the browser console,
`hud.setLayers([1])` switches layers, `hud.pressAt(13)` lights a key and `hud.releaseAt(13)` lets
it go, so the whole page can be exercised without hardware.

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

## Commands

Everything is a verb on `zmk-layer-hud`; `zmk-layer-hud <command> --help` explains any one of them.

| | |
|---|---|
| `start [--reserve]` | start the HUD. `--reserve` (Linux) tiles windows beside it rather than under it |
| `stop`, `restart` | stop it; stop and start again |
| `status` | is it running, and which of the keyboard's two channels is live |
| `log [-n N] [--no-follow]` | follow the panel and feed logs |
| `doctor` | check this machine and say what is missing |
| `setup` | prepare this machine: packages, virtualenv, config, permissions |
| `update`, `uninstall` | fetch a newer tree; remove the tree and the command |
| `keymap [--dump]` | check the configured keymap-drawer YAML converts |
| `import <repo>`, `sync` | take layer ids, key positions and combo layers from a ZMK repo |
| `config path\|show\|edit\|link` | where the config is, what is in it, and linking one kept in a repo |
| `demo` | serve the pages against a sample keymap, with no keyboard |
| `poke`, `feed` | drive the HUD without a keyboard; run the feed alone |
| `version` | what this is and where it lives |

`setup` prints every privileged step and asks before running it, and `--no-sudo` prints them
without running any. From a clone, `bin/zmk-layer-hud setup --link` puts the command on your PATH
pointing at that tree, so a developer runs exactly what everyone else does.

Two flags on `feed` read alike and are not: `--no-keys` sends layers only, while `--no-hid-keys`
drops just the HID half — the typed-keys strip and the shift flag — and keeps positions.

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
| `keyboard`, `serial`, `ble` | pick one of several ZMK boards; name its serial port; its BLE address |
| `title` | corner text (default: the name of the keyboard that is typing) |
| `hud`, `feed` | every size and timing: panel width and opacity, flash and pill durations, combo slack, dead-key window … |
| `extras` | inference hints, used only with firmware that reports no positions |

For a YAML produced by `keymap parse`, layer order and key order already match the keymap and
none of the mapping keys are needed.

Where things live, and what moves them:

| | default | |
|---|---|---|
| `ZMKHUD_CONFIG` | `~/.config/zmk-layer-hud/config.yaml` | the config to read |
| `ZMKHUD_STATE` | `~/.local/state/zmk-layer-hud` | where `panel.log` and `hudfeed.log` go |
| `ZMKHUD_HOME` | `~/.local/share/zmk-layer-hud` | where the installer puts the tree |
| `ZMKHUD_PYTHON` | the tree's `.venv/bin/python3` | the interpreter the feed runs under |
| `ZMKHUD_PORT` | `8766` | the feed's WebSocket port |
| `ZMKHUD_CACHE` | `~/.cache/zmk-layer-hud/repos` | where `import` keeps a repo given by URL |
| `ZMKHUD_REF` | `main` | the branch `install.sh` and `update` fetch |
| `ZMKHUD_RESERVE` | `0` | what `start --reserve` sets |
| `ZMKHUD_DEBUG` | unset | the macOS panel logs every layer and position message |

### Keeping the config in this repo

`zmk-layer-hud setup` copies `config/example.yaml` to `~/.config` once and then leaves your config
alone, which is what you want for a config you edit in place. If instead you keep your config in a
repo — as [config/diamond.yaml](config/diamond.yaml) is kept here — link it rather than copying it,
so `git pull` is the whole of syncing a second machine:

```sh
zmk-layer-hud config link config/diamond.yaml
```

Both names are linked, and that is not a convenience: `<config>.imported.yaml` is looked for beside
the config's own path, not beside whatever that path points at, so linking only `config.yaml` would
leave a stale imported file in play. The pair also has to travel together — a config and an import
that disagree about `combo_term_ms` can cancel out while they are in step and silently fall back to
the 50 ms default the moment one of them moves.

### Taking it from your keyboard's repo

A keymap-drawer file does not carry three things the HUD needs: the id of each ZMK layer, the key
position of each drawn key, and which layers a combo really fires on — a combo's `layers:` there is
a drawing choice, drawn once on the diagram that explains it, where the HUD needs the firmware's
gate. `import` takes them out of the keyboard's own ZMK keymap, once:

```bash
zmk-layer-hud import github.com/you/keyboards          # or a path to a working copy
zmk-layer-hud import ~/projects/keyboards --keyboard diamond
zmk-layer-hud sync                                     # read it again, and say what changed
```

What it derives goes in a file named after the config (`config.yaml` → `config.imported.yaml`), so
the config stays yours: anything set there wins, and a sync never touches it. The one thing import
cannot know is which drawn layer shows which ZMK layer — your names, not the keymap's — so it
drafts that mapping and marks the lines it had to leave undecided. Correct them once in
`config.yaml`; sync will not overwrite them.

A URL is cloned into `~/.cache/zmk-layer-hud` (`$ZMKHUD_CACHE` moves it) and fetched on every
later sync, so a sync sees what you pushed; a path is read where it is, so it sees what you have
not pushed yet. [config/diamond.imported.yaml](config/diamond.imported.yaml) is what it writes for the Diamond.

## Three ways in

Everything on screen comes from one stream of messages, and three things can produce it: the
keyboard, the command line, and a WebSocket client. They are interchangeable by design — the page
cannot tell them apart — which is what makes the HUD testable without hardware, and also what to
keep in mind when reading a HUD that looks right.

### 1. The keyboard

The real one, and the only one that proves anything. It speaks on two channels at once, with
separate permissions, which is why half the HUD can work while the other half does not:

| channel | carries | needs |
|---|---|---|
| the module's own (CDC-ACM over USB, GATT over BLE) | active layers, and each key press/release by position | the tty: `uaccess` from the udev rule, or the `dialout`/`uucp` group |
| the keyboard's HID reports | what you type, and the modifier flags | macOS: Input Monitoring. Linux: read on `/dev/hidrawN`, from the same udev rule |

Lose the first and the board stops following you; lose the second and the typed-keys strip stays
empty and shift stops capitalising the legends, while everything else still works.
`zmk-layer-hud status` says which of the two is live, and `zmk-layer-hud doctor` says why when one
is not. `--no-hid-keys` drops the second channel deliberately, and the permission with it.

### 2. The command line

`zmk-layer-hud` runs the HUD — `start`, `stop`, `status`, `log` and the rest are in
[Commands](#commands) above.

`zmk-layer-hud poke` drives it, by sending the feed the messages a keyboard would have produced:

```bash
zmk-layer-hud poke --type "hello, world"   # type it, character by character
zmk-layer-hud poke --legend 'á'            # one legend, composed as the decoder would
zmk-layer-hud poke --layers 2,22           # set the active layer ids ('' clears)
zmk-layer-hud poke --press 13              # light the key at ZMK position 13, then release it
zmk-layer-hud poke --stdin < script.jsonl  # raw messages, one JSON per line
```

`--gap-ms` and `--hold-ms` set the timing, `-v` echoes what it sends, `--url` points it elsewhere.
Characters go through `host/uskeys.py`, so an accent arrives as the one composed keyDown the real
decoder would have emitted.

This exercises the page — layout, legends, strip, combo grouping — and nothing below it. The
keyboard, the firmware and the wire format are never involved, so **a HUD that looks right under
hudpoke can still be fed wrong by a real keyboard**, and it is worth being clear which of the two
you have just shown.

### 3. The WebSocket

`zmk-layer-hud feed` serves `ws://127.0.0.1:8766` (`--port`, or `ZMKHUD_PORT`; `--no-ws` turns it
off). This is how the Linux panel is fed, and it is what `poke` talks to. Note that the macOS
panel runs the feed in-process and opens **no socket at all** — to drive that one, use the page's
own API in a browser, or run `zmk-layer-hud feed` yourself and point a browser page at it with
`index.html?ws=ws://127.0.0.1:8766`.

Outbound, every message is one JSON object per frame. A message produced by a keyboard also carries
`"device"`, its name, so a page can show which keyboard is typing:

| message | when |
|---|---|
| `{"kind":"keymap", …}` | on connect, and whenever the config, the keymap YAML or the imported file changes |
| `{"kind":"layers","ids":[2,22]}` | the active ZMK layer ids changed (layer 0 is omitted: always active) |
| `{"kind":"device","name":"Diamond"}` | a keyboard was opened |
| `{"kind":"press","pos":13}` / `{"kind":"release","pos":13}` | a key at that ZMK position went down / up |
| `{"kind":"key","type":"keyDown","name":"a","chars":"a","code":4,"flags":{"cmd":false,"ctrl":false,"alt":false,"shift":false,"fn":false},"repeat":false}` | a key or modifier change, decoded from the reports |

`type` is `keyDown`, `keyUp` or `flagsChanged`. The last `keymap`, `layers` and `device` are cached
and replayed to every new client, so a page that connects late — or reconnects — is right
immediately rather than blank until you touch something.

Inbound, a client may send `layers`, `press`, `release`, `key` and `device`, and each is fanned out
to every client exactly as a keyboard's would be. That is the whole of how `hudpoke` works, and you
can do the same from any client:

```bash
python3 -c 'import asyncio,websockets,json
async def main():
    async with websockets.connect("ws://127.0.0.1:8766") as ws:
        await ws.send(json.dumps({"kind":"layers","ids":[2]}))
asyncio.run(main())'
```

After an injected message the feed re-asserts the keyboard's own layer state, so the picture
returns to the truth by itself within one heartbeat rather than staying wherever you left it.
`{"kind":"close"}` from any client exits the feed and takes the HUD down with it — that is what the
page's ✕ sends. `--no-inject` refuses the five cosmetic kinds; it does not disable `close`, which
the socket accepted long before they did. The keymap is deliberately not injectable: it is large,
it is built from files the feed already watches, and a page given a wrong one has no way back.

## How it works

**The channel.** The firmware module has its own: a CDC-ACM serial interface over USB, GATT
notifications over BLE. On it go small framed messages — the active layers as a bitmap, and with
`positions;` each key press and release by its physical position. Nothing on it can be mistaken for
a key. What you type does not travel here — the host reads that from the keyboard's HID reports,
because ZMK emits one per change and so none can be lost, where a snapshot sent from the firmware
has to be deferred to a work queue and coalesces presses away.

It used to ride inside the keyboard report itself, as the keyboard-page usages 0xA5–0xDF that the
HID Usage Tables reserve, on the premise that no OS maps them. Linux does: every unmapped slot in
the kernel's `hid_keyboard[]` table holds `KEY_UNKNOWN`, not nothing, so each layer change arrived
as a phantom key press carrying whatever modifiers were held — enough, with Gui down, to make a
Wayland compositor change workspace. Details and limits in [docs/zmk-setup.md](docs/zmk-setup.md).

**The host.** `host/hudfeed.py` reads that channel with pyserial (or bleak over BLE) for layers
and positions, and the keyboard's HID reports for what you type — with hidapi on macOS, and from
`/dev/hidrawN` directly on Linux, where hidapi's wheel bundles the libusb backend and cannot open
the node with the access the udev rule grants. Two sources, because
they fail differently: ZMK emits one report per change, so reading them cannot lose a keystroke,
while the firmware sending the same snapshot has to defer it to a work queue and coalesces presses
away. Reading the reports is what needs Input Monitoring on macOS; the signal channel needs nothing.
`--no-hid-keys` drops the strip and the permission with it. The rest — decoding keys and modifiers
(US layout, dead keys composed), converting the keymap-drawer YAML with the drawer's own layout
generators and glyphs, re-sending it when the file changes — is unchanged. The macOS panel
(`host/macos/panel.py`, PyObjC) runs it in-process; the Linux panel talks to it over a WebSocket.

**The page** (`hud/`) draws the physical layout, lights the exact key for each position while it
is held, groups positions pressed within the combo term into the combo the drawer defines, keeps
a one-shot layer on screen through its key's flash, and shows typed characters in a strip below.

## Troubleshooting

**`zmk-layer-hud doctor` first.** It checks the interpreter and its version, the virtualenv's
packages, the GTK bindings on Linux, the config and whether its keymap converts, the udev rule,
whether the command is on your PATH, and what the feed last managed to open — and prints the fix
beside anything that is wrong. What it cannot see:

- **`cannot open <keyboard>`** (Linux, `~/.local/state/zmk-layer-hud/hudfeed.log`): tty permissions for the signal channel —
  install the udev rule, or add yourself to `dialout`. The serial port itself needs no permission on
  macOS.
- **`cannot read what is typed on <keyboard>`**: that is the HID half, and it does need one. On
  macOS grant Input Monitoring to whatever runs the feed (your terminal, or Hammerspoon), and untick
  the keyboard under Karabiner-Elements → Devices, which seizes a keyboard whose events it modifies.
  On Linux it is hidraw access, from the same udev rule. Layers and positions keep working without
  it; only the typed-keys strip goes quiet.
- **`… is not the layer signal`**: that port answered nothing for eight seconds. The board exposes
  more than one CDC interface and this was another; the feed moves on to the next by itself. If it
  says so about every port, the firmware is not sending — build it with the snippet
  (`-S layer-hud-usb-uart`), which is what creates the interface and points `zmk,layer-hud-uart` at it.
- **No `layers` lines**: `zmk-layer-hud feed --raw` logs every frame it decodes, so silence there separates
  "the keyboard says nothing" from "the host makes nothing of it".
- **Wrong keys light** on a curated keymap: `positions:` is missing or wrong; the feed logs
  `key position N is not in the keymap's … drawer keys`.
- **A key stays lit ~5 s**: the firmware reports presses but not releases (rebuild with the
  current module).
- `ZMKHUD_DEBUG=1 zmk-layer-hud start` logs every layer and position message with timestamps.

## Development

`make test` runs every suite: the firmware's wire policy in C, the host decoder and keymap
conversion in Python, and the page under node. [docs/development.md](docs/development.md) covers
working from a clone, what each suite sweeps, and how the command line is put together.

## Limits

- Layer ids below 31, key positions below 136.
- Without keymap-drawer installed, only `cols_thumbs_notation` and `ortho_layout` layouts render
  and combos given as `trigger_keys` are skipped.
- The macOS panel runs the feed in-process and serves no WebSocket, so `hudpoke` cannot reach it;
  drive that one from the page's own API, or run `zmk-layer-hud feed` separately.
