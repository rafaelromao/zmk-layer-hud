#!/usr/bin/env python3
"""Host feed for the zmk-layer-hud pages. One source: the keyboard's own signal channel.

The firmware module sends framed messages (host/signal_frame.py) on a channel of its own — a
CDC-ACM serial interface over USB, GATT notifications over BLE — and this turns them into JSON
messages for the pages, over a WebSocket (Linux panel) or on stdout (macOS Hammerspoon host):

  {"kind":"keymap", ...}                  the keymap, built from the keymap-drawer YAML named in the
                                          config by host/keymap.py; re-sent whenever that file, the
                                          config or the layer dtsi changes
  {"kind":"layers","ids":[2,22]}          active ZMK layer ids (layer 0 omitted: always active)
  {"kind":"device","name":"Diamond"}      a keyboard was opened (the page's title)
  {"kind":"press","pos":13}               a key at ZMK position 13 went down (firmware `positions;`)
  {"kind":"release","pos":13}             ... and up again
Every message from a keyboard carries "device": its name, so the page can show which keyboard is
typing.
  {"kind":"key","type":"keyDown","name":"space","chars":" ","code":44,
   "flags":{"cmd":false,"ctrl":false,"alt":false,"shift":false,"fn":false},"repeat":false}
                                          every key press/release and modifier change, derived from
                                          the keyboard's own report snapshot: no OS event tap, no
                                          evdev, no layout guessing
A client sending {"kind":"close"} (the ✕ button) makes this script exit.

This used to read the keyboard's raw HID reports with hidapi, and the layer signal travelled inside
them as reserved keyboard-page usages. Linux maps that whole range to KEY_UNKNOWN rather than to
nothing, so every layer change arrived at the compositor as a phantom key press carrying whatever
modifiers were held — Gui held plus a layer change was enough to switch workspace. The signal has
its own channel now, and the keys frame carries the same (modifiers, usages) snapshot the report
used to, so everything below the reader — the layout tables, dead-key composition, the strip — is
unchanged.

Access: a USB keyboard needs no permission on macOS (/dev/cu.* is world-readable) and no Input
Monitoring, which also means Karabiner-Elements can no longer seize it out from under us; Linux
needs read access to the tty (contrib/udev/60-zmk-layer-hud.rules, or the dialout group). BLE needs
the keyboard bonded to this host, because the characteristic requires encryption.
Dependencies: pyserial, keymap-drawer (`make venv`); bleak for BLE; python-websockets for the
WebSocket.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import keymap as keymap_mod  # noqa: E402  (host/keymap.py)
import signal_frame  # noqa: E402  (host/signal_frame.py)

ZMK_VID, ZMK_PID = 0x1D50, 0x615E

# The module's own GATT service; the characteristic notifies one frame at a time.
BLE_SERVICE_UUID = "d1f0a7c2-5b47-4a1e-9c3d-6f2a8e10b7c1"
BLE_SIGNAL_UUID = "d1f0a7c3-5b47-4a1e-9c3d-6f2a8e10b7c1"
BLE_RETRY_MAX_S = 60  # longest gap between scans while none of them find anything

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


class SignalDecoder:
    """Turns the keyboard's signal messages into the pages' layers / key / flagsChanged messages.
    Pure and tested: feed(message, now_ms) -> list of messages; flush(now_ms) releases a held dead
    key. The messages come from signal_frame.Decoder, which owns the wire format; what is decided
    here is what the HUD should be told.

    A `keys` message is a snapshot of the keyboard report — the modifier byte and the usages held —
    so presses and releases are the difference between successive snapshots, exactly as they were
    when this read the report off the wire itself.

    A dead key (` ' ^ ~ ") is held back for DEAD_KEY_MS: if a letter follows in time, one keyDown
    with the composed character (á, ç, ñ…) is emitted instead of two, which is what the
    keymap-drawer legend says and what the host displays."""

    def __init__(self, compose=True, dead_key_ms=DEAD_KEY_MS):
        self.compose = compose
        self.dead_key_ms = dead_key_ms
        self.layers = None
        self.mods = 0
        self.held = []  # usages currently down, in the order the snapshot lists them
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

    def _keys(self, mods, keys, now_ms):
        out = []
        held = [k for k in keys if k]
        if mods != self.mods:
            self.mods = mods
            out.append({"kind": "key", "type": "flagsChanged", "name": "", "chars": "", "code": 0,
                        "flags": flags_of(mods), "repeat": False})
        flags = flags_of(mods)
        for k in self.held:
            if k not in held:
                out.extend(self._up(key_message(k, False, flags)))
        for k in held:
            if k not in self.held:
                out.extend(self._down(key_message(k, True, flags), now_ms))
        self.held = held
        return out

    def feed(self, msg, now_ms=None):
        kind = msg.get("kind")
        if kind == "layers":
            # Layer 0 is the default layer: always active, never part of the stack the HUD draws.
            # The firmware sends the bitmap as it is, so dropping it is this side's business.
            ids = [i for i in msg["ids"] if i]
            if ids == self.layers:
                return []  # the heartbeat, or a change that cancelled itself out
            self.layers = ids
            return [{"kind": "layers", "ids": ids}]
        if kind in ("press", "release"):
            return [dict(msg)]
        if kind == "keys":
            return self._keys(msg["mods"], msg["keys"], now_ms)
        return []  # a kind this version does not know: the firmware may add some


class Stream:
    """One open connection: the bytes a carrier delivers in, the pages' messages out. Both readers
    share it, because a BLE notification and a run of serial bytes decode the same way."""

    def __init__(self, emit, device, dead_key_ms=DEAD_KEY_MS, raw=False, log=print):
        self.emit, self.device, self.log, self.raw = emit, device, log, raw
        self.frames = signal_frame.Decoder()
        self.decoder = SignalDecoder(dead_key_ms=dead_key_ms)
        self.frames_seen = 0  # a port that never produces one is not ours

    def feed(self, chunk, now_ms):
        for msg in self.frames.feed(chunk):
            self.frames_seen += 1
            if self.raw:
                self.log(f"hudfeed: {self.device} {msg}")
            for out in self.decoder.feed(msg, now_ms):
                self._emit(out)

    def idle(self, now_ms):
        """Nothing arrived: let a dead key past its window out as the plain key it was."""
        for out in self.decoder.flush(now_ms):
            self._emit(out)

    def close(self):
        """The keyboard went away: whatever was held is released."""
        flags = flags_of(0)
        for k in self.decoder.held:
            self._emit(key_message(k, False, flags))
        self.decoder.held = []

    def _emit(self, msg):
        msg["device"] = self.device  # which keyboard this came from
        self.emit(msg)


class SerialReader:
    """Reads the keyboard's CDC-ACM interface on a thread and calls emit(message) for each decoded
    message. Rescans every `rescan` seconds, so unplugging and replugging just works.

    A composite ZMK device can expose more than one CDC interface (ZMK Studio, USB logging), and
    they are indistinguishable from their descriptors. So the port is not chosen, it is tried: a
    port that produces no valid frame within `probe_s` is dropped and not tried again until it goes
    away and comes back. The firmware announces on boot and beats every heartbeat-ms, so silence
    that long is decisive."""

    def __init__(self, emit, vid=ZMK_VID, pid=ZMK_PID, name=None, port=None, rescan=2.0, log=print,
                 raw=False, dead_key_ms=DEAD_KEY_MS, probe_s=8.0):
        self.emit, self.vid, self.pid, self.name, self.port = emit, vid, pid, name, port
        self.rescan, self.log, self.raw = rescan, log, raw
        self.dead_key_ms, self.probe_s = dead_key_ms, probe_s
        self._open = {}   # device path -> serial.Serial
        self._quiet = {}  # device path -> why we stopped reading it
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._scan_loop, name="serial-scan", daemon=True).start()

    def stop(self):
        self._stop.set()

    def _ports(self, list_ports):
        """(device path, product name) of every port that could be the keyboard."""
        out = []
        for info in list_ports.comports():
            if self.port is not None:
                if info.device != self.port:
                    continue
            else:
                if (info.vid, info.pid) != (self.vid, self.pid):
                    continue
                if self.name and self.name.lower() not in (info.product or "").lower():
                    continue
            # macOS lists the same interface twice: /dev/cu.* does not wait for carrier detect,
            # which is what a device that may be silent at first needs.
            if sys.platform == "darwin" and info.device.startswith("/dev/tty."):
                continue
            out.append((info.device, info.product or "?"))
        return out

    def _scan_loop(self):
        try:
            import serial  # noqa: F401
            from serial.tools import list_ports
        except ImportError:
            self.log("hudfeed: pyserial is required (make venv, or pip install pyserial)")
            return
        while not self._stop.is_set():
            try:
                ports = self._ports(list_ports)
            except Exception as e:  # a port vanishing mid-enumeration
                self.log(f"hudfeed: cannot list serial ports: {type(e).__name__}: {e}")
                ports = []
            live = {path for path, _ in ports}
            for path in [p for p in self._quiet if p not in live]:
                del self._quiet[path]  # unplugged: worth trying again when it returns
            for path, product in ports:
                if path in self._open or path in self._quiet:
                    continue
                self._start(path, product)
            self._stop.wait(self.rescan)

    def _start(self, path, product):
        import serial
        try:
            dev = serial.Serial(path, timeout=0.5, exclusive=False)
        except (OSError, serial.SerialException, ValueError) as e:
            self._quiet[path] = str(e)
            self.log(f"hudfeed: cannot open {product} ({path}): {e}")
            if sys.platform != "darwin":
                self.log("hudfeed: check tty permissions (contrib/udev/60-zmk-layer-hud.rules, "
                         "or add yourself to the dialout group)")
            return
        self._open[path] = dev
        threading.Thread(target=self._read_loop, args=(path, dev, product),
                         name="serial-read", daemon=True).start()

    def _read_loop(self, path, dev, product):
        import time
        stream = Stream(self.emit, product, self.dead_key_ms, self.raw, self.log)
        announced = False
        started = time.monotonic()
        try:
            while not self._stop.is_set():
                # Block on the first byte, then take whatever else has landed: read(n) would wait
                # out the whole timeout for a full buffer and put that latency on every keystroke.
                dev.timeout = self.dead_key_ms / 1000 if stream.decoder.pending else 0.5
                chunk = dev.read(1)
                if chunk:
                    waiting = dev.in_waiting
                    if waiting:
                        chunk += dev.read(waiting)
                now = int(time.monotonic() * 1000)
                if not chunk:
                    stream.idle(now)
                    if not stream.frames_seen and time.monotonic() - started > self.probe_s:
                        self._quiet[path] = "no frames"
                        self.log(f"hudfeed: {product} ({path}) is not the layer signal; "
                                 "leaving it alone")
                        return
                    continue
                stream.feed(chunk, now)
                if stream.frames_seen and not announced:
                    announced = True
                    self.log(f"hudfeed: reading {product} on {path}")
                    self.emit({"kind": "device", "name": product})
        except Exception as e:  # SerialException on unplug, and anything else the port throws
            if not self._stop.is_set():
                self.log(f"hudfeed: {product} gone ({type(e).__name__}: {e})")
        finally:
            try:
                dev.close()
            except Exception:
                pass
            self._open.pop(path, None)
            stream.close()


class BleReader:
    """Reads the module's GATT service on its own thread, where bleak's event loop lives. Each
    notification is exactly one frame.

    UNVERIFIED on hardware. Two things make BLE harder than the serial port. The characteristic
    requires encryption, so the keyboard has to be bonded to this host already — pair it with the
    OS first, this cannot do it. And a keyboard connected as a HID peripheral stops advertising, so
    scanning may never find it: on macOS give `ble: {address: ...}` in the config (CoreBluetooth's
    per-host peripheral UUID, which `python3 -m bleak` lists while the keyboard is disconnected),
    on Linux the MAC address works."""

    def __init__(self, emit, address=None, name=None, rescan=2.0, log=print, raw=False,
                 dead_key_ms=DEAD_KEY_MS):
        self.emit, self.address, self.name = emit, address, name
        self.rescan, self.log, self.raw, self.dead_key_ms = rescan, log, raw, dead_key_ms
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._run, name="ble-read", daemon=True).start()

    def stop(self):
        self._stop.set()

    def _run(self):
        try:
            import bleak  # noqa: F401
        except ImportError:
            self.log("hudfeed: bleak is not installed, so BLE keyboards are not read "
                     "(pip install bleak, or pass --no-ble to stop saying so)")
            return
        try:
            asyncio.run(self._loop())
        except Exception as e:
            self.log(f"hudfeed: BLE reader stopped ({type(e).__name__}: {e})")

    async def _find(self):
        from bleak import BleakScanner
        if self.address:
            return await BleakScanner.find_device_by_address(self.address, timeout=self.rescan)
        return await BleakScanner.find_device_by_filter(
            lambda d, ad: (BLE_SERVICE_UUID in (ad.service_uuids or [])
                           and (not self.name or self.name.lower() in (d.name or "").lower())),
            timeout=self.rescan)

    async def _loop(self):
        from bleak import BleakClient
        # A machine with no Bluetooth fails every scan, for ever. Saying so once
        # is useful; saying so every rescan fills run/hudfeed.log with one line
        # every couple of seconds and buries everything worth reading. So each
        # message is held until what it says changes, and a run of failures
        # backs off -- a keyboard that is not there is not found any sooner for
        # being asked more often.
        said = None
        wait = self.rescan
        while not self._stop.is_set():
            try:
                device = await self._find()
                failed = None
            except Exception as e:
                failed = f"BLE scan failed ({type(e).__name__}: {e})"
                device = None

            if device is None:
                note = failed or ("no BLE keyboard advertising the layer signal yet" + (
                    "" if self.address else "; a connected keyboard does not advertise, "
                                            "so it may need `ble: {address: ...}`"))
                if note != said:
                    said = note
                    self.log(f"hudfeed: {note}")
                await asyncio.sleep(wait)
                wait = min(wait * 2, BLE_RETRY_MAX_S)
                continue

            said, wait = None, self.rescan
            await self._session(BleakClient, device)

    async def _session(self, BleakClient, device):
        name = device.name or str(device.address)
        stream = Stream(self.emit, name, self.dead_key_ms, self.raw, self.log)
        gone = asyncio.Event()
        try:
            async with BleakClient(device, disconnected_callback=lambda _: gone.set()) as client:
                import time

                def on_notify(_sender, data):
                    stream.feed(bytes(data), int(time.monotonic() * 1000))

                await client.start_notify(BLE_SIGNAL_UUID, on_notify)
                self.log(f"hudfeed: reading {name} over BLE")
                self.emit({"kind": "device", "name": name})
                while not gone.is_set() and not self._stop.is_set():
                    # bleak has no idle callback, so the dead-key window is closed on a timer.
                    try:
                        await asyncio.wait_for(gone.wait(), timeout=self.dead_key_ms / 1000)
                    except asyncio.TimeoutError:
                        stream.idle(int(time.monotonic() * 1000))
        except Exception as e:
            self.log(f"hudfeed: {name} over BLE ({type(e).__name__}: {e})")
        finally:
            stream.close()


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
                 port=None, ble=True, ble_address=None, raw=False):
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
        ser = cfg.get("serial") or {}
        bt = cfg.get("ble") or {}
        feed_cfg = dict(keymap_mod.FEED_DEFAULTS)
        feed_cfg.update(cfg.get("feed") or {})
        self.keymap = keymap
        self.keys = keys
        rescan, dead_key_ms = float(feed_cfg["rescan_s"]), int(feed_cfg["dead_key_ms"])
        kb_name = name if name is not None else kb.get("name")
        self.reader = SerialReader(
            self._emit, log=log, raw=raw, rescan=rescan, dead_key_ms=dead_key_ms,
            vid=vid if vid is not None else int(kb.get("vid", ZMK_VID)),
            pid=pid if pid is not None else int(kb.get("pid", ZMK_PID)),
            name=kb_name, port=port if port is not None else ser.get("port"),
            probe_s=float(ser.get("probe_s", 8.0)))
        self.ble = None
        if ble and bt.get("enabled", True):
            self.ble = BleReader(self._emit, log=log, raw=raw, rescan=rescan, dead_key_ms=dead_key_ms,
                                 address=ble_address if ble_address is not None else bt.get("address"),
                                 name=kb_name)
        self.watcher = KeymapWatcher(self.source, self._emit, log) if (self.source is not None and keymap) else None

    def _emit(self, msg):
        if not self.keys and msg["kind"] == "key":
            return
        if msg["kind"] in ("press", "release"):
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
        if self.ble:
            self.ble.start()
        return self

    def stop(self):
        if self.watcher:
            self.watcher.stop()
        self.reader.stop()
        if self.ble:
            self.ble.stop()


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
    p.add_argument("--name", help="substring of the product name to select one keyboard (default: config `keyboard.name`)")
    p.add_argument("--serial", metavar="DEV", help="the keyboard's serial port, instead of finding it by vid/pid "
                                                   "(default: config `serial.port`)")
    p.add_argument("--no-ble", action="store_true", help="do not look for BLE keyboards")
    p.add_argument("--ble-address", help="address of the BLE keyboard (default: config `ble.address`); needed where a "
                                         "connected keyboard no longer advertises")
    p.add_argument("--debug", action="store_true", help="log layer messages to stderr")
    p.add_argument("--raw", action="store_true", help="DEBUG ONLY: log every decoded frame (includes your typing)")
    return p.parse_args(argv)


async def main(args):
    hub = Hub(stdout=args.stdout, debug=args.debug)
    loop = asyncio.get_running_loop()

    def emit(msg):
        loop.call_soon_threadsafe(lambda: asyncio.ensure_future(hub.send(msg)))

    Feed(emit, log=hub.log, config=args.config, keys=not args.no_keys, keymap=not args.no_keymap,
         vid=args.vid, pid=args.pid, name=args.name, port=args.serial,
         ble=not args.no_ble, ble_address=args.ble_address, raw=args.raw).start()

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
