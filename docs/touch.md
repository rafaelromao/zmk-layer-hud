# The touch keyboard

The HUD can type. Tap a drawn key and it does what that key does on the real keyboard: `&kp A`
types a, `&mt LSHIFT A` types a when tapped and holds Shift when held, `&lt 2 SPACE` is a space or
layer 2, `&sk LSHIFT` shifts the next key. Docked, the HUD sits along the bottom of the screen at
full width and never takes the keyboard focus, as an operating system's onboard keyboard does.

This page is the contract between the pieces that make that work. Each piece can be built and
tested against it alone.

```
ZMK repo ──sync──► <config>.definitions.json   zmk.keymap: the raw bindings, behaviours, combos
                        │                       (a drawing without a repo: its legends)
                build_message ──► the keymap message, with "actions" compiled and "touch" settings
                        │
   page:  engine.js (a virtual firmware) ◄── touch.js (pointer events) ──► hidtext.js (HID → text)
                        │ {"kind":"hid"} report snapshots                    │ text ops
     ┌──────────────────┼────────────────────────┐                           │
 macOS bridge     Linux bridge       remote.js (Wi-Fi, paired)     Android IME / iOS keyboard
     │                  │                        │                           │
  inject.Typist ── cgevent.py │ uinput.py ◄── remote.py            ImeSink (Kotlin) / TextSink (Swift)
```

- **The firmware runs in the page.** `hud/engine.js` is ZMK's keymap, behaviours and HID report,
  rewritten as a pure state machine: no DOM, no timers of its own, no `Date`. The same file runs in
  node's tests, in both panels' web views, in a tablet's browser, in Android's WebView and in iOS's
  JavaScriptCore.
- **A desktop host is a HID sink.** It receives what a keyboard would send -- a modifier byte and
  the usages held -- and presses those keys at the operating system, which applies the user's
  layout as it does for the physical keyboard. A US-International dead key stays a dead key.
