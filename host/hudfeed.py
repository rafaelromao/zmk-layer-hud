#!/usr/bin/env python3
"""Host feed for the zmk-layer-hud pages: layer announcements from the keyboard, key events,
and the zmk-vim-mode daemon's decisions, as JSON messages over a WebSocket or on stdout.

The HUD (hud/index.html) and the typed-keys strip (hud/keys.html) are plain web pages. On
Linux, open them with `?ws=ws://127.0.0.1:8766` (host/linux/hud.sh does) and they connect
here. On macOS, Hammerspoon (host/macos/hud.lua) runs this script with `--stdout
--layers-only` and injects each line into its webviews itself.

Messages (one JSON object per message or line):
  {"kind":"layers","ids":[2,22]}          active ZMK layer ids, layer 0 omitted (always active)
  {"kind":"key","type":"keyDown","name":"space","chars":" ","code":57,
   "flags":{"cmd":false,"ctrl":false,"alt":false,"shift":false,"fn":false},"repeat":false}
  {"kind":"mode","code":1,"mode":"normal","reason":"nvim client"}
A client sending {"kind":"close"} (the ✕ button) makes this script exit.

Inputs:
  * raw HID   the keyboard's own keyboard report, read with hidapi. The firmware module
              (firmware/) puts one reserved usage per active layer (base + id) plus a commit
              usage into the report; only reports carrying the commit usage are decoded.
              Ordinary reports (your typing) are discarded unread and never logged.
              macOS: Input Monitoring for the process that runs this (Hammerspoon when started
              from hud.lua). Linux: hidraw access, granted by contrib/udev/60-zmk-layer-hud.rules
              (or the zmk-vim-mode daemon's identical rule).
  * evdev     every keyboard under /dev/input (Linux only; needs read access: the udev rule
              gives uaccess for the ZMK keyboard, `input` group membership covers the rest)
  * journal   `journalctl --user -u zmk-vim-mode -f` for the daemon's `msg=decision …` lines
              (Linux); falls back to polling `zmk-vim-mode status --json`.

Dependencies: python-hidapi (`import hid`; Arch: python-hidapi, macOS: brew install hidapi &&
pip install hidapi). Linux extras: python-evdev python-websockets.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

ZMK_VID, ZMK_PID = 0x1D50, 0x615E
BASE_USAGE, COMMIT_USAGE = 0xC0, 0xDF
KEYBOARD_REPORT_ID = 1
KEY_UNKNOWN = 240  # evdev code Linux gives usages it has no name for (our reserved usages)

BINARY = os.path.expanduser("~/.local/bin/zmk-vim-mode")


# ---------- layer signal decoding (mirrors firmware/src/layer_signal_policy.h) ----------

def decode_keys(keys, base=BASE_USAGE, commit=COMMIT_USAGE):
    """Key bytes of one keyboard report -> sorted layer ids, or None when the report does
    not carry the commit usage (then it is not an announcement and must be ignored)."""
    if commit not in keys:
        return None
    return sorted({k - base for k in keys if base < k < commit and k - base < 32})


def decode_report(report, base=BASE_USAGE, commit=COMMIT_USAGE, report_id=KEYBOARD_REPORT_ID):
    """One raw input report (report id first, as hidapi returns it) -> layer ids or None.
    ZMK's keyboard report is [id, modifiers, reserved, key...]; pass report_id=None for
    firmware without report ids ([modifiers, reserved, key...])."""
    data = bytes(report)
    if report_id is not None:
        if len(data) < 3 or data[0] != report_id:
            return None
        return decode_keys(data[3:], base, commit)
    if len(data) < 2:
        return None
    return decode_keys(data[2:], base, commit)


class LayerReader:
    """Reads the keyboard's raw HID reports on a thread and calls on_layers(ids) when the
    announced set changes. Rescans for the device every `rescan` seconds (hotplug)."""

    def __init__(self, on_layers, vid=ZMK_VID, pid=ZMK_PID, name=None, base=BASE_USAGE,
                 commit=COMMIT_USAGE, report_id=KEYBOARD_REPORT_ID, rescan=2.0, log=print):
        self.on_layers, self.vid, self.pid, self.name = on_layers, vid, pid, name
        self.base, self.commit, self.report_id, self.rescan, self.log = base, commit, report_id, rescan, log
        self.last = None
        self._open = {}
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._scan_loop, name="hid-scan", daemon=True).start()

    def stop(self):
        self._stop.set()

    def _paths(self, hid):
        seen, out = set(), []
        for info in hid.enumerate(self.vid, self.pid):
            if self.name and self.name.lower() not in (info.get("product_string") or "").lower():
                continue
            path = info["path"]
            if path in seen:  # macOS lists one entry per usage pair, all with the same path
                continue
            seen.add(path)
            out.append((path, info.get("product_string") or "?"))
        return out

    def _scan_loop(self):
        try:
            import hid
        except ImportError:
            self.log("hudfeed: python-hidapi is required for layer signals "
                     "(pip install hidapi / pacman -S python-hidapi; macOS also brew install hidapi)")
            return
        while not self._stop.is_set():
            for path, product in self._paths(hid):
                if path in self._open:
                    continue
                try:
                    dev = hid.device()
                    dev.open_path(path)
                except (OSError, IOError, ValueError) as e:
                    self.log(f"hudfeed: cannot open {product} ({path!r}): {e}")
                    self._open[path] = None  # do not retry every scan
                    continue
                self._open[path] = dev
                self.log(f"hudfeed: reading layer signals from {product}")
                threading.Thread(target=self._read_loop, args=(path, dev, product),
                                 name="hid-read", daemon=True).start()
            self._stop.wait(self.rescan)

    def _read_loop(self, path, dev, product):
        try:
            while not self._stop.is_set():
                report = dev.read(64, timeout_ms=500)
                if not report:
                    continue
                ids = decode_report(report, self.base, self.commit, self.report_id)
                if ids is None or ids == self.last:
                    continue
                self.last = ids
                self.on_layers(ids)
        except (OSError, IOError, ValueError) as e:
            self.log(f"hudfeed: {product} gone ({e})")
        finally:
            try:
                dev.close()
            except Exception:
                pass
            self._open.pop(path, None)


# ---------- key events (Linux evdev) ----------

def evdev_tables():
    from evdev import ecodes as E

    # US layout: evdev key -> (char, shifted char). The HUD resolves characters against the
    # keymap-drawer legends, so this only needs to match what the host layout produces.
    chars = {
        **{getattr(E, f"KEY_{c}"): (c.lower(), c) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
        E.KEY_1: ("1", "!"), E.KEY_2: ("2", "@"), E.KEY_3: ("3", "#"), E.KEY_4: ("4", "$"),
        E.KEY_5: ("5", "%"), E.KEY_6: ("6", "^"), E.KEY_7: ("7", "&"), E.KEY_8: ("8", "*"),
        E.KEY_9: ("9", "("), E.KEY_0: ("0", ")"), E.KEY_MINUS: ("-", "_"), E.KEY_EQUAL: ("=", "+"),
        E.KEY_LEFTBRACE: ("[", "{"), E.KEY_RIGHTBRACE: ("]", "}"), E.KEY_BACKSLASH: ("\\", "|"),
        E.KEY_SEMICOLON: (";", ":"), E.KEY_APOSTROPHE: ("'", '"'), E.KEY_GRAVE: ("`", "~"),
        E.KEY_COMMA: (",", "<"), E.KEY_DOT: (".", ">"), E.KEY_SLASH: ("/", "?"), E.KEY_SPACE: (" ", " "),
    }
    # Named keys, spelled the way hs.keycodes.map spells them (the pages share one table).
    named = {
        E.KEY_SPACE: "space", E.KEY_ENTER: "return", E.KEY_ESC: "escape", E.KEY_BACKSPACE: "delete",
        E.KEY_DELETE: "forwarddelete", E.KEY_TAB: "tab", E.KEY_LEFT: "left", E.KEY_RIGHT: "right",
        E.KEY_UP: "up", E.KEY_DOWN: "down", E.KEY_HOME: "home", E.KEY_END: "end",
        E.KEY_PAGEUP: "pageup", E.KEY_PAGEDOWN: "pagedown",
        **{getattr(E, f"KEY_F{n}"): f"f{n}" for n in range(1, 13)},
    }
    mods = {
        E.KEY_LEFTSHIFT: "shift", E.KEY_RIGHTSHIFT: "shift", E.KEY_LEFTCTRL: "ctrl", E.KEY_RIGHTCTRL: "ctrl",
        E.KEY_LEFTALT: "alt", E.KEY_RIGHTALT: "alt", E.KEY_LEFTMETA: "cmd", E.KEY_RIGHTMETA: "cmd",
    }
    return E, chars, named, mods


class KeyFeed:
    def __init__(self, broadcast, log=print):
        self.broadcast, self.log = broadcast, log
        self.held = {"cmd": 0, "ctrl": 0, "alt": 0, "shift": 0}
        self.E, self.CHARS, self.NAMED, self.MODS = evdev_tables()

    def flags(self):
        return {k: v > 0 for k, v in self.held.items()} | {"fn": False}

    def key_event(self, code, value):
        """One evdev key event -> HUD message (or None). value: 1 down, 0 up, 2 repeat."""
        E = self.E
        if code == KEY_UNKNOWN:
            return None  # the layer-signal usages, when the kernel forwards them
        if code in self.MODS:
            m = self.MODS[code]
            self.held[m] = max(0, self.held[m] + (1 if value == 1 else -1 if value == 0 else 0))
            return {"kind": "key", "type": "flagsChanged", "name": m, "chars": "", "code": code,
                    "flags": self.flags(), "repeat": False}
        typ = "keyUp" if value == 0 else "keyDown"
        chars = ""
        if code in self.CHARS and code != E.KEY_SPACE:
            chars = self.CHARS[code][1 if self.held["shift"] else 0]
        elif code == E.KEY_SPACE:
            chars = " "
        elif code == E.KEY_ENTER:
            chars = "\r"
        elif code == E.KEY_ESC:
            chars = "\x1b"
        name = self.NAMED.get(code) or (chars if chars and chars != " " else E.KEY.get(code, str(code)).replace("KEY_", "").lower())
        return {"kind": "key", "type": typ, "name": name, "chars": chars, "code": code,
                "flags": self.flags(), "repeat": value == 2}

    def keyboards(self):
        import evdev
        devs = []
        for path in evdev.list_devices():
            try:
                d = evdev.InputDevice(path)
            except PermissionError:
                self.log(f"hudfeed: no permission for {path} (add yourself to the input group or fix udev)")
                continue
            caps = d.capabilities().get(self.E.EV_KEY, [])
            if self.E.KEY_A in caps and self.E.KEY_Z in caps:
                devs.append(d)
        return devs

    async def read(self, dev):
        self.log(f"hudfeed: reading keys from {dev.path} ({dev.name})")
        try:
            async for ev in dev.async_read_loop():
                if ev.type == self.E.EV_KEY:
                    msg = self.key_event(ev.code, ev.value)
                    if msg:
                        await self.broadcast(msg)
        except OSError as e:
            self.log(f"hudfeed: {dev.path} gone ({e})")


# ---------- daemon decisions ----------

DECISION = re.compile(r'msg=decision mode=(\S+) code=(\d) reason="([^"]*)"')


class ModeFeed:
    def __init__(self, broadcast, log=print):
        self.broadcast, self.log = broadcast, log
        self.last = None

    async def apply_line(self, line):
        m = DECISION.search(line)
        if not m:
            return
        msg = {"kind": "mode", "code": int(m.group(2)), "mode": m.group(1), "reason": m.group(3)}
        if msg != self.last:
            self.last = msg
            await self.broadcast(msg)

    async def run(self):
        if sys.platform == "linux" and shutil.which("journalctl"):
            proc = await asyncio.create_subprocess_exec(
                "journalctl", "--user", "-u", "zmk-vim-mode", "-f", "-o", "cat", "-n", "50",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            self.log("hudfeed: following journalctl --user -u zmk-vim-mode")
            async for raw in proc.stdout:
                await self.apply_line(raw.decode("utf-8", "replace"))
            self.log("hudfeed: journalctl ended; polling status instead")
        await self.poll_status()

    async def poll_status(self):
        while True:
            try:
                out = subprocess.run([BINARY, "status", "--json"], capture_output=True, text=True, timeout=2).stdout
                st = json.loads(out)
                await self.apply_line(f'msg=decision mode={st["mode"]} code={st["code"]} reason="{st.get("reason", "")}"')
            except Exception:
                pass
            await asyncio.sleep(0.2)


# ---------- outputs ----------

class Hub:
    """Fans messages out to WebSocket clients and/or stdout; replays the cached layer set and
    mode to new clients so a page that (re)connects is right immediately."""

    def __init__(self, stdout=False, debug=False):
        self.stdout, self.debug = stdout, debug
        self.clients = set()
        self.cache = {}  # kind -> last message, for "layers" and "mode"

    def log(self, *a):
        print(*a, file=sys.stderr, flush=True)

    async def send(self, msg):
        if msg["kind"] in ("layers", "mode"):
            self.cache[msg["kind"]] = msg
        if self.debug and msg["kind"] != "key":
            self.log("hudfeed:", json.dumps(msg, ensure_ascii=False))
        data = json.dumps(msg, ensure_ascii=False)
        if self.stdout:
            print(data, flush=True)
        if self.clients:
            await asyncio.gather(*(c.send(data) for c in list(self.clients)), return_exceptions=True)

    async def handler(self, ws):
        self.clients.add(ws)
        try:
            for msg in self.cache.values():
                await ws.send(json.dumps(msg, ensure_ascii=False))
            async for raw in ws:
                try:
                    if json.loads(raw).get("kind") == "close":
                        self.log("hudfeed: close requested by the page")
                        os._exit(0)
                except json.JSONDecodeError:
                    pass
        finally:
            self.clients.discard(ws)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--stdout", action="store_true", help="print messages as JSON lines (macOS host)")
    p.add_argument("--no-ws", action="store_true", help="do not serve the WebSocket")
    p.add_argument("--port", type=int, default=int(os.environ.get("ZMKHUD_PORT", "8766")))
    p.add_argument("--layers-only", action="store_true", help="only the raw-HID layer feed (no keys, no mode)")
    p.add_argument("--no-keys", action="store_true", help="skip the evdev key feed")
    p.add_argument("--no-mode", action="store_true", help="skip the daemon decision feed")
    p.add_argument("--no-layers", action="store_true", help="skip the raw-HID layer feed")
    p.add_argument("--vid", type=lambda s: int(s, 0), default=ZMK_VID)
    p.add_argument("--pid", type=lambda s: int(s, 0), default=ZMK_PID)
    p.add_argument("--name", help="substring of the HID product string to select one keyboard")
    p.add_argument("--base", type=lambda s: int(s, 0), default=BASE_USAGE, help="base-usage of the firmware node")
    p.add_argument("--commit", type=lambda s: int(s, 0), default=COMMIT_USAGE, help="commit-usage of the firmware node")
    p.add_argument("--no-report-id", action="store_true", help="firmware without HID report ids")
    p.add_argument("--debug", action="store_true", help="log layer and mode messages to stderr")
    return p.parse_args(argv)


async def main(args):
    hub = Hub(stdout=args.stdout, debug=args.debug)
    loop = asyncio.get_running_loop()
    tasks = []

    if not args.no_layers:
        def on_layers(ids):
            loop.call_soon_threadsafe(lambda: asyncio.ensure_future(hub.send({"kind": "layers", "ids": ids})))
        reader = LayerReader(on_layers, vid=args.vid, pid=args.pid, name=args.name, base=args.base,
                             commit=args.commit, report_id=None if args.no_report_id else KEYBOARD_REPORT_ID,
                             log=hub.log)
        reader.start()

    if not args.layers_only and not args.no_keys:
        if sys.platform == "linux":
            try:
                feed = KeyFeed(hub.send, hub.log)
                devs = feed.keyboards()
                if not devs:
                    hub.log("hudfeed: no readable keyboard under /dev/input")
                tasks += [feed.read(d) for d in devs]
            except ImportError:
                hub.log("hudfeed: python-evdev is required for key events: sudo pacman -S python-evdev")
        else:
            hub.log("hudfeed: key events come from the host's own tap on this platform (hud.lua)")

    if not args.layers_only and not args.no_mode:
        tasks.append(ModeFeed(hub.send, hub.log).run())

    if not args.no_ws:
        try:
            import websockets
        except ImportError:
            sys.exit("python-websockets is required for the WebSocket (or pass --no-ws): sudo pacman -S python-websockets")
        async with websockets.serve(hub.handler, "127.0.0.1", args.port):
            hub.log(f"hudfeed: ws://127.0.0.1:{args.port}")
            await asyncio.gather(*tasks, asyncio.Event().wait())
    else:
        await asyncio.gather(*tasks, asyncio.Event().wait())


if __name__ == "__main__":
    try:
        asyncio.run(main(parse_args()))
    except KeyboardInterrupt:
        pass
