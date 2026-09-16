#!/usr/bin/env python3
"""Host feed for the zmk-layer-hud pages. One source: the keyboard's own HID reports.

Reads the keyboard's raw input reports with hidapi and turns them into JSON messages for the
pages, over a WebSocket (Linux panel) or on stdout (macOS Hammerspoon host):

  {"kind":"keymap", ...}                  the keymap, built from the keymap-drawer YAML named in the
                                          config by host/keymap.py; re-sent whenever that file, the
                                          config or the layer dtsi changes
  {"kind":"layers","ids":[2,22]}          active ZMK layer ids (layer 0 omitted: always active), from
                                          the firmware module's announcement inside the report
  {"kind":"device","name":"Diamond"}      a keyboard was opened (its HID product name; the page's title)
  {"kind":"press","pos":13}               a key at ZMK position 13 was pressed (firmware `positions;`)
  {"kind":"key","type":"keyDown","name":"space","chars":" ","code":44,
   "flags":{"cmd":false,"ctrl":false,"alt":false,"shift":false,"fn":false},"repeat":false}
                                          every key press/release and modifier change, straight from
                                          the report: no OS event tap, no evdev, no layout guessing
A client sending {"kind":"close"} (the ✕ button) makes this script exit.

Access: macOS needs Input Monitoring for the process running this (Hammerspoon when started from
hud.lua, else your terminal); Linux needs hidraw access (contrib/udev/60-zmk-layer-hud.rules).
Dependencies: hidapi and keymap-drawer (`make venv`); python-websockets for the WebSocket.

The report is ZMK's HKRO keyboard report: [report id 1, modifiers, reserved, key usages...].
Usages base..commit-1 are the layer announcement (host/keymap.py's `signal`), everything else is
a real key. Characters are derived from the usage with a US layout table; the keymap-drawer
legends are matched against them by the page.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import keymap as keymap_mod  # noqa: E402  (host/keymap.py)

ZMK_VID, ZMK_PID = 0x1D50, 0x615E
BASE_USAGE, COMMIT_USAGE = 0xC0, 0xDF
KEYBOARD_REPORT_ID = 1

# HID modifier byte bits -> HUD flag names (left/right collapse).
MOD_BITS = {0x01: "ctrl", 0x02: "shift", 0x04: "alt", 0x08: "cmd", 0x10: "ctrl", 0x20: "shift", 0x40: "alt", 0x80: "cmd"}

# Keyboard-page usage -> (char, shifted char), US layout.
CHARS = {}
for i, c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    CHARS[0x04 + i] = (c, c.upper())
for i, (c, s) in enumerate(zip("1234567890", "!@#$%^&*()")):
    CHARS[0x1E + i] = (c, s)
CHARS.update({0x2C: (" ", " "), 0x2D: ("-", "_"), 0x2E: ("=", "+"), 0x2F: ("[", "{"), 0x30: ("]", "}"),
              0x31: ("\\", "|"), 0x32: ("#", "~"), 0x33: (";", ":"), 0x34: ("'", '"'), 0x35: ("`", "~"),
              0x36: (",", "<"), 0x37: (".", ">"), 0x38: ("/", "?"), 0x64: ("\\", "|"),
              0x54: ("/", "/"), 0x55: ("*", "*"), 0x56: ("-", "-"), 0x57: ("+", "+"), 0x67: ("=", "=")})
for i, c in enumerate("1234567890"):
    CHARS[0x59 + i] = (c, c)  # keypad 1..9, 0 (0x59..0x62)
CHARS[0x63] = (".", ".")
# Usage -> name, spelled like Hammerspoon's hs.keycodes.map (the pages share one table).
NAMED = {0x28: "return", 0x29: "escape", 0x2A: "delete", 0x2B: "tab", 0x2C: "space", 0x4C: "forwarddelete",
         0x4F: "right", 0x50: "left", 0x51: "down", 0x52: "up", 0x4A: "home", 0x4D: "end", 0x4B: "pageup",
         0x4E: "pagedown", 0x39: "capslock", 0x46: "printscreen", 0x47: "scrolllock", 0x48: "pause",
         0x49: "insert", 0x58: "return", 0x65: "menu"}
for i in range(24):
    NAMED[0x3A + i if i < 12 else 0x68 + i - 12] = f"f{i + 1}"
CONTROL_CHARS = {0x28: "\r", 0x29: "\x1b", 0x2A: "\x7f", 0x2B: "\t", 0x58: "\r"}
# macOS Option layer of the US layout: (usage, shifted) -> character. Macros like &kp LS(LA(N2))
# for € land here. Dead keys of that layer (⌥e ⌥` ⌥i ⌥u ⌥n) are left out on purpose.
ALT_CHARS = {
    (0x1E, False): "¡", (0x1E, True): "⁄", (0x1F, False): "™", (0x1F, True): "€", (0x20, False): "£", (0x20, True): "‹",
    (0x21, False): "¢", (0x21, True): "›", (0x22, False): "∞", (0x22, True): "ﬁ", (0x23, False): "§", (0x23, True): "ﬂ",
    (0x24, False): "¶", (0x24, True): "‡", (0x25, False): "•", (0x25, True): "°", (0x26, False): "ª", (0x26, True): "·",
    (0x27, False): "º", (0x27, True): "‚", (0x2D, False): "–", (0x2D, True): "—", (0x2E, False): "≠", (0x2E, True): "±",
    (0x2F, False): "“", (0x2F, True): "”", (0x30, False): "‘", (0x30, True): "’", (0x31, False): "«", (0x31, True): "»",
    (0x33, False): "…", (0x33, True): "Ú", (0x34, False): "æ", (0x34, True): "Æ", (0x36, False): "≤", (0x36, True): "¯",
    (0x37, False): "≥", (0x37, True): "˘", (0x38, False): "÷", (0x38, True): "¿",
    (0x04, False): "å", (0x04, True): "Å", (0x05, False): "∫", (0x05, True): "ı", (0x06, False): "ç", (0x06, True): "Ç",
    (0x07, False): "∂", (0x07, True): "Î", (0x09, False): "ƒ", (0x09, True): "Ï", (0x0A, False): "©", (0x0A, True): "˝",
    (0x0B, False): "˙", (0x0B, True): "Ó", (0x0D, False): "∆", (0x0D, True): "Ô", (0x0E, False): "˚", (0x0E, True): "",
    (0x0F, False): "¬", (0x0F, True): "Ò", (0x10, False): "µ", (0x10, True): "Â", (0x12, False): "ø", (0x12, True): "Ø",
    (0x13, False): "π", (0x13, True): "∏", (0x14, False): "œ", (0x14, True): "Œ", (0x15, False): "®", (0x15, True): "‰",
    (0x16, False): "ß", (0x16, True): "Í", (0x17, False): "†", (0x17, True): "ˇ", (0x19, False): "√", (0x19, True): "◊",
    (0x1A, False): "∑", (0x1A, True): "„", (0x1B, False): "≈", (0x1B, True): "˛", (0x1C, False): "¥", (0x1C, True): "Á",
    (0x1D, False): "Ω", (0x1D, True): "¸",
}


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


POS_HI, POS_HI_N, POS_LO, POS_LO_N = 0xA5, 17, 0xB8, 8  # 0xB6/0xB7 skipped: Linux types ( ) for them


def decode_position(keys):
    """Key bytes -> physical key position when exactly one hi (0xA5..0xB5) and one lo
    (0xB8..0xBF) usage are present (firmware `positions;`), else None."""
    hi = [k - POS_HI for k in keys if POS_HI <= k < POS_HI + POS_HI_N]
    lo = [k - POS_LO for k in keys if POS_LO <= k < POS_LO + POS_LO_N]
    if len(hi) != 1 or len(lo) != 1:
        return None
    return hi[0] * POS_LO_N + lo[0]


def split_report(report, report_id=KEYBOARD_REPORT_ID):
    """-> (modifiers byte, key usages) of a keyboard report, or None for other reports."""
    data = bytes(report)
    if report_id is not None:
        if len(data) < 3 or data[0] != report_id:
            return None
        return data[1], data[3:]
    if len(data) < 2:
        return None
    return data[0], data[2:]


def flags_of(mods):
    f = {"cmd": False, "ctrl": False, "alt": False, "shift": False, "fn": False}
    for bit, name in MOD_BITS.items():
        if mods & bit:
            f[name] = True
    return f


def key_message(usage, down, flags):
    """A key press/release -> the HUD's key event."""
    chars = ""
    if flags["alt"] and not flags["cmd"] and not flags["ctrl"] and (usage, flags["shift"]) in ALT_CHARS:
        chars = ALT_CHARS[(usage, flags["shift"])]
    elif usage in CHARS:
        chars = CHARS[usage][1 if flags["shift"] else 0]
    elif usage in CONTROL_CHARS:
        chars = CONTROL_CHARS[usage]
    name = NAMED.get(usage) or (chars if chars and chars != " " else f"usage{usage:02x}")
    return {"kind": "key", "type": "keyDown" if down else "keyUp", "name": name, "chars": chars,
            "code": usage, "flags": flags, "repeat": False}


