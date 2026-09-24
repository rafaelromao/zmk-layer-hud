#!/usr/bin/env python3
"""Drive the HUD without a keyboard, by sending hudfeed the messages one would have produced.

hudfeed accepts layers / press / release / key from a WebSocket client and fans them out to the
pages exactly as it fans out the keyboard's own (see Hub.INJECTABLE). This is the sender.

    host/hudpoke.py --type "hello, world"     type it, character by character
    host/hudpoke.py --type zebra --combos     ...drawing the keymap's combos where it has them
    host/hudpoke.py --layers 2,22             set the active layer ids
    host/hudpoke.py --press 13                light key at position 13, then release it
    host/hudpoke.py --legend 'á'              one legend, composed as the decoder would
    echo '{"kind":"layers","ids":[1]}' | host/hudpoke.py    raw messages, one JSON per line

What this proves and what it does not: the page, its layout, its legends, its strip and its combo
grouping are all exercised end to end. The keyboard, the firmware and the wire format are not --
nothing here goes anywhere near them. A HUD that looks right under hudpoke can still be fed wrong
by a real keyboard, which is the whole reason the manual test exists.

Characters are turned into the same key events host/uskeys.py generates for the strip tests, so a
legend that composes there (accents, the Option layer, named keys) composes here too.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uskeys  # noqa: E402  (host/uskeys.py)


def key_events(text, gap_ms=90):
    """Text -> the messages a keyboard would have sent, with the gap each should wait first.

    Every character goes through uskeys, so what arrives is what the real decoder would have
    emitted for it -- including an accent arriving as one composed keyDown rather than two keys.
    """
    for ch in text:
        events = uskeys.events_for(ch)
        if events is None:
            print(f"hudpoke: no US-layout keystrokes spell {ch!r}, skipped", file=sys.stderr)
            continue
        for down in events:
            yield gap_ms, down
            # The page lights a key on the down and clears it on the up; sending only downs
            # leaves the strip correct but every key stuck lit.
            yield 0, dict(down, type="keyUp")


def messages(args):
    """-> (wait_ms_before, message) pairs, in the order they should be sent."""
    if args.layers is not None:
        ids = [int(x) for x in args.layers.split(",") if x.strip() != ""]
        yield 0, {"kind": "layers", "ids": ids}
    for pos in args.press or []:
        yield 0, {"kind": "press", "pos": pos}
        yield args.hold_ms, {"kind": "release", "pos": pos}
    for legend in args.legend or []:
        yield from key_events(legend, args.gap_ms)
    if args.type is not None:
        yield from key_events(args.type, args.gap_ms)
    if args.stdin:
        for line in sys.stdin:
            line = line.strip()
            if line:
                yield 0, json.loads(line)


def says_combos(pairs, combos):
    """Every `key` sent says whether combos are how it was typed (hudfeed COMBOS_DEFAULT): with
    --combos a z is the r+a chord, without it Alpha 2's key. A raw message from --stdin that
    already says keeps its own word."""
    for wait_ms, msg in pairs:
        if msg.get("kind") == "key" and not isinstance(msg.get("combos"), bool):
            msg = {**msg, "combos": combos}
        yield wait_ms, msg


async def main(args):
    try:
        import websockets
    except ImportError:
        sys.exit("python-websockets is required: pip install websockets (or make venv)")

    url = args.url or f"ws://127.0.0.1:{os.environ.get('ZMKHUD_PORT', '8766')}"
    sent = 0
    async with websockets.connect(url) as ws:
        for wait_ms, msg in says_combos(messages(args), args.combos):
            if wait_ms:
                await asyncio.sleep(wait_ms / 1000)
            await ws.send(json.dumps(msg, ensure_ascii=False))
            sent += 1
            if args.verbose:
                print(json.dumps(msg, ensure_ascii=False))
        # The send is fire-and-forget, so give the last one time out of the socket.
        await asyncio.sleep(0.1)
    print(f"hudpoke: sent {sent} message{'' if sent == 1 else 's'} to {url}", file=sys.stderr)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--url", help="hudfeed's WebSocket (default: ws://127.0.0.1:$ZMKHUD_PORT or 8766)")
    p.add_argument("--type", metavar="TEXT", help="type TEXT one character at a time")
    p.add_argument("--legend", action="append", metavar="L", help="type one legend (repeatable)")
    p.add_argument("--combos", action="store_true",
                   help="draw what is typed with the keymap's combos where it has them "
                        "(default: without, a letter on its layer's own key)")
    p.add_argument("--layers", metavar="IDS", help="set the active layer ids, comma separated ('' clears)")
    p.add_argument("--press", action="append", type=int, metavar="POS", help="press and release a position (repeatable)")
    p.add_argument("--gap-ms", type=int, default=90, help="wait between keystrokes (default 90)")
    p.add_argument("--hold-ms", type=int, default=120, help="how long a position stays pressed (default 120)")
    p.add_argument("--stdin", action="store_true", help="read raw messages from stdin, one JSON per line")
    p.add_argument("-v", "--verbose", action="store_true", help="print each message as it is sent")
    args = p.parse_args(argv)
    if not any((args.type, args.legend, args.layers is not None, args.press, args.stdin)):
        p.error("nothing to send: pass --type, --legend, --layers, --press or --stdin")
    return args


if __name__ == "__main__":
    try:
        asyncio.run(main(parse_args()))
    except KeyboardInterrupt:
        pass
