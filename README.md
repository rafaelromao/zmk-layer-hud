# zmk-layer-hud

An on-screen HUD for ZMK keyboards. It shows the layer you are on and lights the keys, combos
and macros as you press them, drawn from your ZMK keymap, or from the
[keymap-drawer](https://github.com/caksoylar/keymap-drawer) file you document your layout with.
The keyboard itself reports its layers and key positions, so nothing is guessed. Keep it on
screen while you learn a layout, or while you record or share your screen.

**Try it in your browser:** [rafaelromao.github.io/zmk-layer-hud](https://rafaelromao.github.io/zmk-layer-hud/)
runs this HUD on three sample boards, and you can type on them.

![The HUD following a keyboard through its vim layers: typing on the base layer, a combo into vim
mode, NORMAL with h j k l lit one at a time, v into VISUAL to select a word, yank and put it back,
then i into INSERT and Esc out](docs/hud.gif)

*A [Diamond](https://github.com/rafaelromao/keyboards) running
[zmk-vim-mode](https://github.com/rafaelromao/zmk-vim-mode), whose daemon moves the keyboard
between the vim layers. Rendered by `docs/make-gif.sh` from `docs/demo-vim.json`.*

- **One source**: the keyboard, on a channel of its own. No OS event tap, no service to install,
  no per-app plugin.
- **Any ZMK keyboard**: a small ZMK module on the keyboard; on the host, `import` draws its keymap.
- **Live**: under `sync --watch`, edit the keymap or its drawing and the HUD redraws; every size
  and timing lives in one config file.
- **Native panels**: a macOS overlay panel, a Hyprland layer-shell panel on Linux.

## Requirements

- macOS, or Linux running Hyprland (the panel is a layer-shell surface).
- Python 3.10 or newer. `setup` builds a virtualenv; on macOS, Apple's `/usr/bin/python3` is 3.9
  and is not enough on its own.
- A ZMK keyboard whose firmware you can build and flash, and its zmk-config: a GitHub URL, or a
  working copy. A keymap-drawer YAML of the keymap is optional.
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

That puts the latest release in `~/.local/share/zmk-layer-hud` (`ZMKHUD_REF=main` before `sh`
installs the main branch instead, and `ZMKHUD_REF=v1.0.0` that release) and hands over to
`zmk-layer-hud setup`,
which prepares the machine itself: Homebrew's hidapi on macOS, the GTK and layer-shell packages
and the udev rule on Linux, the virtualenv, a config to start from, and the command in
`~/.local/bin`. Nothing runs as root without printing the command and asking first, so a piped
`curl` never quietly acquires it.

Then draw your keymap into the HUD's own files, and start:

```bash
zmk-layer-hud import github.com/you/zmk-config   # or a path to a working copy
zmk-layer-hud start
```

`import` reads the keymap: every layer, where each key sits, the combos and the layers they really
fire on. It writes what the HUD draws beside the config, in `config.definitions.json`, and the HUD
reads that and the config and nothing else, so it runs without the keymap, keymap-drawer or the
network. Already keep a keymap-drawer YAML, glyphs and key sizes included? Name it as `keymap:` in
the config first (`zmk-layer-hud config edit`), and import draws that instead. After a change to
the keymap, `zmk-layer-hud sync` draws it again ([more](#taking-it-from-your-keyboards-repo)).

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

(From a clone, the second is `--config config/example-4x12.yaml`.) That loads the sample's
definitions, committed beside its config, serves the pages and opens them, fed by a socket of their own the way the Linux panel's are. In
the browser console, `hud.setLayers([1])` switches layers, `hud.pressAt(13)` lights a key and
`hud.releaseAt(13)` lets it go; `zmk-layer-hud poke --url <the socket demo printed> --type hello`
types on it from a terminal (the demo's socket URL carries its token, like a running HUD's).

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

`zmk-layer-hud update` fetches a newer tree and keeps your config: the newest release, or for a tree
installed from a branch that branch again, and it leaves a tree that is already there alone.
`--ref` names another: `latest`, a release's tag like `v1.0.0`, or a branch. `zmk-layer-hud
version` says which one a tree is. A clone updates with `git pull` instead. `zmk-layer-hud uninstall` removes the tree and the command and leaves your config in
`~/.config/zmk-layer-hud`, which `--purge` removes too, with the logs and the import cache. On
Linux the udev rule stays until you remove it:
`sudo rm /etc/udev/rules.d/60-zmk-layer-hud.rules`.

### Hidden, and at login

The HUD can run off screen and go on counting, so a session and its heatmap keep growing while you
work:
- `zmk-layer-hud hide` takes it off screen and `show` brings it back. `toggle` does whichever
  applies, which suits a keybinding.
- The HUD's own minus button, beside the ✕, hides it too. The ✕ still quits.
- On macOS a keyboard icon sits in the menubar, with a WPM to its left while the HUD runs, shown
  or hidden. It is struck through while the HUD is not running. It is dimmed while the HUD is hidden, and it stays after the HUD quits. A click
  shows or hides the HUD, or starts it. A right-click lists the live WPM, the session's average
  and its top, each with its number now: the one picked is the one beside the icon
  (`zmk-layer-hud menubar wpm current|average|top` does the same). It also offers Quit HUD and
  Remove Icon. `start` brings the icon back, and `zmk-layer-hud menubar disable` keeps it away.
- On Omarchy 4, `zmk-layer-hud menubar enable` puts the same icon in the bar. It installs the
  plugin `rafaelromao.zmk-layer-hud` and lists it first in `bar.layout.right` of
  `~/.config/omarchy/shell.json`, keeping the old file as `shell.json.bak-zmk-layer-hud`. It looks
  and works like the macOS one, its menu's three WPMs included, in the shell's own menu style.
  Resting the pointer on it also opens the HUD's stats column, laid out across in the shell's
  popout: the same tiles the HUD shows (`stats:` in the config picks them). If it does not appear,
  run `omarchy-restart-shell`: the shell caches the plugins it has loaded.

Two global shortcuts do the same from the keyboard, on both systems: **Ctrl+Alt+L** shows or
hides the HUD, and **Ctrl+Alt+Gui+L** (Cmd on macOS, Super on Omarchy) starts or stops it. The
icon's right-click menu shows each one beside its item. To use other keys, or none, set them in
the config: `shortcuts: { toggle: ctrl+alt+l, power: ctrl+alt+gui+l }`, with `null` turning one
off.
- **On macOS** the menubar icon holds them, so they need no permission, but they go away when the
  icon does (`menubar disable`, Remove Icon). A config change takes effect at once.
- **On Omarchy** they are Hyprland binds, written in the language its config is. Omarchy 4's
  `~/.config/hypr/hyprland.lua` gets `~/.config/hypr/zmk-layer-hud.lua`, written by every `start`
  from the config, and the first one adds a line at the end of `hyprland.lua` that reads it (in a
  `pcall`, so a missing file never breaks the rest of the config). A classic `hyprland.conf` gets
  `zmk-layer-hud.conf` and a `source =` line instead. The first edit keeps a copy of the file as it
  was in `<file>.bak-zmk-layer-hud`, and Hyprland reads its config again whenever the binds changed.
  The binds stay after the HUD stops, so the power shortcut starts it again. They unbind those keys
  first, so anything else bound to them stops working. A config change takes effect on the next
  start, and `uninstall` removes both the file and the line.

`zmk-layer-hud autostart enable` starts the HUD hidden at every login, and `autostart disable`
stops doing so; it leaves a running HUD alone.
- **On macOS** it is a login item that runs a small app of its own, "ZMK Layer HUD", in
  `~/Library/Application Support/zmk-layer-hud`. The first time, macOS asks to let that app use
  Input Monitoring and Bluetooth; grant both. The app exists because macOS asks the app a program
  was started from, which is your terminal when you type `start` and would be Python at login.
  A `restart` typed in a terminal hands the running HUD back to the terminal's grants.
- **On Linux** it is `~/.config/autostart/zmk-layer-hud.desktop`, which Omarchy starts through
  uwsm. The HUD's output is in `zmk-layer-hud log`; `journalctl --user` has only a start that
  failed.

## Commands

Everything is a verb on `zmk-layer-hud`; `zmk-layer-hud <command> --help` (or `help`) lists the
flags of any one of them, and [docs/cli.md](docs/cli.md) has every verb's in one place.

| Command | What it does |
|---|---|
| `start`, `stop`, `restart`, `power` | start the HUD; stop it; stop and start again; start it if it is not running, stop it if it is |
| `show`, `hide`, `toggle` | bring the HUD on screen; take it off, still running and counting; whichever it is not |
| `status` | is it running, and which of the keyboard's two channels is live |
| `log` | follow the panel and feed logs |
| `session [list\|new\|save\|load\|reset\|delete\|rename-layer\|export\|history\|compare]` | the typing sessions: the active one, naming it, starting or loading another, drawing its heatmap as an SVG, its days, two side by side |
| `heatmap [live\|session\|physical\|speed\|off]` | what the keys glow with: what was just typed, every press this session on the layer on screen or on all of them, each key's time, or nothing |
| `doctor` | check this machine and say what is missing |
| `autostart [enable\|disable]` | start the HUD hidden at login, counting from the first keystroke, or stop doing so |
| `menubar [enable\|disable\|wpm]` | the icon that shows, hides or starts the HUD, with its WPM (`wpm current\|average\|top` picks it): in the macOS menubar, and on Omarchy a plugin for its bar |
| `setup` | prepare this machine: packages, virtualenv, config, permissions |
| `update`, `uninstall` | fetch a newer tree; remove the tree and the command |
| `keymap` | check the HUD's own files load: the config and the definitions import wrote |
| `import [<repo>]`, `sync` | write the definitions the HUD draws from: the drawing, from the ZMK keymap or the keymap-drawer YAML `keymap:` names, with layer ids and combo layers from a ZMK repo; `sync` does it again from the same sources, and `sync --watch` each time they are edited. `import --pristine` starts over, and says first what only an earlier import had (`--keep-custom` keeps just that) |
| `config path\|show\|edit\|link` | where the config is, what is in it, and linking one kept in a repo |
| `demo [--play SCRIPT]` | serve the pages against a sample keymap, with no keyboard; type a demo script on them |
| `poke`, `feed` | drive the HUD without a keyboard; run the feed alone |
| `version` | what this is and where it lives |

`start --reserve` (Linux) tiles windows beside the HUD rather than under it. `start --hidden` starts it off
screen, counting from the first key. `setup` prints every
privileged step and asks before running it, and `setup --no-sudo` prints them without running any.

Two flags on `feed` read alike and are not: `--no-keys` sends layers only, while `--no-hid-keys`
drops just the HID half — the typed-keys strip and the shift flag — and keeps positions.

## Configuration

`~/.config/zmk-layer-hud/config.yaml` (or `--config` / `ZMKHUD_CONFIG`). Paths may be relative to
the file, and nothing in it is required. [config/example.yaml](config/example.yaml) is the config
`setup` starts you with; [config/diamond.yaml](config/diamond.yaml), the author's own, shows every
key with its default and a comment. The first three rows are what `import` and `sync` draw from;
the HUD itself never reads them:

| key | what |
|---|---|
| `keymap`, `drawer_config` | a keymap-drawer YAML to draw instead of the ZMK keymap, and your drawer config (key sizes, glyphs) |
| `layout` | how the keys sit, as a keymap-drawer layout, when the drawing comes from the ZMK keymap and import cannot find the board's physical layout in its repo |
| `stagger` | a 3x5 split whose `layout` only counts its keys is drawn with a Ferris Sweep's column stagger; `false` keeps it ortholinear |
| `layers` | which drawer layer shows which ZMK layer, when the YAML is curated (`map`) |
| `base` | the drawer layer that is always active (default: the layer for id 0) |
| `positions` | ZMK position of each drawer key when the YAML's key order is not the keymap's |
| `combo_term_ms` | the keymap's combo timeout, so simultaneous presses form a combo |
| `combo_idle_ms` | the keymap's `require-prior-idle-ms` for combos: a chord struck sooner after another key is drawn as its keys, as ZMK types it |
| `combos` | layer coverage for a combo the import gets wrong |
| `keyboard`, `serial`, `ble` | pick one of several ZMK boards; name its serial port; its BLE address |
| `title` | corner text (default: the name of the keyboard that is typing) |
| `hud`, `feed` | every size and timing: panel width and opacity, flash and pill durations, combo slack, how long a key's glow takes to fade, dead-key window … |
| `stats` | which boxes the [stats block](#how-it-works) shows: `true` or `false` for each |
| `fingers` | the finger that strikes each drawer key (`lp` … `li`, `lt`, `rt`, `ri` … `rp`), when the HUD cannot tell from the layout |
| `extras` | inference hints, used only with firmware that reports no positions |

When import draws the ZMK keymap itself, or a YAML `keymap parse` made, layer order and key
order already match the keymap and none of the mapping keys are needed.

Where things live, and what moves them:

| | default | |
|---|---|---|
| `ZMKHUD_CONFIG` | `~/.config/zmk-layer-hud/config.yaml` | the config to read |
| `ZMKHUD_STATE` | `~/.local/state/zmk-layer-hud` | where `panel.log`, `hudfeed.log`, the socket's `token` and the `sessions/` go (this user's alone) |
| `ZMKHUD_ROOT` | the installed tree | the tree the command runs from |
| `ZMKHUD_HOME` | `~/.local/share/zmk-layer-hud` | where `install.sh` puts the tree |
| `ZMKHUD_BIN_DIR` | `~/.local/bin` | where `setup` puts the command |
| `ZMKHUD_PYTHON` | the tree's `.venv/bin/python3` | the interpreter the feed runs under |
| `ZMKHUD_PORT` | `8766` | the feed's WebSocket port; the URL is `ws://127.0.0.1:8766/<token>`, the token in `$ZMKHUD_STATE/token` for the run |
| `ZMKHUD_CACHE` | `~/.cache/zmk-layer-hud/repos` | where `import` keeps a repo given by URL |
| `ZMKHUD_REF` | `latest` | what `install.sh` and `update` fetch: `latest` (the newest release), a release's tag like `v1.0.0`, or a branch |
| `ZMKHUD_RESERVE` | `0` | what `start --reserve` sets |
| `ZMKHUD_DEBUG` | unset | the panel and the feed log every layer message with a timestamp (never the key positions) |

### Sessions

What the keyboard types is counted into a session, and there is always one, the active one. A
new session is named after when it began, and typing adds to it until another takes its place:

```bash
zmk-layer-hud session                 # the active one: every number the stats bar shows, and each layer's share
zmk-layer-hud session save colemak-1  # name it
zmk-layer-hud session new             # start another; the one before stays saved
zmk-layer-hud session list
zmk-layer-hud session load colemak-1  # make it the active one again: typing adds to it
zmk-layer-hud heatmap session         # the keys glow with how often each one was pressed
zmk-layer-hud session history         # the active one, day by day, with each day's layers; --all adds every session's days up
zmk-layer-hud session compare colemak-1   # side by side with the active one (or name a second)
```

`session` prints every number the HUD's stats bar shows, worked out from the session's counts:
keys and the share made as combos, what was typed and deleted, accuracy, typing time, average and
top speed, same-finger bigrams, the hands' shares, and the slowest key. Below those is every
layer's share of the keys, the most used first, where the bar shows the share of the layer on screen
only. `history` gives each day its own line of layers' shares. `compare` sets the same numbers of
two sessions against each other, each layer's share included. That is the way to see what a
change to the keymap did.

`save` on a session that already has a name keeps it as it is and goes on in a copy under the
new one. `reset` zeroes the active session and `delete` removes one that is not active; both ask
first (`--yes` does not). The commands work whether the HUD is running or not, and it follows
what they do within a second.

Counts are kept per layer, by the layer's name in the drawing, and a session writes
down which layers its keymap had. Rename a layer, or take one out, and what was counted on it
can no longer be drawn: `zmk-layer-hud session` lists those layers, and
`zmk-layer-hud session rename-layer OLD NEW` moves their counts over, in the active session or,
with `--all`, in every one.

A session's heatmap can be kept as a picture, drawn by keymap-drawer from the HUD's definitions
with the keymap's own legends, glyphs and combos:

```bash
zmk-layer-hud session export                    # the active one: colemak-1-session.svg, every layer with heat
zmk-layer-hud session export colemak-1 --mode physical -o hands.svg   # all layers added up, on the base
zmk-layer-hud session export --mode speed --layers SYM,NUM            # each key's time, on those layers
```

The steps and colours are the HUD's own, on light keys or dark ones as the HUD's are. `-o -`
writes the SVG to stdout.

A session is kept in `$ZMKHUD_STATE/sessions/<name>.json` (the directory 0700, each file 0600),
and it holds counts: how often each key was pressed on each layer, each combo, how many characters
were typed and deleted, the time spent typing and the best speed, each key's average time, and
the same totals again, with the keys on each layer, for each day it was typed on. It also notes
what the keymap says of each key it counted (its finger and its legend), and keeps `stats`:
every number `zmk-layer-hud session` prints, layers' shares included, written again with the
file, for anything that reads it. Never what was typed, in what order, or when within a day. Nothing leaves the machine. Only the keyboard's own typing is counted: `poke` and
the demo light the board and time their typing, and a session never sees them. `feed.sessions: 0`
in the config (or `feed --no-sessions`) keeps no files, and `uninstall --purge` removes them.
What the HUD protects and what it cannot — the socket's token, the serial port, Bluetooth, the
installer's trust in a branch — is written up in [docs/security-review.md](docs/security-review.md).

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

Three names are linked — the config, `<config>.definitions.json` and `<config>.imported.yaml` —
and that matters: the definitions are looked for beside the config's own path, not beside whatever
that path points at, so linking only `config.yaml` would leave stale definitions in play, or none.
The three also have to travel together: definitions from another import can draw layers the config
maps differently. A sync writes through the links, into the repo's own files.

### Taking it from your keyboard's repo

The HUD reads two files and nothing else: its config, and the definitions `import` writes beside
it (`config.yaml` → `config.definitions.json`). They hold the drawing — every key where it sits,
each layer's legends, the combos, the glyphs — made with keymap-drawer from the YAML the config
names as `keymap:`, or, when it names none, from the keyboard's own ZMK keymap. From a repo they
also hold what the keymap says and a drawing does not: the id of each ZMK layer, and the layers
each combo really fires on (a combo's `layers:` in a drawing is a drawing choice, where the HUD
needs the firmware's gate).

```bash
zmk-layer-hud import github.com/you/zmk-config         # or a path to a working copy
zmk-layer-hud import ~/zmk-config --keyboard corne     # --keyboard: which one, when it holds several
zmk-layer-hud import                                   # no repo: draw the YAML `keymap:` names, alone
zmk-layer-hud sync                                     # read the same sources again, and say what changed
zmk-layer-hud sync --watch                             # and again each time one of them is saved
```

The config stays yours: anything set there wins, and a sync never touches it. What a repo's keymap
says is also written for you to read, in `config.imported.yaml`. When the drawing is a curated
keymap-drawer file, the one thing import cannot know is which drawn layer shows which ZMK layer —
your names, not the keymap's — so it drafts that mapping there and marks the lines it had to leave
undecided. Correct them once in `config.yaml`; sync will not overwrite them. A curated file whose
key order is not the keymap's says each key's position with `positions:`.

`start` refuses until there are definitions, and says which command writes them; `doctor` says
when a source is newer than they are. A sync whose definitions would not load writes nothing, so it
never leaves the HUD without the keymap it had.

A URL is cloned into `~/.cache/zmk-layer-hud/repos` (`$ZMKHUD_CACHE` moves it) and fetched on every
later sync, so a sync sees what you pushed; a path is read where it is, so it sees what you have
not pushed yet. `sync --watch` stays, and syncs again whenever the config, the keymap-drawer files,
or a working copy's keymap and what it includes are saved; the running HUD redraws. A repo given
by URL is not watched. [config/diamond.definitions.json](config/diamond.definitions.json) and
[config/diamond.imported.yaml](config/diamond.imported.yaml) are what it writes for the Diamond.

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
modifiers (US layout, dead keys composed), and builds the keymap the page draws from the config and
the definitions `import` wrote, sending it again whenever either changes. The macOS panel
(`host/macos/panel.py`, PyObjC) runs the feed in-process; the Linux panel talks to it over a
WebSocket.

**The page** (`hud/`) draws the physical layout, lights the exact key for each position while it
is held, groups positions pressed within the combo term into the combo the drawer defines, keeps
a one-shot layer on screen through its key's flash, and shows typed characters in a strip below.
A key also glows once it is pressed, and cools over `hud.heatmap_ms` (3 s; `0` turns it off): a
live heatmap of what was just typed, where a key struck again and again stays warm longest.

Beside the panel, a block of its own says how the typing goes:
- words per minute, live, over the last `hud.wpm_window_ms` (10 s);
- the session's average over its typing time, not counting pauses longer than `hud.wpm_idle_ms`
  (3 s), and its peak;
- accuracy, the share of typed characters that were not deleted;
- keystrokes, and the share of them made as combos;
- the share of typing done on the layer on screen.

Below those it names the [session](#sessions), and switches the heatmap (a click on `heat`): `live`; `session`, how
often each key was pressed on the layer on screen; `physical`, every layer's presses of a key
added up, which is where the fingers went; `speed`, how long each key takes after the key before
it, the slowest the hottest; and `off`. Every keystroke the board draws is counted once, when
nothing can take it back: a combo counts as one keystroke, and a report that beats its own
position is not counted twice.

Its last box is the keys: `light`, as keymap-drawer draws them, or `dark`, with the heat in one
indigo, light blue cold and dark blue hot. A click changes it, and the feed remembers the choice on this machine; until one is made,
`hud.dark: 1` in the config makes them dark. The chevron at the top right of the panel puts the
block away and brings it back, remembered the same way; `hud.stats_bar: 0` leaves it out.

Four more chips are there for the asking, in the config's `stats:` section, which turns any chip
on or off:

```yaml
stats:
  time: true     # how long the session has been typing, pauses left out
  hands: true    # the left and the right hand's shares of the keystrokes
  sfb: true      # same-finger bigrams: two keys in a row struck by one finger, of all such pairs
  slow: true     # the key slowest to strike after the key before it, and its average
  layer: false   # ...and any of the others off: wpm, session, accuracy, keys, layer, heatmap, theme
```

A key's time runs from the keystroke before it, within `hud.wpm_idle_ms`. A key that can be held
(a layer key, a home-row mod) is a keystroke only when it was tapped: held, it typed nothing, and
the key struck under it is timed from the keystroke before. It counts as held when a layer came
up with it, when it stayed down past 400 ms, or when another key went down and came up inside it,
which is how ZMK's balanced hold-tap decides. A thumb or a chord between two keys makes no bigram
of them. `hands` and `sfb` need to know which finger strikes each key. On a split board drawn
the usual way, row by row with the thumbs last, the HUD works that out from the columns: pinky
to index from the outside in, with the index finger taking the two inner columns of a hand of
five or more. Otherwise, say it in drawer order with `fingers: [lp, lr, lm, li, …, lt, rt, …]`.
Neither chip shows until the fingers are known.

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
packages, the GTK bindings on Linux, the config and whether its definitions load (and whether a
sync is due), the udev rule,
whether the command is on your PATH, and what the feed last managed to open — and prints the fix
beside anything that is wrong. What it cannot see:

- **`cannot open <keyboard>`** (Linux, `~/.local/state/zmk-layer-hud/hudfeed.log`): tty permissions
  for the signal channel — install the udev rule, or add yourself to `dialout`. The serial port
  itself needs no permission on macOS, where it is open to every user; once the feed has
  recognised the signal on a port it takes the port for itself (`TIOCEXCL`), so with `positions;`
  on, the key positions it carries are not there for another process to read. A tool that needs
  that same port (`tio`, ZMK Studio) gets it back when the HUD stops.
- **`cannot read what is typed on <keyboard>`**: that is the HID half, and it does need one. On
  macOS grant Input Monitoring to whatever runs the feed (your terminal, or Hammerspoon), and untick
  the keyboard under Karabiner-Elements → Devices, which seizes a keyboard whose events it modifies.
  On Linux it is hidraw access, from the same udev rule. Layers and positions keep working without
  it; only the typed-keys strip goes quiet.
- **`… is not the layer signal`**: that port answered nothing for eight seconds. The board exposes
  more than one CDC interface and this was another; the feed moves on to the next by itself. If it
  says so about every port, the firmware is not sending — build it with the snippet
  (`-S layer-hud-usb-uart`), which is what creates the interface and points `zmk,layer-hud-uart` at it.
- **Nothing over Bluetooth**: pair the keyboard with the OS first; the signal needs a bonded host.
  On macOS the feed takes a keyboard that is already connected straight from CoreBluetooth, so no
  address is needed, but whatever runs the feed needs Bluetooth permission (System Settings →
  Privacy & Security → Bluetooth). `… over BLE, not read` means it connected but found no signal
  service: the firmware was built without `CONFIG_ZMK_LAYER_SIGNAL_GATT`, or macOS has cached the
  keyboard's services from an older firmware — remove and re-pair it.
- **`no definitions yet`**: `start` waits for `import` to have drawn the keymap:
  `zmk-layer-hud import <your zmk-config>`, or with `keymap:` set, `zmk-layer-hud import`.
- **No `layers` lines**: `zmk-layer-hud feed --raw` logs every frame it decodes, so silence there
  separates "the keyboard says nothing" from "the host makes nothing of it".
- **Wrong keys light** on a curated keymap: `positions:` is missing or wrong; the feed logs
  `key position N is not in the keymap's … drawer keys`.
- **A key stays lit ~5 s**: the firmware is reporting presses but not releases. Rebuild and
  reflash it.
- `ZMKHUD_DEBUG=1 zmk-layer-hud start` logs every layer message with a timestamp. Key positions
  are never logged: with the keymap they are the typing. (`feed --raw` does log them, and says so.)

## Limits

- Layer ids 0–31, key positions 0–255.
- `import` and `sync` draw with keymap-drawer, from the virtualenv `setup` makes; without it they
  draw only `cols_thumbs_notation` and `ortho_layout` layouts, and skip combos given as
  `trigger_keys`. The HUD itself needs neither keymap-drawer nor the network.
- Linux has nothing like macOS's secure input, so there the strip shows what is typed into a
  password field too, and the board lights its keys. Stop the HUD, or record with it hidden, when
  that matters.
- The HUD's socket takes one port (`ZMKHUD_PORT`, 8766). If something else holds it when the macOS
  panel starts (a `zmk-layer-hud feed` of its own, say), the panel runs without a socket and
  `zmk-layer-hud poke` cannot reach it. Its log says so. The socket takes a token in its URL path,
  one per run, kept in `$ZMKHUD_STATE/token`: `poke` reads it from there, and a page or process
  without it is refused (HTTP 403) before it sees a message — a browser applies no same-origin
  rule to WebSockets, and what travels there is your typing.

## Contributing

From a clone, `make install` sets the machine up and puts `zmk-layer-hud` on your PATH pointing at
that clone; `make test` runs every suite — the firmware's wire policy in C, the host decoder and
keymap conversion in Python, and the page under node.
[docs/development.md](docs/development.md) covers what each suite sweeps and how the command line
is put together.

## License

MIT — see [LICENSE](LICENSE).