# Dead keys of the US-International layout, which is how accent macros type on the host:
# the dead key, then the letter, back to back (ZMK macro with wait-ms 0).
DEAD_KEYS = {"`": "̀", "'": "́", "^": "̂", "~": "̃", '"': "̈"}
# US-International special cases that are not the combining mark: ' + c is ç, not ć.
DEAD_KEY_SPECIAL = {("'", "c"): "ç", ("'", "C"): "Ç"}
DEAD_KEY_MS = 60  # a dead key followed by a letter within this window is one accented character


class ReportDecoder:
    """Turns the stream of keyboard reports into layers / key / flagsChanged messages. Pure and
    tested: feed(report, now_ms) -> list of messages; flush(now_ms) releases a held dead key.

    A dead key (` ' ^ ~ ") is held back for DEAD_KEY_MS: if a letter follows in time, one keyDown
    with the composed character (á, ç, ñ…) is emitted instead of two, which is what the
    keymap-drawer legend says and what the host displays."""

    def __init__(self, base=BASE_USAGE, commit=COMMIT_USAGE, report_id=KEYBOARD_REPORT_ID, compose=True,
                 dead_key_ms=DEAD_KEY_MS):
        self.base, self.commit, self.report_id, self.compose = base, commit, report_id, compose
        self.dead_key_ms = dead_key_ms
        self.layers = None
        self.mods = 0
        self.held = []  # real usages currently down, in press order
        self.pending = None  # (message, deadline_ms) of a dead key waiting for its letter

    def flush(self, now_ms=None):
        """Release a held dead key (its time ran out, or the reader idled) as the plain key it was."""
        if self.pending and (now_ms is None or now_ms >= self.pending["deadline"]):
            p, self.pending = self.pending, None
            return [p["down"]] + ([p["up"]] if p["up"] else [])
        return []

    def _down(self, msg, now_ms):
        """Dead-key composition: returns the messages to emit for a keyDown."""
        if not self.compose:
            return [msg]
        out = []
        if self.pending:
            p, self.pending = self.pending, None
            if now_ms is not None and now_ms <= p["deadline"] and len(msg["chars"]) == 1 and msg["chars"].isalpha():
                import unicodedata
                dead = p["down"]["chars"]
                composed = DEAD_KEY_SPECIAL.get((dead, msg["chars"])) or \
                    unicodedata.normalize("NFC", msg["chars"] + DEAD_KEYS[dead])
                if len(composed) == 1:
                    return [dict(msg, chars=composed, name=composed, composed=[p["down"]["code"], msg["code"]])]
            out.append(p["down"])
            if p["up"]:
                out.append(p["up"])
        if msg["chars"] in DEAD_KEYS and now_ms is not None:
            self.pending = {"down": msg, "up": None, "deadline": now_ms + self.dead_key_ms}
            return out
        out.append(msg)
        return out

    def _up(self, msg):
        """A dead key's release travels with its press: held back too, dropped when composed."""
        if self.pending and self.pending["up"] is None and self.pending["down"]["code"] == msg["code"]:
            self.pending["up"] = msg
            return []
        return [msg]

    def feed(self, report, now_ms=None):
        parts = split_report(report, self.report_id)
        if parts is None:
            return []
        mods, keys = parts
        out = []
        ids = decode_keys(keys, self.base, self.commit)
        if ids is not None and ids != self.layers:
            self.layers = ids
            out.append({"kind": "layers", "ids": ids})
        pos = decode_position(keys)
        if pos is not None:
            out.append({"kind": "press", "pos": pos})
        real = [k for k in keys if k and not (self.base <= k <= self.commit)
                and not (POS_HI <= k < POS_HI + POS_HI_N) and not (POS_LO <= k < POS_LO + POS_LO_N)]
        if mods != self.mods:
            self.mods = mods
            flags = flags_of(mods)
            out.append({"kind": "key", "type": "flagsChanged", "name": "", "chars": "", "code": 0,
                        "flags": flags, "repeat": False})
        flags = flags_of(mods)
        for k in self.held:
            if k not in real:
                out.extend(self._up(key_message(k, False, flags)))
        for k in real:
            if k not in self.held:
                out.extend(self._down(key_message(k, True, flags), now_ms))
        self.held = real
        return out


