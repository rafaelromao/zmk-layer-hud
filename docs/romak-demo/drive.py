#!/usr/bin/env python3
"""A demo script, played into the running HUD and typed into the focused window, on one clock.

    python3 docs/romak-demo/drive.py docs/demo-romak.json            the HUD and the window
    python3 docs/romak-demo/drive.py docs/demo-romak.json --dry-run  print what the window would get

What `zmk-layer-hud poke --play` sends the HUD (host/play.py compiles the script on the feed's own
keymap), plus each keyDown typed for real with wtype (Wayland), so a screen recording shows the
keys on the HUD and the text appearing beside it at the same moments. A composed character (é, ã)
arrives as one keyDown and is typed whole; Enter, Tab and Backspace by name. wtype runs on a
thread of its own, in order, so a slow spawn never holds the HUD's clock back.

Needs a running HUD (the token in $ZMKHUD_STATE/token, as poke reads it), python-websockets, and
wtype on PATH; --dry-run needs only --keymap (zmk-layer-hud keymap --config ... --dump).
"""

import argparse
import asyncio
import json
import os
import queue
import shutil
import subprocess
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "host"))

import hudpoke  # noqa: E402
import play  # noqa: E402

NAMED = {"return": "Return", "tab": "Tab", "delete": "BackSpace", "escape": "Escape"}


def typed_by(msg):
    """The wtype arguments that type this message, or None when it types nothing in a window."""
    if msg.get("kind") != "key" or msg.get("type") != "keyDown":
        return None
    flags = msg.get("flags") or {}
    if flags.get("cmd") or flags.get("ctrl"):
        return None
    name = (msg.get("name") or "").lower()
    if name in NAMED:
        return ["-k", NAMED[name]]
    chars = msg.get("chars") or ""
    return ["--", chars] if chars else None


def text_of(args_):
    return "\n" if args_ == ["-k", "Return"] else "\t" if args_ == ["-k", "Tab"] else args_[-1]


class Typist(threading.Thread):
    """wtype, one call after another, in the order the clock asked for them."""

    def __init__(self):
        super().__init__(daemon=True)
        self.calls = queue.Queue()

    def run(self):
        while True:
            a = self.calls.get()
            if a is None:
                return
            subprocess.run(["wtype", *a], check=False)

    def type(self, a):
        self.calls.put(a)

    def finish(self):
        self.calls.put(None)
        self.join()


async def drive(args, compiled):
    try:
        import websockets
    except ImportError:
        sys.exit("drive: python-websockets is required (the HUD's venv has it: .venv/bin/python3)")
    if not shutil.which("wtype"):
        sys.exit("drive: wtype is not on PATH (pacman -S wtype)")
    typist = Typist()
    typist.start()
    url = hudpoke.feed_url(argparse.Namespace(url=args.url))
    async with hudpoke.connected(websockets, url, max_size=None) as ws:
        async def drain():
            async for _ in ws:
                pass
        drainer = asyncio.ensure_future(drain())

        async def send(msg):
            await ws.send(json.dumps(msg, ensure_ascii=False))
            a = typed_by(msg)
            if a:
                typist.type(a)
        try:
            await play.play(compiled.timeline, send, duration_ms=compiled.duration_ms)
        finally:
            drainer.cancel()
            typist.finish()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("script", help="a demo script (docs/demo-scripts.md)")
    p.add_argument("--keymap", help="the keymap message to type on (default: the one the feed replays)")
    p.add_argument("--url", help="the feed's WebSocket, as poke takes it")
    p.add_argument("--dry-run", action="store_true", help="print what would be typed, and play nothing")
    args = p.parse_args(argv)

    script = play.load(args.script)
    keymap = None
    if args.keymap:
        with open(args.keymap, encoding="utf-8") as f:
            keymap = json.load(f)
    elif args.dry_run:
        p.error("--dry-run needs --keymap")
    else:
        keymap = asyncio.run(feed_keymap(args))
    compiled = play.compile(script, keymap)
    for pr in compiled.problems:
        print(play.describe(pr, args.script), file=sys.stderr)
    if any(pr["level"] == "skip" for pr in compiled.problems):
        return 1
    if args.dry_run:
        sys.stdout.write("".join(text_of(a) for _, m in compiled.timeline if (a := typed_by(m))))
        return 0
    print(f"drive: {len(compiled.strokes)} keystrokes over {compiled.duration_ms / 1000:.1f} s", file=sys.stderr)
    asyncio.run(drive(args, compiled))
    return 0


async def feed_keymap(args):
    import websockets
    url = hudpoke.feed_url(argparse.Namespace(url=args.url))
    async with hudpoke.connected(websockets, url, max_size=None) as ws:
        km = await hudpoke.replayed_keymap(ws, 3.0)
    if km is None:
        sys.exit("drive: the feed sent no keymap; pass --keymap (zmk-layer-hud keymap --config ... --dump)")
    return km


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass
