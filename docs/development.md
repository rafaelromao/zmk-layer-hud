# Development

## From a clone

```bash
git clone https://github.com/rafaelromao/zmk-layer-hud
cd zmk-layer-hud
make install
```

`make install` is `bin/zmk-layer-hud setup --link`: it puts `zmk-layer-hud` on your PATH pointing
at the clone, so you run exactly what everyone else runs. Everything else — the virtualenv, the
system packages, the config, the udev rule — is what `setup` does for any machine. `make venv`
builds just the virtualenv, which is what the error messages that mention it mean.

`$ZMKHUD_BIN_DIR` puts the command somewhere other than `~/.local/bin`.

`zmk-layer-hud update` refuses to touch a clone; `git pull` is its update path.

## The suites

```bash
make test        # firmware wire policy (C) + host decoder and keymap conversion (Python) + the page (node) + the landing page
make test-hud    # just the page; KEYMAP=hud/keymap.json runs it against your own board
make test-site   # just the landing page: what it is built from, and its demo on the HUD page
make audit       # the drawing against the keyboard's own keymap; SOURCE=path/to/zmk-config for a working copy
make fixture     # rebuild the committed test keymap from the configured one
```

`zmk-layer-hud keymap` checks that the configured keymap-drawer YAML converts.

`firmware/src/signal_frame.h` is the single definition of the wire format; the C and Python tests
share its vectors, so a change on one side that is not mirrored on the other fails both.

`make test-hud` sweeps the page: every key on every layer it can be shown on, every combo on every
layer it is declared on and in four press orders, every press that must draw no combo, and the
typed-keys strip against what a keyboard would have sent to type each legend. Over 5000 checks in
about a third of a second. The cases are generated from the keymap message, so pointing it at
another board sweeps that board.

Those two drive one channel each, with the page's own rules for what a keymap means, and that is
how a letter typed by a combo lit nothing once the board had to work from the character: both
stayed green. `hud/tests/words_test.js` works from the keyboard's side instead. `host/ways.py`
lists every way the keymap has of typing each legend — its key, a combo, another layer, a held
Shift — under ZMK's own rules (a combo fires on the highest active layer only; a hold-tap's report
comes when the key comes up), stated there rather than borrowed from `hud.js`. Each way is replayed
down every channel it can arrive by: positions and reports in either order, reports alone with and
without layers, positions alone, and typing sent in with combos on and off. Words go key after key
on one page at a typist's pace, so a way is also tested next to its neighbours: a one-shot layer
that outlives its key, a chord inside a combo term, a macro across two layers.

`make audit` checks what those cases cannot: the drawing itself. One drawer layer stands for every
ZMK layer drawn as it, so where two of them fire different things on the same keys, the HUD can be
right for only one. It reads the keyboard's keymap with keymap-drawer's own parser (what `import`
reads) and lists every such chord.

`hud/tests/dom.js` is a browser small enough to read — the DOM the page touches and a clock the
test drives by hand — so `hud/hud.js` and `hud/keys.js` run under node exactly as they ship, with
no npm and nothing to build. A DOM that small can also be wrong, so the same cases run in the real
page: serve `hud/`, open `index.html?keymap=tests/fixtures/diamond.json`, and

```js
await import("./tests/browser.js"); await hudBrowserSweep("tests/fixtures/diamond.json")
```

prints the same count and the same failure digest that `node hud/tests/hud_test.js --signature`
does. A case the two disagree about is a hole in the shim.

## Driving the HUD without a keyboard

`zmk-layer-hud poke` sends the feed the messages a keyboard would have produced, and
`zmk-layer-hud demo` serves the pages against a sample keymap, on a socket of its own that `poke
--url` reaches. The WebSocket both speak is documented in [protocol.md](protocol.md).

A demo script ([demo-scripts.md](demo-scripts.md)) is compiled by `host/play.py` into the
positions, layers and reports a keyboard would send, and when. `host/play_test.py` holds it to the
keymap's own ways of typing each character (`host/ways.py`) and to what the page needs of the
timing, and `hud/tests/play_test.js` plays the committed scripts on the page, keystroke by
keystroke. `make fixture` also rebuilds `hud/tests/fixtures/example-3x5.json`, the 3x5 sample's
keymap those scripts are typed on.

## The landing page

`site/` is the page at <https://rafaelromao.github.io/zmk-layer-hud/>, and `make site` builds it
into `build/site/`; `STRICT=1` fails where CI fails.

`site/build.py` puts together the page's own files, the HUD page as it ships (the files
`hud/index.html` names, nothing else), and for each board in `site/boards.json` its keymap message
and its demo. A board is converted the way the panel converts it, under the venv with
keymap-drawer's layouts and glyphs, and its `source` is replaced with a public one: a dump names the
keymap by a path under the home folder of whoever built it. A demo is compiled ahead of time by
`host/play.py`, in the shape `--capture` prints. Without `STRICT=1`, a board whose files are not on
this machine is skipped with a line saying so: the Diamond's keymap lives in
[rafaelromao/keyboards](https://github.com/rafaelromao/keyboards), which CI checks out beside this
repo.

The page runs the HUD in a frame, `hud/index.html?embed`. With `?embed` the HUD installs no keydown
listener of its own and hides the ✕, because the page around it owns the keyboard. `site/demo.js`
turns the browser's key and input events into the feed's own key messages (typing sent in: `sent`,
and `synthetic`, since no firmware position stands behind them) and plays a compiled demo on a
clock of its own; `site/site.js` hands both to the frame through `hud.receive`. The frame never gets
`?ws=`: there is no feed behind a public page. To look at it, serve `build/site`
(`python3 -m http.server -d build/site 8790`) and open <http://localhost:8790>.

`.github/workflows/pages.yml` runs `make test`, then `make site STRICT=1`, and publishes
`build/site` on every push to `main` that touches what the page is built from. The repo's Pages
source has to be *GitHub Actions* (Settings → Pages) for it to land.

`site/og.png`, the card that link previews show, is rendered by hand from `site/og.html` and
committed. `og.html` draws the real HUD as the 3x5 demo leaves it 1450 ms in:

```bash
make site
python3 -m http.server -d build/site 8791 --bind 127.0.0.1 &
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu \
  --no-first-run --user-data-dir="$(mktemp -d)" --hide-scrollbars --window-size=1200,630 \
  --virtual-time-budget=3000 --screenshot="$PWD/site/og.png" http://127.0.0.1:8791/og.html
kill %1
```

On Linux, `google-chrome` or `chromium` in place of the macOS path.

## The command line

`bin/zmk-layer-hud` finds an interpreter and hands off to `host/cli.py`, which is where every verb
lives. Two rules it keeps, and both matter:

- **Nothing above the stdlib is imported at module level.** `doctor` and `setup` have to run on a
  machine where the virtualenv does not exist yet — that is when they are most needed — and
  Apple's `/usr/bin/python3` is 3.9, where keymap-drawer will not even install.
- **The verbs that need the venv re-exec into it first** (`NEEDS_VENV`). The rest must keep
  working on a half-installed machine.

Starting and stopping stays in `host/macos/start.sh` and `host/linux/hud.sh`; `cli.py` picks one
and gives them the same verbs. `poke` and `feed` are split off before argparse sees them and
handed their whole line, because their flags belong to `host/hudpoke.py` and `host/hudfeed.py` and
restating them here would only give them somewhere to drift apart.
