# Testing every open PR together

This branch, `all-open-prs`, is `main` with every open PR merged into it, so that they can be tried
live as one build before each is reviewed alone. It is not for merging: its draft PR carries this test
plan and the results, and each PR still merges on its own.

| PR | Branch | What to look at |
|----|--------|-----------------|
| [#1](https://github.com/rafaelromao/zmk-layer-hud/pull/1) | touch-keyboard | the touch keyboard: docked, typing, remote, the tablets' keyboards |
| [#6](https://github.com/rafaelromao/zmk-layer-hud/pull/6) | keystroke-module | keystrokes through `hud/keystrokes.js`; a combo fires on the highest layer up |
| [#8](https://github.com/rafaelromao/zmk-layer-hud/pull/8) | key-names | one key vocabulary; a named key drawn as a word lights |
| [#9](https://github.com/rafaelromao/zmk-layer-hud/pull/9) | panel-module | both panels as one module: show, hide, dock, size, the icons |
| [#10](https://github.com/rafaelromao/zmk-layer-hud/pull/10) | keymap-source | `sync --watch` and `doctor` see a drawing's layout file; a missing `--config` is an error |
| [#11](https://github.com/rafaelromao/zmk-layer-hud/pull/11) | keymap-reader | the Python tools read a keymap message as the page does |
| [#12](https://github.com/rafaelromao/zmk-layer-hud/pull/12) | feed-readers | every wired keyboard read through one loop; a lost keyboard lets its modifiers go |
| [#13](https://github.com/rafaelromao/zmk-layer-hud/pull/13) | hud-state | one reading of the HUD's state for every verb and both icons; `status --json` |
| [#16](https://github.com/rafaelromao/zmk-layer-hud/pull/16) | feed-one-carrier | a keyboard on USB and BLE at once is read on one carrier |

The stack's five tips (#13, #10, #11, #12, #16) were merged onto `main`; the rest come in through
them. Only #16 met #12, in `host/hudfeed.py` and its test, and was resolved as the note on #16 says.
`main` was merged in again after its WebKitGTK 2.54 fix to the Linux panel.

## On macOS: the script

```bash
./test-all-open-prs-macos.sh
```

It runs everything a script can check by itself, and asks you about the rest, one stage at a time:
the test suites (with the ports and ptys this machine allows), the branch's HUD in place of the
installed one, the CLI verbs against `status --json`, a second copy of the HUD, the menubar icon,
docking and typing, the keyboard over USB, BLE and both, the keymap tools, and a tablet in remote
mode. Each answer goes to `run/all-open-prs-macos/results.txt` (pass, fail with what happened, or
skip), and the logs beside it. Ctrl-C stops it; a run again offers the answers already given.

At the end it puts things back: the branch's HUD and icon stop, and the installed copy's start again
if they were running.

## On Omarchy: by hand

Get the branch:

```bash
zmk-layer-hud update --ref all-open-prs
```

(or `git fetch && git switch all-open-prs` in a clone), then `zmk-layer-hud typing setup` once for
the uinput rule. Afterwards `zmk-layer-hud update --ref latest` goes back to the release.

The test suites, as on macOS: `make test PYTHON=.venv/bin/python`.

**The panel and the CLI (#9, #13)**
- `start`, `hide`, `show`, `toggle`, and Ctrl+Alt+L; the page's hide button.
- Drag the HUD, `restart`, and it comes back where it was left.
- The banner's attach and detach; `dock` and `dock off`. A board too big to dock floats, and
  `dock status` says why. Minimized while docked, it keeps the bar's height, and the exclusive zone
  shrinks to the bar.
- `status` says "panel: running, shown" and "feed: running"; `status --json` prints the object.
- The bar's icon, its hover and its menu, with the HUD shown, hidden and stopped; "Stop Remote
  Typing" after `remote on`, gone after `remote off`. The stats column's heading opens the site.
- From a second clone, `show` and `power` say where the HUD runs and leave it alone.

**Typing (#1, #6, #8)**
- Touch reaches the docked layer surface without stealing focus; the exclusive zone re-tiles.
- The uinput rule works, and your `kb_layout` applies: dead keys, ç.
- The keys are 18 mm on a ruler (the monitor's size from GDK), and the Diamond fits at
  `dock.height_pct: 60` (it needs about 115 mm of height).
- A base combo struck with a layer up that declares no combo there counts as two presses.
- Named keys light from reports alone: Tab, Esc, Page Down, Delete, Caps Lock. The strip draws
  ⇥ ⌘⇧C F5 ↵ ⌥←.

**The keyboard (#12, #16)**
- hidraw with the strip; unplug while holding Shift and Shift is let go; replug and it reads again.
- With the keyboard unplugged, the log says once that no serial port is the keyboard's.
- On USB and BLE at once, every key arrives once; unplug USB and BLE takes over with the layers.
- Do the serial port and the HID reader name the keyboard alike? `zmk-layer-hud log` shows both.
  If they differ, the page may count two devices.

**The keymap tools (#10, #11)**
- `sync --watch` against your keyboards working copy: an edit to the drawing's layout file syncs.
- `make audit SOURCE=<working copy>`; `doctor` names a missing layout file beside its drawing.
- `keymap --config` of a file that is not there is an error.

**Remote (#1)**: `remote on`, then an iPad and an Android tablet, each in the browser and from the
home screen; `remote off`.

## Not on either machine

The tablets' own keyboards (#1, #8) are built as `docs/tablets.md` says: first-show latency,
two-thumb rollover, the App Group read without Full Access, a killed renderer coming back, the real
size from the dpi and the HUD-only fallback held upright, detaching back to the system keyboard and
picking this one again, minimizing, and the iPad picking up a fresh import. An iPad project made
before #8 lacks `keynames.js`: generate it again.