- **Only the panel's own page can type** on the machine it runs on: typing goes through the
  panel's native bridge, never the feed's socket, whose token sits in every page URL. A tablet
  types through a separate, paired channel ([remote mode](#remote-mode)).

## What a key does: `actions`

### Captured at import

`sync` reads the keyboard's keymap with keymap-drawer's devicetree parser, as it already does for
the layer list and the combo timings, and keeps the bindings themselves in the definitions file:

```json
"zmk": { "layers": {...}, "combos": [...],
  "keymap": { "v": 1, "positions": 36,
    "layers":    { "0": ["&kp Q", "&lt 10 D", "&mt LCTL N", "..."], "1": ["&trans", "..."] },
    "behaviors": { "hml": { "compatible": "zmk,behavior-hold-tap", "cells": 2,
                            "props": { "bindings": ["&kp", "&kp"], "flavor": "balanced",
                                       "tapping-term-ms": ["280"] } } },
    "overrides": { "mt": { "flavor": "balanced" } },
    "combos":    [ { "name": "esc", "positions": [0, 1], "binding": "&kp ESC", "layers": [0, 2],
                     "timeout-ms": 30, "require-prior-idle-ms": null, "slow-release": false } ],
    "conditional": [ { "if": [1, 2], "then": 3 } ] } }
```

- `layers` is keyed by ZMK layer id, numbered as `sync` numbers them (reserved layers count only
  on firmware that keeps them in place).
- A behaviour's properties come in the shapes keymap-drawer's `DTNode` returns them: a phandle
  array as binding strings, any other array as its cells split at whitespace, a string, or `true`.
- `overrides` are a keymap's `&mt { flavor = "balanced"; };` edits of a built-in behaviour.
- Only the behaviours a key or a combo reaches are kept, through the bindings of the behaviours
  they name in turn.
- The file's `version` stays 1, so a HUD older than the touch keyboard still reads it; `keymap.v`
  is versioned on its own. A file without `keymap` -- an older import, a drawing with no repo --
  types from its legends.

### Compiled at runtime

`build_message` compiles what was captured, so a fix to the compiler reaches every keyboard
without an import. The keymap message gains three fields:

```json
"schema": 1,
"actions": { "v": 1, "source": "keymap", "positions": 36, "default": 0,
  "table":  [ {"op": "trans"}, {"op": "kp", "key": {"page": 7, "id": 20, "mods": 0}, "raw": "&kp Q"} ],
  "layers": { "0": [1, 7, 9, "..."], "1": [0, 0, "..."] },
  "combos": [ { "positions": [0, 1], "layers": [0, 2], "action": 12, "term": 30, "idle": 0,
                "slow_release": false, "drawn": 4 } ],
  "conditional": [ { "if": [1, 2], "then": 3 } ],
  "coverage": { "keymap": 1010, "legend": 52, "unsupported": 18,
                "why": { "zmk,behavior-adaptive-key": 11 } } },
"touch": { "enabled": 1, "hold_ms": 280, "flavor": "balanced", "latch": 1, "lock_ms": 350,
           "sticky_ms": 0, "repeat_delay_ms": 450, "repeat_ms": 60, "combos": "chips",
           "heat": 1, "strip": 1 }
```

- `table` holds each distinct action once; `layers[id]` lists, for every ZMK position, the index
  of its action in `table`. `combos[].drawn` is the combo's index in the message's `combos`.
- `source` is `keymap` (every binding compiled), `legends` (a drawing with no repo), or `mixed`
  (some bindings fell back to their legends, see below).
- `schema` is the keymap message's own version: a tablet app refuses a message newer than it knows.

An action is an object tagged by `op`. A key `K` is `{"page": 7|12|1, "id": n, "mods": byte}`.
`L` is a ZMK layer id. Any action may also carry `raw` (the binding it came from) and
`"via": "legend"`.

| `op` | Fields | ZMK behaviour |
|---|---|---|
| `none`, `trans` | | `&none`, `&trans` |
| `kp`, `kt` | `key` | key press, key toggle |
| `mo` | `layer`, `locking` | momentary layer |
| `to` | `layer` | to layer |
| `tog` | `layer`, `mode` (`flip`/`on`/`off`) | toggle layer |
| `sl` | `layer`, `release_after`, `quick_release`, `lazy` | sticky layer |
| `sk` | `key`, `release_after`, `quick_release`, `lazy`, `ignore_mods` | sticky key |
| `ht` | `hold`, `tap` (actions), `flavor`, `term`, `quick_tap`, `prior_idle`, `retro_tap`, `trigger_positions`, `trigger_on_release` | hold-tap (`&mt`, `&lt`, custom) |
| `morph` | `mods`, `plain`, `morphed` (actions), `keep` | mod-morph |
| `td` | `term`, `taps` (actions) | tap dance |
| `macro` | `steps`: `{press: A}`, `{release: A}`, `{tap: A, ms}`, `{wait: ms}`, `{pause: true}` | macros |
| `repeat` | `pages` | key repeat |
| `caps_word` | `continue` (keys), `mods` | caps word |
| `mouse` | `button`, or `move` / `scroll` `[x, y]` | mouse keys |
| `system` | `kind`: `bt`, `out`, `rgb`, `bl`, `ext_power`, `reset`, `bootloader`, `studio`, `soft_off` | does nothing on screen |
| `unsupported` | `why` | a behaviour the engine does not run |

### When there is no binding: the legends

A drawing with no ZMK repo behind it (`config/romak-34.yaml`, the examples) has only legends, and
a binding the engine cannot run (a module's adaptive key, leader key, layer morph) has nothing
better. Either way the key's legend says what to type:

- A drawn layer's name is `mo` of it, or `sl` where the key is marked sticky.
- A modifier legend (⇧, Ctrl, ⌘, Shift Super) is `kp` of that modifier.
- A character or a named key (Tab, ⌫, ↵) is `kp` of the key a US layout types it with, Shift,
  Option or a dead key included. A word of letters on a typing layer (`qu`, `ão`) is a macro.
- A hold legend makes the key a balanced hold-tap; a hold that names a layer and leaves it on
  after the tap (Romak's ç) is the tap and then that layer, sticky.
- Anything else -- a label, an icon -- is `none`.

The legend's action is marked `"via": "legend"`. `sync` reports how many keys typed from their
bindings, how many from their legends, and which behaviours it could not run.

## The engine

```js
const eng = ZmkEngine.create(actions, { touch, sink, trace });
eng.down({pos: n} | {combo: i}, t)  -> events      // t: milliseconds, any monotonic clock
eng.up({pos: n} | {combo: i}, t)    -> events
eng.tick(t)                         -> events      // what fell due by t
eng.nextDeadline()                  -> ms | null   // when tick next has something to do
eng.adopt(ids, t)                   -> events      // while idle: take the physical keyboard's layers
eng.releaseAll(t)                   -> events      // everything up: a reload, a hidden page
eng.state()                         -> { layers, locks, top, mods, capsWord, down, latches, idle }
ZmkEngine.drive(eng, { now, setTimeout, clearTimeout, emit })   // arms one timer for a page
```

`touch` is the message's `touch` settings, or null; `sink` is `{repeat, pages, mouse}` from the
host's sink; `trace` adds the keycode events ZMK's tests compare.

Events:

```js
{ kind: "report", t, seq, mods, keys: [usage], consumer: [usage], mouse: { buttons } }  // on change
{ kind: "layers", t, ids: [top first], locks: [...] }
{ kind: "latches", t, latches: [{ pos, kind: "mod" | "layer", state: "oneshot" | "locked" }] }
{ kind: "unsupported", t, input, raw, why }
{ kind: "keycode", t, state, page, id, implicit, explicit }   // with trace
```

With `touch: null` it is the firmware: ZMK's event order, layer rules, hold-tap decisions and
captured events, sticky keys, mod-morphs, tap dances, the macro queue, key repeat and caps word,
ported from ZMK's sources and held to 182 of ZMK's own behaviour tests
(`hud/tests/fixtures/zmk_vectors.json`, written by `make zmk-vectors ZMK_DIR=...`). With `touch`
set, glass changes a few things:

| Gesture | What it does |
|---|---|
| Tap | press and release |
| Long press past `hold_ms` (280 by default: glass is slower than a switch) | hold; the flavour is `balanced`, so a two-thumb roll stays letters |
| A hold released with no other key touched | **latches**: a modifier becomes one-shot, a layer becomes one-shot; released again within `lock_ms`, it **locks**; the next press of it lets go |
| A hold while another finger types | a real hold, never latched |
| A tap on `&mo` | a one-shot layer; a double tap locks it |
| A plain key held | repeats |

## The page

- `hud/touch.js` takes pointer events on the board -- delegated on `#board`, because the board
  rebuilds its keys on every resize -- and drives the engine. A key is chosen when the finger
  lands and kept until it lifts, as a switch would be.
- The engine's layers are drawn over the physical keyboard's. Latched and locked keys show it
  (`.latched`, `.locked`), and the banner says *touch · one shot* or *touch · locked*.
- Touch typing counts nowhere: not in the session, the WPM, or the tallies. It warms the live heat
  and shows in the typed-keys strip, except while a secret is being typed.
- In **dock** mode (`<html class="dock">`) the board fills the width it is given and the height
  the host allows (`max_height`), centred; the stats column, the status line and the strip are
  hidden, and the banner is one thin line. The page reports the height it took.

### Messages

Page → host, typing:

```json
{"kind":"hid","v":1,"seq":12,"mods":2,"keys":[4]}
```

- A snapshot of everything held, never a press or a release on its own: a lost message is mended
  by the next one, and `{"mods":0,"keys":[]}` lets everything go.
- While anything is held the page sends the snapshot again every 500 ms. A host that hears nothing
  for 1.5 s lets that page's keys go, and so it does when the page reloads, hides or quits.
- `keys` holds at most ten keyboard-page usages; a modifier usage there is folded into `mods`.

Host → page:

```json
{"kind":"touch","on":true,"sink":{"output":"hid","repeat":"os","pages":[7],"shortcuts":true}}
{"kind":"dock","on":true,"max_height":360}
{"kind":"typing","state":"denied","fix":"zmk-layer-hud typing setup"}
```

- `sink.output` is `hid` (report snapshots, for a desktop host) or `text` (the page translates
  reports to text first, for a tablet's keyboard). `repeat` says who repeats a held key: `os` (the
  host or its operating system does), `synthetic` (the engine sends it again), or `none`.
- `typing.state` is `ok`, `off`, `denied` (the operating system's permission is missing),
  `missing` (no way to type at all, e.g. no `/dev/uinput`) or `error`; the page shows `fix`.
- A native shell calls `hud.setTouch(sink)` and `hud.setDock({on, max_height})` directly.

For a text sink, `ZmkHidText.textEvents` turns reports into `{kind:"text", text}`,
`{kind:"key", key, mods}` (backspace, delete, enter, tab, escape, arrows, home, end, page up and
down, F-keys) or `{kind:"chord", chars, name, usage, mods}` (a ⌘ or Ctrl shortcut). Every event
keeps the `usage` and `mods` it came from.

## Remote mode

A tablet's browser types into the computer: `zmk-layer-hud remote on` serves the page on the
local network and prints a URL to open on the tablet. The URL carries a key in its fragment, which
a browser never sends over the network; the tablet proves it holds the key, and every message it
sends is signed with it. Anyone on the same Wi-Fi can see which keys are tapped -- the page and its
messages travel as plain HTTP -- but cannot type. See `docs/security-review.md`.
