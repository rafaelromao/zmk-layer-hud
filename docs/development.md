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
make test        # firmware wire policy (C) + host decoder and keymap conversion (Python) + the page (node)
make test-hud    # just the page; KEYMAP=hud/keymap.json runs it against your own board
make audit       # the drawing against the keyboard's own keymap; SOURCE=~/projects/keyboards for a working copy
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
`zmk-layer-hud demo` serves the pages against a sample keymap. The WebSocket both speak is
documented in [protocol.md](protocol.md).

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
