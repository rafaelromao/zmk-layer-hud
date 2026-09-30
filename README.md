# zmk-layer-hud

An on-screen HUD for ZMK keyboards. It shows the layer you are on and lights the keys, combos
and macros as you press them, drawn from the same
[keymap-drawer](https://github.com/caksoylar/keymap-drawer) file you document your layout with.
The keyboard itself reports its layers and key positions, so nothing is guessed. Keep it on
screen while you learn a layout, or while you record or share your screen.

![The HUD following a keyboard through its vim layers: typing on the base layer, a combo into vim
mode, NORMAL with h j k l lit one at a time, v into VISUAL to select a word, yank and put it back,
then i into INSERT and Esc out](docs/hud.gif)

*A [Diamond](https://github.com/rafaelromao/keyboards) running
[zmk-vim-mode](https://github.com/rafaelromao/zmk-vim-mode), whose daemon moves the keyboard
between the vim layers. Rendered by `docs/make-gif.sh` from `docs/demo-vim.json`.*

- **One source**: the keyboard, on a channel of its own. No OS event tap, no service to install,
  no per-app plugin.
- **Any ZMK keyboard**: a small ZMK module on the keyboard, a keymap-drawer YAML on the host.
- **Live**: edit the YAML and the HUD redraws; every size and timing lives in one config file.
- **Native panels**: a macOS overlay panel, a Hyprland layer-shell panel on Linux.

## Requirements

- macOS, or Linux running Hyprland (the panel is a layer-shell surface).
- Python 3.10 or newer. `setup` builds a virtualenv; on macOS, Apple's `/usr/bin/python3` is 3.9
  and is not enough on its own.
- A ZMK keyboard whose firmware you can build and flash, and a keymap-drawer YAML of its keymap
  (the [quick start](#quick-start) shows how to make one).
- On Linux, the system GTK bindings for the panel — `python-gobject`, `webkit2gtk-4.1` and
  `gtk-layer-shell`, which `setup` installs.

## Quick start

**Keyboard.** Add the module to your zmk-config's `config/west.yml`, and this node to your
keymap:

```c
/ {
    layer_signal {
        compatible = "zmk,layer-signal";
        heartbeat-ms = <2000>;   // re-send the layers every 2 s, so a HUD started late catches up
        positions;               // also report every key press by position, to light the exact key
    };
};
```

Over USB the signal needs a serial interface of its own, which the module's snippet adds:
`-S layer-hud-usb-uart` on a local `west build`, or `snippet: layer-hud-usb-uart` on the board's
entry in `build.yaml` when GitHub Actions builds it. Over Bluetooth it needs nothing more. Build and
flash the central half, or the dongle; peripherals need nothing.
[docs/zmk-setup.md](docs/zmk-setup.md) has every step, the `west.yml` lines included.

**Host.** One line:

```bash
curl -fsSL https://raw.githubusercontent.com/rafaelromao/zmk-layer-hud/main/install.sh | sh
```

That puts the tree in `~/.local/share/zmk-layer-hud` and hands over to `zmk-layer-hud setup`,
which prepares the machine itself: Homebrew's hidapi on macOS, the GTK and layer-shell packages
and the udev rule on Linux, the virtualenv, a config to start from, and the command in
`~/.local/bin`. Nothing runs as root without printing the command and asking first, so a piped
`curl` never quietly acquires it.

No keymap-drawer YAML yet? keymap-drawer makes one from your keymap, and `setup` has already
installed it in the tree's virtualenv:

```bash
~/.local/share/zmk-layer-hud/.venv/bin/keymap parse -z path/to/your.keymap > keymap.yaml
```

A YAML made that way needs nothing else in the config: its layer and key order are the keymap's.

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
tiles windows beside the HUD rather than under it. That is meant for recording, where an editor
must never end up behind the board; it rearranges every window on that output.

### Try it without a keyboard

The tree holds two keymaps from keymap-drawer's own examples, with ready configs: a 3x5+3 split
and a 4x12 ortho board.

```bash
zmk-layer-hud demo                                                                  # the 3x5 sample
zmk-layer-hud demo --config ~/.local/share/zmk-layer-hud/config/example-4x12.yaml   # the ortho board
```

(From a clone, the second is `--config config/example-4x12.yaml`.) That converts the keymap,
serves the pages and opens them, fed by a socket of their own the way the Linux panel's are. In
the browser console, `hud.setLayers([1])` switches layers, `hud.pressAt(13)` lights a key and
`hud.releaseAt(13)` lets it go; `zmk-layer-hud poke --url ws://127.0.0.1:8767 --type hello` types
on it from a terminal.

**A demo that types.** A script says what the keyboard does, and the demo plays it in real time:

```bash
zmk-layer-hud demo --play ~/.local/share/zmk-layer-hud/docs/demo-type.json --loop
```

Text in a script is typed through the keymap, every character on its key or combo and the layer
it lives on, and the board, the bar and the heatmap follow it as they would the keyboard.
`zmk-layer-hud poke --play FILE` plays one into a running HUD's feed.
[docs/demo-scripts.md](docs/demo-scripts.md) has the format.

**A GIF of your own.** `docs/make-gif.sh` renders the same scripts frame by frame in a headless
Chromium-family browser and assembles them with `ffmpeg`. With no options it renders the 3x5
sample:

```bash
bash ~/.local/share/zmk-layer-hud/docs/make-gif.sh --out demo.gif
bash ~/.local/share/zmk-layer-hud/docs/make-gif.sh --live --script docs/demo-type.json --out typing.gif
```

A frame is a still by default, one per step. With `--live` it is a moment: the script played on a
clock of the page's own and stopped every 1/8 s (`--fps`), so the GIF shows the glow fading, the
pills coming and going and the speed on the bar. The animation at the top of this page is `--config
config/diamond.yaml --script docs/demo-vim.json`. In the page's URL, `&demo=N` renders still N
alone (`&script=<url>`, or `demo.json` beside the page), and `&timeline=<url>&at=T` the moment T
ms in (`host/play.py --capture`). The browser needs unix sockets, so this cannot run inside a
sandbox that denies them.

### Updating and uninstalling

`zmk-layer-hud update` fetches a newer tree and keeps your config; a clone updates with `git pull`
instead. `zmk-layer-hud uninstall` removes the tree and the command and leaves your config in
`~/.config/zmk-layer-hud`, which `--purge` removes too, with the logs and the import cache. On
Linux the udev rule stays until you remove it:
`sudo rm /etc/udev/rules.d/60-zmk-layer-hud.rules`.

## Commands

Everything is a verb on `zmk-layer-hud`; `zmk-layer-hud <command> --help` lists the flags of any
one of them.

| Command | What it does |
|---|---|
| `start`, `stop`, `restart` | start the HUD; stop it; stop and start again |
| `status` | is it running, and which of the keyboard's two channels is live |
| `log` | follow the panel and feed logs |
| `session [list\|new\|save\|load\|reset\|delete]` | the typing sessions: the active one, naming it, starting or loading another |
| `heatmap [live\|session\|off]` | what the keys glow with: what was just typed, every press this session, or nothing |
| `doctor` | check this machine and say what is missing |
| `setup` | prepare this machine: packages, virtualenv, config, permissions |
| `update`, `uninstall` | fetch a newer tree; remove the tree and the command |
| `keymap` | check the configured keymap-drawer YAML converts |
| `import <repo>`, `sync` | take layer ids, key positions and combo layers from a ZMK repo |
| `config path\|show\|edit\|link` | where the config is, what is in it, and linking one kept in a repo |
| `demo [--play SCRIPT]` | serve the pages against a sample keymap, with no keyboard; type a demo script on them |
| `poke`, `feed` | drive the HUD without a keyboard; run the feed alone |
| `version` | what this is and where it lives |

`start --reserve` (Linux) tiles windows beside the HUD rather than under it. `setup` prints every
privileged step and asks before running it, and `setup --no-sudo` prints them without running any.

Two flags on `feed` read alike and are not: `--no-keys` sends layers only, while `--no-hid-keys`
drops just the HID half — the typed-keys strip and the shift flag — and keeps positions.

## Configuration

`~/.config/zmk-layer-hud/config.yaml` (or `--config` / `ZMKHUD_CONFIG`). Paths may be relative to
the file. Only `keymap:` is required. [config/example.yaml](config/example.yaml) is the config
`setup` starts you with; [config/diamond.yaml](config/diamond.yaml), the author's own, shows every
key with its default and a comment:

| key | what |
|---|---|
| `keymap`, `drawer_config` | the keymap-drawer YAML, and your drawer config (key sizes, glyphs) |
| `layers` | which drawer layer shows which ZMK layer, when the YAML is curated (`map`) |
| `base` | the drawer layer that is always active (default: the layer for id 0) |
| `positions` | ZMK position of each drawer key when the YAML's key order is not the keymap's |
| `combo_term_ms` | the keymap's combo timeout, so simultaneous presses form a combo |
| `combo_idle_ms` | the keymap's `require-prior-idle-ms` for combos: a chord struck sooner after another key is drawn as its keys, as ZMK types it |
| `combos` | layer coverage for a combo the import gets wrong |
| `keyboard`, `serial`, `ble` | pick one of several ZMK boards; name its serial port; its BLE address |
| `title` | corner text (default: the name of the keyboard that is typing) |
| `stagger` | a 3x5 split whose `layout` only counts its keys is drawn with a Ferris Sweep's column stagger; `false` keeps it ortholinear |
| `hud`, `feed` | every size and timing: panel width and opacity, flash and pill durations, combo slack, how long a key's glow takes to fade, dead-key window … |
| `extras` | inference hints, used only with firmware that reports no positions |

For a YAML produced by `keymap parse`, layer order and key order already match the keymap and
none of the mapping keys are needed.

Where things live, and what moves them:

| | default | |
|---|---|---|
| `ZMKHUD_CONFIG` | `~/.config/zmk-layer-hud/config.yaml` | the config to read |
| `ZMKHUD_STATE` | `~/.local/state/zmk-layer-hud` | where `panel.log`, `hudfeed.log` and the `sessions/` go |
| `ZMKHUD_ROOT` | the installed tree | the tree the command runs from |
| `ZMKHUD_HOME` | `~/.local/share/zmk-layer-hud` | where `install.sh` puts the tree |
| `ZMKHUD_BIN_DIR` | `~/.local/bin` | where `setup` puts the command |
| `ZMKHUD_PYTHON` | the tree's `.venv/bin/python3` | the interpreter the feed runs under |
| `ZMKHUD_PORT` | `8766` | the feed's WebSocket port |
| `ZMKHUD_CACHE` | `~/.cache/zmk-layer-hud/repos` | where `import` keeps a repo given by URL |
| `ZMKHUD_REF` | `main` | the branch `install.sh` and `update` fetch |
| `ZMKHUD_RESERVE` | `0` | what `start --reserve` sets |
| `ZMKHUD_DEBUG` | unset | the macOS panel logs every layer and position message |

### Sessions

What the keyboard types is counted into a session, and there is always one, the active one. A
new session is named after when it began, and typing adds to it until another takes its place:

```bash
zmk-layer-hud session                 # the active one: keys, combos, typing time, speed
zmk-layer-hud session save colemak-1  # name it
zmk-layer-hud session new             # start another; the one before stays saved
zmk-layer-hud session list
zmk-layer-hud session load colemak-1  # make it the active one again: typing adds to it
zmk-layer-hud heatmap session         # the keys glow with how often each one was pressed
```

`save` on a session that already has a name keeps it as it is and goes on in a copy under the
new one. `reset` zeroes the active session and `delete` removes one that is not active; both ask
first (`--yes` does not). The commands work whether the HUD is running or not, and it follows
what they do within a second.

A session is kept in `$ZMKHUD_STATE/sessions/<name>.json` (the directory 0700, each file 0600),
and it holds counts: how often each key was pressed on each layer, each combo, how many characters
were typed and deleted, the time spent typing and the best speed. Never what was typed, in what
order, or when. Nothing leaves the machine. Only the keyboard's own typing is counted: `poke` and
the demo light the board and time their typing, and a session never sees them. `feed.sessions: 0`
in the config (or `feed --no-sessions`) keeps no files, and `uninstall --purge` removes them.

**Passwords.** On macOS, while a password field has focus (or a terminal's Secure Keyboard Entry
is on), the system turns on secure input, and the feed then passes on nothing typed: no
characters, no keys lit, nothing counted. The strip clears, the board dims and the banner says
`secure input · typing hidden` until it is over. The HUD reads the keyboard itself, where that
switch does not reach, so it holds back on its own. `feed.secure_input: 0` turns this off. Linux
has no such switch; see [Limits](#limits).

### Keeping the config in a repo

`zmk-layer-hud setup` copies `config/example.yaml` to `~/.config` once and then leaves your config
alone, which is what you want for a config you edit in place. If instead you keep your config in a
repo — your zmk-config, say, the way [config/diamond.yaml](config/diamond.yaml) is kept in this
one — link it rather than copying it, so `git pull` is the whole of syncing a second machine:

```sh
zmk-layer-hud config link path/to/your/config.yaml
```

Both names are linked, and that matters: `<config>.imported.yaml` is looked for beside the config's
own path, not beside whatever that path points at, so linking only `config.yaml` would leave a
stale imported file in play. The pair also has to travel together — a config and an import that
disagree about `combo_term_ms` silently fall back to the 50 ms default.

### Taking it from your keyboard's repo

A keymap-drawer file does not carry three things the HUD needs: the id of each ZMK layer, the key
position of each drawn key, and which layers a combo really fires on — a combo's `layers:` there is
a drawing choice, where the HUD needs the firmware's gate. `import` takes them out of the
keyboard's own ZMK keymap, once:

```bash
zmk-layer-hud import github.com/you/zmk-config         # or a path to a working copy
zmk-layer-hud import ~/zmk-config --keyboard corne     # --keyboard: which one, when it holds several
zmk-layer-hud sync                                     # read it again, and say what changed
```

What it derives goes in a file named after the config (`config.yaml` → `config.imported.yaml`), so
the config stays yours: anything set there wins, and a sync never touches it. The one thing import
cannot know is which drawn layer shows which ZMK layer — your names, not the keymap's — so it
drafts that mapping and marks the lines it had to leave undecided. Correct them once in
`config.yaml`; sync will not overwrite them.

A URL is cloned into `~/.cache/zmk-layer-hud/repos` (`$ZMKHUD_CACHE` moves it) and fetched on every
later sync, so a sync sees what you pushed; a path is read where it is, so it sees what you have
not pushed yet. [config/diamond.imported.yaml](config/diamond.imported.yaml) is what it writes for
the Diamond.

## How it works

**The channel.** The firmware module has its own: a CDC-ACM serial interface over USB, GATT
notifications over BLE. On it go small framed messages — the active layers as a bitmap, and with
`positions;` each key press and release by its physical position. Nothing on it can be mistaken for
a key, which is the point: a signal carried inside the keyboard report reaches the OS as well as
the host, and on Linux arrives as phantom key presses. The reasoning is in
[docs/zmk-setup.md](docs/zmk-setup.md#why-a-channel-of-its-own).

**The host.** `host/hudfeed.py` reads that channel with pyserial (or bleak over BLE) for layers
and positions, and the keyboard's HID reports for what you type — with hidapi on macOS, and from
`/dev/hidrawN` directly on Linux, where hidapi's wheel bundles the libusb backend and cannot open
the node with the access the udev rule grants. Two sources, because they fail differently: ZMK
emits one HID report per change, so reading them cannot lose a keystroke, while the firmware
sending the same snapshot would have to defer it to a work queue that coalesces presses away.
Reading the reports is what needs Input Monitoring on macOS; the signal channel needs nothing, and
`--no-hid-keys` drops the strip and the permission with it. The feed also decodes keys and
modifiers (US layout, dead keys composed) and converts the keymap-drawer YAML with the drawer's own
layout generators and glyphs, re-sending it when the file changes. The macOS panel
(`host/macos/panel.py`, PyObjC) runs the feed in-process; the Linux panel talks to it over a
WebSocket.

**The page** (`hud/`) draws the physical layout, lights the exact key for each position while it
is held, groups positions pressed within the combo term into the combo the drawer defines, keeps
a one-shot layer on screen through its key's flash, and shows typed characters in a strip below.
A key also glows once it is pressed, and cools over `hud.heatmap_ms` (3 s; `0` turns it off): a
live heatmap of what was just typed, where a key struck again and again stays warm longest.

Above the panel, a bar says how the typing goes:
- words per minute, live, over the last `hud.wpm_window_ms` (10 s);
- the session's average over its typing time, not counting pauses longer than `hud.wpm_idle_ms`
  (3 s), and its peak;
- accuracy, the share of typed characters that were not deleted;
- keystrokes, and the share of them made as combos;
- the share of typing done on the layer on screen.

Its last chip names the [session](#sessions) and switches the heatmap between `live`, `session`
(how often each key has been pressed, on the layer on screen) and `off`. Every keystroke the
board draws is counted once, when nothing can take it back: a combo counts as one keystroke, and
a report that beats its own position is not counted twice. `hud.stats_bar: 0` hides the bar.

### Where the messages come from

Three things can produce the stream the page draws, and the page treats them alike: the keyboard,
`zmk-layer-hud poke`, and any WebSocket client. Only the keyboard proves anything — a HUD that looks
right under `poke` can still be fed wrong by a real keyboard.

The one difference is typing sent in, which says whether combos are how it is typed. By default
they are not: `zmk-layer-hud poke --type zebra` lights, for each letter, a single key that types it
(for a `z` on a second alpha layer, that layer's key), and `--combos` lights the combo instead
wherever one types it (a `z` typed by an `r`+`a` chord). A WebSocket client says the same with
`"combos": true` on a `key` ([docs/protocol.md](docs/protocol.md#typing-sent-in)). The
keyboard's own reports need no such word: they are placed on the layers it says are up.

The keyboard speaks on two channels at once, with separate permissions, which is why half the HUD
can work while the other half does not:

| channel | carries | needs |
|---|---|---|
| the module's own (CDC-ACM over USB, GATT over BLE) | active layers, and each key press/release by position | the tty: `uaccess` from the udev rule, or the `dialout`/`uucp` group |
| the keyboard's HID reports | what you type, and the modifier flags | macOS: Input Monitoring. Linux: read on `/dev/hidrawN`, from the same udev rule |

Lose the first and the board stops following you; lose the second and the typed-keys strip stays
empty and shift stops capitalising the legends, while everything else works.
`zmk-layer-hud status` says which of the two is live, and `zmk-layer-hud doctor` says why when one
is not.

The message format, the WebSocket the feed serves, and what a client may inject are documented in
[docs/protocol.md](docs/protocol.md).

## Troubleshooting

**`zmk-layer-hud doctor` first.** It checks the interpreter and its version, the virtualenv's
packages, the GTK bindings on Linux, the config and whether its keymap converts, the udev rule,
whether the command is on your PATH, and what the feed last managed to open — and prints the fix
beside anything that is wrong. What it cannot see:

- **`cannot open <keyboard>`** (Linux, `~/.local/state/zmk-layer-hud/hudfeed.log`): tty permissions
  for the signal channel — install the udev rule, or add yourself to `dialout`. The serial port
  itself needs no permission on macOS.
- **`cannot read what is typed on <keyboard>`**: that is the HID half, and it does need one. On
  macOS grant Input Monitoring to whatever runs the feed (your terminal, or Hammerspoon), and untick
  the keyboard under Karabiner-Elements → Devices, which seizes a keyboard whose events it modifies.
  On Linux it is hidraw access, from the same udev rule. Layers and positions keep working without
  it; only the typed-keys strip goes quiet.
- **`… is not the layer signal`**: that port answered nothing for eight seconds. The board exposes
  more than one CDC interface and this was another; the feed moves on to the next by itself. If it
  says so about every port, the firmware is not sending — build it with the snippet
  (`-S layer-hud-usb-uart`), which is what creates the interface and points `zmk,layer-hud-uart` at it.
- **No `layers` lines**: `zmk-layer-hud feed --raw` logs every frame it decodes, so silence there
  separates "the keyboard says nothing" from "the host makes nothing of it".
- **Wrong keys light** on a curated keymap: `positions:` is missing or wrong; the feed logs
  `key position N is not in the keymap's … drawer keys`.
- **A key stays lit ~5 s**: the firmware is reporting presses but not releases. Rebuild and
  reflash it.
- `ZMKHUD_DEBUG=1 zmk-layer-hud start` logs every layer and position message with timestamps.

## Limits

- Layer ids 0–31, key positions 0–255.
- Without keymap-drawer installed, only `cols_thumbs_notation` and `ortho_layout` layouts render
  and combos given as `trigger_keys` are skipped.
- Linux has nothing like macOS's secure input, so there the strip shows what is typed into a
  password field too, and the board lights its keys. Stop the HUD, or record with it hidden, when
  that matters.
- The macOS panel runs the feed in-process and serves no WebSocket, so `zmk-layer-hud poke` cannot
  reach it; drive that one from the page's own API, or run `zmk-layer-hud feed` separately. A demo
  script plays on the demo page (`zmk-layer-hud demo --play`) on either host.

## Contributing

From a clone, `make install` sets the machine up and puts `zmk-layer-hud` on your PATH pointing at
that clone; `make test` runs every suite — the firmware's wire policy in C, the host decoder and
keymap conversion in Python, and the page under node.
[docs/development.md](docs/development.md) covers what each suite sweeps and how the command line
is put together.

## License

MIT — see [LICENSE](LICENSE).