class KeyboardReader:
    """Reads the keyboard's raw HID reports on a thread and calls emit(message) for every
    decoded message. Rescans for the device every `rescan` seconds (hotplug)."""

    def __init__(self, emit, vid=ZMK_VID, pid=ZMK_PID, name=None, base=BASE_USAGE, commit=COMMIT_USAGE,
                 report_id=KEYBOARD_REPORT_ID, rescan=2.0, log=print, raw=False, dead_key_ms=DEAD_KEY_MS):
        self.emit, self.vid, self.pid, self.name = emit, vid, pid, name
        self.base, self.commit, self.report_id, self.rescan, self.log = base, commit, report_id, rescan, log
        self.raw = raw  # debug only: dumps every report, i.e. also what you type
        self.dead_key_ms = dead_key_ms
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
            self.log("hudfeed: hidapi is required (make venv, or pip install hidapi; macOS also brew install hidapi)")
            return
        if sys.platform == "darwin":
            # Since hidapi 0.12 the macOS backend opens devices exclusively (seizing them), which
            # macOS refuses for a keyboard it is using: "open failed" although IOHIDDeviceOpen
            # succeeds. The Python binding does not expose the switch, but the extension exports
            # the C symbol, so flip it through ctypes before the first open.
            try:
                import ctypes
                ctypes.CDLL(hid.__file__).hid_darwin_set_open_exclusive(0)
            except (OSError, AttributeError) as e:
                self.log(f"hudfeed: could not disable hidapi's exclusive open ({e}); opens may fail")
        while not self._stop.is_set():
            for path, product in self._paths(hid):
                if path in self._open:
                    continue
                try:
                    dev = hid.device()
                    dev.open_path(path)
                except (OSError, IOError, ValueError) as e:
                    self.log(f"hudfeed: cannot open {product} ({path!r}): {e}")
                    if sys.platform == "darwin":
                        self.log("hudfeed: on macOS this means the app running Python (your terminal, or "
                                 "Hammerspoon) lacks Input Monitoring (System Settings > Privacy & Security), "
                                 "or Karabiner-Elements modifies this keyboard's events and has seized it "
                                 "(Karabiner > Devices: untick it)")
                    else:
                        self.log("hudfeed: check hidraw permissions (contrib/udev/60-zmk-layer-hud.rules)")
                    self._open[path] = None  # do not retry every scan
                    continue
                self._open[path] = dev
                self.log(f"hudfeed: reading {product}")
                self.emit({"kind": "device", "name": product})
                threading.Thread(target=self._read_loop, args=(path, dev, product),
                                 name="hid-read", daemon=True).start()
            self._stop.wait(self.rescan)

    def _read_loop(self, path, dev, product):
        import time
        decoder = ReportDecoder(self.base, self.commit, self.report_id, dead_key_ms=self.dead_key_ms)
        try:
            while not self._stop.is_set():
                # Wake early while a dead key waits, so it is released on time when no letter follows.
                report = dev.read(64, timeout_ms=self.dead_key_ms if decoder.pending else 500)
                now = int(time.monotonic() * 1000)
                if not report:
                    for msg in decoder.flush(now):
                        self.emit(msg)
                    continue
                if self.raw:
                    self.log(f"hudfeed: {product} raw {bytes(report).hex(' ')}")
                for msg in decoder.feed(report, now):
                    self.emit(msg)
        except (OSError, IOError, ValueError) as e:
            self.log(f"hudfeed: {product} gone ({e})")
        finally:
            try:
                dev.close()
            except Exception:
                pass
            self._open.pop(path, None)
            # The keyboard went away: whatever was held is released.
            flags = flags_of(0)
            for k in decoder.held:
                self.emit(key_message(k, False, flags))


