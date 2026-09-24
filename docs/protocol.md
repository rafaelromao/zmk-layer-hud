# The feed protocol

Everything on screen comes from one stream of messages. Three things can produce it — the
keyboard, the command line, and a WebSocket client — and the page treats them alike, with one
exception: typing sent in says whether combos are how it is typed ([below](#typing-sent-in)), and
that field is also how the page knows a character did not come from the keyboard's own layers.
The sameness is what makes the HUD testable without hardware, and what to keep in mind when
reading a HUD that looks right: only the keyboard proves anything.

## The socket

`zmk-layer-hud feed` serves `ws://127.0.0.1:8766` (`--port`, or `ZMKHUD_PORT`; `--no-ws` turns it
off). This is how the Linux panel is fed, and it is what `zmk-layer-hud poke` talks to.

The macOS panel runs the feed in-process and opens no socket. To drive that one, use the page's
own API in a browser, or run `zmk-layer-hud feed` yourself and point a browser page at it with
`index.html?ws=ws://127.0.0.1:8766`.

## Outbound messages

One JSON object per frame. A message produced by a keyboard also carries `"device"`, its name, so
a page can show which keyboard is typing.

| message | when |
|---|---|
| `{"kind":"keymap", …}` | on connect, and whenever the config, the keymap YAML or the imported file changes |
| `{"kind":"layers","ids":[2,22]}` | the active ZMK layer ids changed (layer 0 is omitted: always active) |
| `{"kind":"device","name":"Diamond"}` | a keyboard was opened |
| `{"kind":"press","pos":13}` / `{"kind":"release","pos":13}` | a key at that ZMK position went down / up |
| `{"kind":"key","type":"keyDown","name":"a","chars":"a","code":4,"flags":{"cmd":false,"ctrl":false,"alt":false,"shift":false,"fn":false},"repeat":false}` | a key or modifier change, decoded from the HID reports |

`type` is `keyDown`, `keyUp` or `flagsChanged`. The last `keymap`, `layers` and `device` are cached
and replayed to every new client, so a page that connects late — or reconnects — is right
immediately rather than blank until you touch something.

## Inbound messages

A client may send `layers`, `press`, `release`, `key` and `device`, and each is fanned out to every
client as a keyboard's would be — a `key` with one field more, [below](#typing-sent-in):

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
page's ✕ sends. `--no-inject` refuses the five message kinds above; it does not disable `close`.

The keymap is not injectable: it is large, it is built from files the feed already watches, and a
page given a wrong one has no way back.

### Typing sent in

A character alone does not say how it was typed. On the Diamond, `z` is both the `r`+`a` chord and
Alpha 2's own key, and which one the board lights is a matter of technique. So a `key` sent in
says it:

```json
{"kind":"key","type":"keyDown","name":"z","chars":"z","combos":false}
```

| `combos` | `z`, the keyboard on its base |
|---|---|
| `false` — **the default** | Alpha 2's key, and the key that brings Alpha 2 up |
| `true` | the `r`+`a` chord, and its pill |

A `key` that does not say gets `false`: the feed fills it in before fanning the message out, so a
client only has to send the field to turn combos on. Off, a character is drawn by a way that is not
a combo wherever the keymap has one; a character only a combo types is still drawn as that combo.
On, the layers already up come first, combos and all.

Either way, typing sent in is placed by technique, not on the keyboard's layers: it did not come
from them, so Alpha 2 can light for a `z` while the keyboard is on its base. The keyboard's own
reports never carry the field, and are still placed exactly on the layers it says are up — there,
a `z` with only the base up can only have been the chord.

## zmk-layer-hud poke

`poke` is a client of this socket, and sends the messages a keyboard would have produced:

```bash
zmk-layer-hud poke --type "hello, world"   # type it, character by character
zmk-layer-hud poke --type zebra --combos   # ...drawn with the keymap's combos where it has them
zmk-layer-hud poke --legend 'á'            # one legend, composed as the decoder would
zmk-layer-hud poke --layers 2,22           # set the active layer ids ('' clears)
zmk-layer-hud poke --press 13              # light the key at ZMK position 13, then release it
zmk-layer-hud poke --stdin < script.jsonl  # raw messages, one JSON per line
```

`--combos` draws what is typed with the keymap's combos ([Typing sent in](#typing-sent-in); without
it, none). `--gap-ms` and `--hold-ms` set the timing, `-v` echoes what it sends, `--url` points it
elsewhere.
Characters go through `host/uskeys.py`, so an accent arrives as the one composed keyDown the real
decoder would have emitted.

This exercises the page — layout, legends, strip, combo grouping — and nothing below it. The
keyboard, the firmware and the wire format are never involved, so a HUD that looks right under
`poke` can still be fed wrong by a real keyboard.
