# Demo scripts

A demo script is a JSON file that says what a keyboard does, step by step. The same file plays in
real time on the demo page, or into a running HUD, and renders the frames of a GIF:

```bash
zmk-layer-hud demo --play docs/demo-type.json --loop       # on the demo page, again and again
zmk-layer-hud poke --play docs/demo-type.json              # into a running feed (the Linux panel)
bash docs/make-gif.sh --script docs/demo-type.json --out demo.gif
host/play.py docs/demo-type.json --keymap keymap.json --check   # what it plays, what it cannot type
```

`host/play.py` turns a script into what the keyboard would send and when: key positions, the
layers it reports, and the HID reports for what is typed. Text is typed through the keymap by the
rules `host/ways.py` states for ZMK, each character on the key or combo of the layer the keymap
has it on. So a demo is typed the way the board types it, and the page draws it exactly as it
draws the keyboard.

## The script

```json
{
  "device": "3x5 example",
  "opacity": 100,
  "wpm": 60,
  "steps": [
    { "type": "Hello (world)" },
    { "wait": 900 },
    { "type": " 42", "stills": "last" },
    { "layers": [2], "press": [32], "hold": [32] },
    { "layers": [2], "press": [32, 16], "hold": [32], "keys": [{ "name": "down" }] },
    { "layers": [] }
  ]
}
```

At the top, all optional but `steps`:

| field | what |
|---|---|
| `steps` | the script, in order |
| `device` | the panel's title |
| `opacity` | the panel's opacity for the demo and the GIF (`poke` cannot change it) |
| `wpm` | how fast text is typed (default: about 70 wpm, the pace the tests replay at) |
| `combos` | type text with the keymap's combos where it has them (default: `false`, as `poke`) |
| `step_ms`, `hold_ms` | how long a frame stays, played, and how long its keys stay down (1000, 250) |
| `loop` | play it again and again (as `--loop`) |

A step is one of three things.

**Text**: `{"type": "…"}`, typed a legend at a time, the longest the keymap has first (`ão`
before `ã`). Each character goes on the way a typist would use there: on the layer that is
already up, else the base, the base with Shift held, another layer with the key that brings it
up. A combo is used only with `"combos": true`, or where nothing else types the character. A
space, a newline, a tab and the named-key glyphs (`⎋ ⌫ ⌦ ↵ ⇥ ← → ↑ ↓ ⇱ ⇲ ⇞ ⇟`) type those keys,
whatever the drawing calls them (`␣`, `Space`). On a board drawn in capitals, a lowercase letter
is typed on its capital's key. A character the keymap cannot type is named and skipped
(`--strict` refuses the script instead). Optional:
- `combos` and `wpm`, for this text alone;
- `layers`, the layers it is typed over (vim's insert mode, say);
- `stills`: its frames in a GIF, `each` keystroke (the default), the `last` one, or `none`.

**A pause**: `{"wait": 900}`, in ms. Nothing happens and it makes no frame.

**A frame**: what the GIFs were always made of:
- `layers`: the ZMK layer ids up;
- `press`: the ZMK positions struck, together (inside the combo term, so a combo draws its pill);
- `keys`: the strip, as a string per chip or part of a key event (`{"name": "down"}`,
  `{"chars": "w", "flags": {"ctrl": true}}`).

Played, a frame is one keystroke or chord, then `step_ms` of stillness. Optional:
- `hold`: positions that stay down into the next frame. A key holding a layer that is up in both
  frames stays down by itself, so a thumb under a chord needs nothing said.
- `ms`, `hold_ms`: this frame's own lengths.
- `typed`: what the frame types when played, when the strip rule below guesses wrong.

`keys` is the strip as the frame shows it, and a frame is a fresh page, so a word being typed is
written `["n"]`, `["n", "o"]`, `["n", "o", "t"]`. Played, a frame types what it adds: when its keys
begin with the frame before's, only the rest. A pause of 1.8 s or more empties the strip, as it
does on screen.

## Played, and as frames

The player keeps to the clock it started on, so a message late for any reason does not push the
rest back. Keys go down 5 ms apart in a chord, and never closer than the combo term plus a margin
between keystrokes, however fast the script says to go. `--speed` quickens the pace, the frames
and the pauses, and never the chords.

Everything played is sent in (`"sent": true`, [protocol.md](protocol.md#inbound-messages)): the
board lights, the bar times it and the keys glow, and no session counts it. The macOS panel has no
socket to play into; `zmk-layer-hud demo --play` is the way to watch a script there.

For a GIF, `host/play.py --stills` gives the frames `docs/make-gif.sh` renders. Frames are
themselves, text is a frame per keystroke with the strip it has typed so far, and a pause is
nothing. A script of frames alone renders exactly as it did before text could be typed in one.