# ---------- keymap (config + keymap-drawer YAML, live reload) ----------

class KeymapWatcher(threading.Thread):
    """Emits the keymap message on start and whenever one of its source files changes. A broken
    edit is logged and the last good keymap stays on screen."""

    def __init__(self, source, emit, log=print, poll=1.0):
        super().__init__(name="keymap-watch", daemon=True)
        self.source, self.emit, self.log, self.poll = source, emit, log, poll
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def announce(self, msg):
        self.log(f"hudfeed: keymap {msg['source']}: {len(msg['layout']['keys'])} keys, "
                 f"{len(msg['layers'])} drawer layers, {len(msg['zmk_layers'])} ZMK layers")
        self.emit(msg)

    def run(self):
        # The caller already loaded (and validated) the keymap, which also snapshotted the file
        # timestamps: send that first, then watch for edits.
        if self.source.message is not None:
            self.announce(self.source.message)
        while not self._stop.is_set():
            if self.source.changed():
                try:
                    self.announce(self.source.load())
                except keymap_mod.KeymapError as e:
                    self.log(f"hudfeed: keymap not (re)loaded: {e}")
                except Exception as e:  # a half-saved YAML, a typo in the config
                    self.log(f"hudfeed: keymap not (re)loaded: {type(e).__name__}: {e}")
            self._stop.wait(self.poll)


# ---------- the whole feed, embeddable ----------

class Feed:
    """Everything a host needs: load the config, watch the keymap, read the keyboard(s). `emit`
    is called from worker threads with each message; hosts marshal it to their UI thread.
    Used in-process by host/macos/panel.py and by main() below for the WebSocket/stdout modes."""

    def __init__(self, emit, log=print, config=None, keys=True, keymap=True, vid=None, pid=None, name=None,
                 base=None, commit=None, report_id=KEYBOARD_REPORT_ID, raw=False):
        self.emit, self.log = emit, log
        self.source, cfg = None, {}
        try:
            self.source = keymap_mod.KeymapSource(config)
            self.source.load()  # fail early with a readable reason
            cfg = self.source.cfg
        except keymap_mod.KeymapError as e:
            log(f"hudfeed: {e}")
            if keymap:
                log("hudfeed: continuing without a keymap; the pages show nothing until one arrives")
        except Exception as e:
            log(f"hudfeed: config/keymap failed: {type(e).__name__}: {e}")
        kb = cfg.get("keyboard") or {}
        sig = cfg.get("signal") or {}
        feed_cfg = dict(keymap_mod.FEED_DEFAULTS)
        feed_cfg.update(cfg.get("feed") or {})
        self.keymap = keymap
        self.keys = keys
        self.reader = KeyboardReader(
            self._emit, log=log, raw=raw, report_id=report_id,
            rescan=float(feed_cfg["rescan_s"]), dead_key_ms=int(feed_cfg["dead_key_ms"]),
            vid=vid if vid is not None else int(kb.get("vid", ZMK_VID)),
            pid=pid if pid is not None else int(kb.get("pid", ZMK_PID)),
            name=name if name is not None else kb.get("name"),
            base=base if base is not None else int(sig.get("base", BASE_USAGE)),
            commit=commit if commit is not None else int(sig.get("commit", COMMIT_USAGE)))
        self.watcher = KeymapWatcher(self.source, self._emit, log) if (self.source is not None and keymap) else None

    def _emit(self, msg):
        if not self.keys and msg["kind"] == "key":
            return
        if msg["kind"] == "press":
            self._check_position(msg["pos"])
        self.emit(msg)

    def _check_position(self, pos):
        """A position the keymap cannot place means the config lacks (or has a wrong) `positions:`
        map for a drawer whose key order differs from the keymap's. Say so once."""
        km = self.source.message if self.source else None
        if not km or getattr(self, "_pos_warned", False):
            return
        if str(pos) not in km["positions"]:
            self._pos_warned = True
            self.log(f"hudfeed: key position {pos} is not in the keymap's {len(km['layout']['keys'])} drawer keys; "
                     "add `positions:` (the ZMK position of each drawer key) to the config, "
                     "see config/diamond.yaml")

    def start(self):
        if self.watcher:
            self.watcher.start()
        self.reader.start()
        return self

    def stop(self):
        if self.watcher:
            self.watcher.stop()
        self.reader.stop()


# ---------- outputs ----------

class Hub:
    """Fans messages out to WebSocket clients and/or stdout; replays the cached keymap and layer
    set to new clients so a page that (re)connects is right immediately."""

    def __init__(self, stdout=False, debug=False):
        self.stdout, self.debug = stdout, debug
        self.clients = set()
        self.cache = {}  # kind -> last message, for "keymap" and "layers"

    def log(self, *a):
        print(*a, file=sys.stderr, flush=True)

    async def send(self, msg):
        if msg["kind"] in ("keymap", "layers", "device"):
            self.cache[msg["kind"]] = msg
        if self.debug and msg["kind"] in ("layers", "press"):
            import time
            self.log(f"hudfeed: {time.monotonic() * 1000:.0f}ms", json.dumps(msg, ensure_ascii=False))
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
    p.add_argument("--config", help=f"zmk-layer-hud config (default: $ZMKHUD_CONFIG or {keymap_mod.DEFAULT_CONFIG})")
    p.add_argument("--stdout", action="store_true", help="print messages as JSON lines (macOS host)")
    p.add_argument("--no-ws", action="store_true", help="do not serve the WebSocket")
    p.add_argument("--port", type=int, default=int(os.environ.get("ZMKHUD_PORT", "8766")))
    p.add_argument("--no-keymap", action="store_true", help="skip the keymap feed (pages keep whatever they have)")
    p.add_argument("--no-keys", action="store_true", help="send layers only, no key events")
    p.add_argument("--vid", type=lambda s: int(s, 0), help="keyboard vendor id (default: config `keyboard.vid`, else ZMK's)")
    p.add_argument("--pid", type=lambda s: int(s, 0), help="keyboard product id (default: config `keyboard.pid`, else ZMK's)")
    p.add_argument("--name", help="substring of the HID product string to select one keyboard (default: config `keyboard.name`)")
    p.add_argument("--base", type=lambda s: int(s, 0), help="base-usage of the firmware node (default: config `signal.base`, else 0xC0)")
    p.add_argument("--commit", type=lambda s: int(s, 0), help="commit-usage of the firmware node (default: config `signal.commit`, else 0xDF)")
    p.add_argument("--no-report-id", action="store_true", help="firmware without HID report ids")
    p.add_argument("--debug", action="store_true", help="log layer messages to stderr")
    p.add_argument("--raw", action="store_true", help="DEBUG ONLY: dump every HID report as hex (includes your typing)")
    return p.parse_args(argv)


async def main(args):
    hub = Hub(stdout=args.stdout, debug=args.debug)
    loop = asyncio.get_running_loop()

    def emit(msg):
        loop.call_soon_threadsafe(lambda: asyncio.ensure_future(hub.send(msg)))

    Feed(emit, log=hub.log, config=args.config, keys=not args.no_keys, keymap=not args.no_keymap,
         vid=args.vid, pid=args.pid, name=args.name, base=args.base, commit=args.commit,
         report_id=None if args.no_report_id else KEYBOARD_REPORT_ID, raw=args.raw).start()

    if not args.no_ws:
        try:
            import websockets
        except ImportError:
            sys.exit("python-websockets is required for the WebSocket (or pass --no-ws): pip install websockets")
        async with websockets.serve(hub.handler, "127.0.0.1", args.port):
            hub.log(f"hudfeed: ws://127.0.0.1:{args.port}")
            await asyncio.Event().wait()
    else:
        await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main(parse_args()))
    except KeyboardInterrupt:
        pass
