#!/usr/bin/env python3
"""Host feed for the zmk-layer-hud pages. One source: the keyboard's own signal channel.

The firmware module sends framed messages (host/signal_frame.py) on a channel of its own — a
CDC-ACM serial interface over USB, GATT notifications over BLE — and this turns them into JSON
messages for the pages, over a WebSocket (Linux panel) or on stdout (macOS Hammerspoon host):

  {"kind":"keymap", ...}                  the keymap, built by host/keymap.py from the config and
                                          the definitions import wrote beside it; re-sent whenever
                                          either changes (a sync rewrites the definitions)
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

The socket takes a token in its URL path, ws://127.0.0.1:8766/<token>, one per run: the typing it
carries is the user's, and a browser applies no same-origin rule to WebSockets, so without one any
web page open on the machine could read it. The feed keeps the token in $STATE/token (0600) for
`zmk-layer-hud poke`; the panels give it to their own pages. A client without it is refused before
the upgrade (HTTP 403), as is one from an Origin that is not a local page (Hub.process_request).

Two sources, because they failed in different ways and the fixes are different.

Layers and positions come from the channel above. They used to travel inside the keyboard report as
reserved keyboard-page usages, on the premise that no OS maps them; Linux maps that whole range to
KEY_UNKNOWN, so every layer change reached the compositor as a phantom key press carrying whatever
modifiers were held — Gui held plus a layer change was enough to switch workspace.

What is typed comes from the keyboard's HID reports, read here (hidapi on macOS, the kernel's own
/dev/hidrawN on Linux, where hidapi's wheel talks libusb and cannot), which is how this
always worked and was never the thing at fault. The firmware used to be able to send the same
snapshot, and it dropped keys: the report can only be read after ZMK has updated it, so the read
was deferred to a k_work item, and k_work_submit on an already-queued item does nothing -- a press
and its release arriving together collapsed into one snapshot and the key was never seen held. That
path is gone from the module. ZMK already emits one report per change, so reading them here cannot
lose a transition.

Access: reading the reports needs Input Monitoring on macOS for whatever runs this (your terminal,
or Hammerspoon), and a keyboard Karabiner-Elements modifies is seized by it; Linux needs hidraw
access. The signal channel needs neither — /dev/cu.* is world-readable, and Linux only needs the
tty. Both grants are in contrib/udev/60-zmk-layer-hud.rules. --no-hid-keys drops the strip and the
permission with it. BLE needs the keyboard bonded to this host, because the characteristic requires
encryption.
Dependencies: pyserial (`make venv`); bleak for BLE; python-websockets for the WebSocket; hidapi on
macOS only -- Linux reads /dev/hidrawN itself (HidrawReader), because the wheel's Linux backend is
libusb, which wants /dev/bus/usb and detaches the kernel HID driver; PyYAML (or yq) for the config;
jeepney on Linux only, for the accessibility bus, where a password field with focus is seen
(host/a11y.py); under Hyprland the session lock is seen on its control socket (host/sessionlock.py).
Not keymap-drawer: the drawing comes ready in the definitions `zmk-layer-hud import` wrote.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hmac
import http
import json
import os
import re
import secrets
import sys
import termios
import threading
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import a11y  # noqa: E402  (host/a11y.py)
import sessionlock  # noqa: E402
import keymap as keymap_mod  # noqa: E402  (host/keymap.py)
import panelstate  # noqa: E402  (host/panelstate.py)
import session as session_mod  # noqa: E402  (host/session.py)
import signal_frame  # noqa: E402  (host/signal_frame.py)
from hiddecode import DEAD_KEY_MS, SignalDecoder  # noqa: E402

ZMK_VID, ZMK_PID = 0x1D50, 0x615E

# The module's own GATT service; the characteristic notifies one frame at a time. They are the
# firmware's, ZLS_BT_SERVICE_UUID and ZLS_BT_SIGNAL_UUID in firmware/src/signal_gatt.c, and
# host/gatt_uuid_test.py holds the two sides equal.
BLE_SERVICE_UUID = "d1f0a7c2-6b3e-4f8a-9c21-5e7b4a0d9f31"
BLE_SIGNAL_UUID = "d1f0a7c3-6b3e-4f8a-9c21-5e7b4a0d9f31"
BLE_RETRY_MAX_S = 60  # longest gap between scans while none of them find anything


class Stream:
    """One open connection: what a carrier delivers in, the pages' messages out. Every reader shares
    it: a BLE notification and a run of serial bytes decode the same way, and a HID report the
    host reads itself arrives in the same shape (message)."""

    def __init__(self, emit, device, dead_key_ms=DEAD_KEY_MS, raw=False, log=print, altgr=False):
        # `raw` logs every decoded message: True, or a callable asked at each one, which is how the
        # feed keeps the log quiet while a secret is being typed (SecureInput): the log is the
        # typing, and the gate sits downstream of here.
        self.emit, self.device, self.log, self.raw = emit, device, log, raw
        self.frames = signal_frame.Decoder()
        self.decoder = SignalDecoder(dead_key_ms=dead_key_ms, altgr=altgr)
        self.frames_seen = 0  # a port that never produces one is not ours

    def feed(self, chunk, now_ms):
        """Bytes off a carrier: find the frames in them and hand each one on."""
        for msg in self.frames.feed(chunk):
            self.frames_seen += 1
            self.message(msg, now_ms)

    def message(self, msg, now_ms):
        """One decoded message, wherever it came from -- a frame off the wire, or a HID report the
        host read itself. Both arrive here in the same shape, so one decoder serves both."""
        if self.raw and (self.raw is True or self.raw()):
            self.log(f"hudfeed: {self.device} {msg}")
        for out in self.decoder.feed(msg, now_ms):
            self._emit(out)

    def idle(self, now_ms):
        """Nothing arrived: let a dead key past its window out as the plain key it was."""
        for out in self.decoder.flush(now_ms):
            self._emit(out)

    def close(self):
        """The keyboard went away: a dead key waiting for its letter is the plain key it was, and
        then everything held comes up, modifiers too -- as the report a keyboard sends when every
        key is let go, so the decoder says it the way it says any release."""
        for out in self.decoder.flush():
            self._emit(out)
        for out in self.decoder.feed({"kind": "keys", "mods": 0, "keys": []}):
            self._emit(out)

    def resync(self):
        """Forget what the pages were last told about the layers.

        Only sent on a change, so once something else has drawn on the pages -- an injected
        message, a showcase take -- the keyboard agreeing with itself is silence, and the wrong
        picture stays up. Forgetting makes the next heartbeat a change again, so the truth is
        back within heartbeat-ms without anyone having to touch a layer key."""
        self.decoder.layers = None

    def _emit(self, msg):
        msg["device"] = self.device  # which keyboard this came from
        self.emit(msg)


def take_port(dev, log):
    """The port for this process alone (TIOCEXCL): with `positions;` on, what it carries is which
    key went down and when, and on macOS /dev/cu.* is open to every user. Only once the port is
    known to be the signal -- a composite board's other CDC ports (ZMK Studio, USB logging) are
    tried too, and stay open to the tools that want them. pyserial's `exclusive=True` is an
    advisory flock, which stops nothing. A later open() by another process gets EBUSY until the
    feed closes the port; the kernel clears the flag with the last close."""
    try:
        import fcntl
        import termios
        fcntl.ioctl(dev.fileno(), termios.TIOCEXCL)
    except (ImportError, AttributeError, OSError) as e:
        log(f"hudfeed: could not take the port for this process alone ({e})")


class SerialReader:
    """Reads the keyboard's CDC-ACM interface on a thread and calls emit(message) for each decoded
    message. Rescans every `rescan` seconds, so unplugging and replugging just works.

    A composite ZMK device can expose more than one CDC interface (ZMK Studio, USB logging), and
    they are indistinguishable from their descriptors. So the port is not chosen, it is tried: a
    port that produces no valid frame within `probe_s` is dropped and not tried again until it goes
    away and comes back. The firmware announces on boot and beats every heartbeat-ms, so silence
    that long is decisive."""

    def __init__(self, emit, vid=ZMK_VID, pid=ZMK_PID, name=None, port=None, rescan=2.0, log=print,
                 raw=False, dead_key_ms=DEAD_KEY_MS, probe_s=8.0, altgr=False):
        self.emit, self.vid, self.pid, self.name, self.port = emit, vid, pid, name, port
        self.rescan, self.log, self.raw = rescan, log, raw
        self.dead_key_ms, self.probe_s, self.altgr = dead_key_ms, probe_s, altgr
        self._open = {}   # device path -> serial.Serial
        self._streams = set()  # live Streams, for resync()
        self._quiet = {}  # device path -> why we stopped reading it
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._scan_loop, name="serial-scan", daemon=True).start()

    def stop(self):
        self._stop.set()

    def resync(self):
        for stream in list(self._streams):
            stream.resync()

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
        stream = Stream(self.emit, product, self.dead_key_ms, self.raw, self.log, self.altgr)
        self._streams.add(stream)
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
                    take_port(dev, self.log)
        except Exception as e:  # SerialException on unplug, and anything else the port throws
            if not self._stop.is_set():
                self.log(f"hudfeed: {product} gone ({type(e).__name__}: {e})")
        finally:
            try:
                dev.close()
            except Exception:
                pass
            self._open.pop(path, None)
            self._streams.discard(stream)
            stream.close()


class BleReader:
    """Reads the module's GATT service on its own thread, where bleak's event loop lives. Each
    notification is exactly one frame.

    Two things make BLE harder than the serial port. The characteristic requires encryption, so
    the keyboard has to be bonded to this host already — pair it with the OS first, this cannot do
    it. And a keyboard connected as a HID peripheral stops advertising, so scanning never finds it
    while it is in use. On macOS the connected keyboard is asked of CoreBluetooth instead
    (_connected_macos); on Linux give `ble: {address: ...}`, its MAC address."""

    def __init__(self, emit, address=None, name=None, rescan=2.0, log=print, raw=False,
                 dead_key_ms=DEAD_KEY_MS, altgr=False):
        self.emit, self.address, self.name = emit, address, name
        self.rescan, self.log, self.raw, self.dead_key_ms = rescan, log, raw, dead_key_ms
        self.altgr = altgr
        self._streams = set()  # live Streams, for resync()
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._run, name="ble-read", daemon=True).start()

    def stop(self):
        self._stop.set()

    def resync(self):
        for stream in list(self._streams):
            stream.resync()

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
        if sys.platform == "darwin":
            device = await self._connected_macos()
            if device is not None:
                return device
        if self.address:
            return await BleakScanner.find_device_by_address(self.address, timeout=self.rescan)
        return await BleakScanner.find_device_by_filter(
            lambda d, ad: (BLE_SERVICE_UUID in (ad.service_uuids or [])
                           and (not self.name or self.name.lower() in (d.name or "").lower())),
            timeout=self.rescan)

    async def _connected_macos(self):
        """The keyboard macOS already has connected, or None. A bonded keyboard in use is connected
        to the system as a HID peripheral and so no longer advertises: no scan finds it, however
        long it runs, but CoreBluetooth hands it over by service. Wrapped the way bleak's own
        scanner wraps what it finds (corebluetooth/scanner.py), so BleakClient takes it as is."""
        from bleak.backends.device import BLEDevice
        try:
            from bleak.backends.corebluetooth.CentralManagerDelegate import CentralManagerDelegate
            from CoreBluetooth import CBUUID
        except ImportError:
            return None
        if getattr(self, "_manager", None) is None:
            manager = CentralManagerDelegate()
            await manager.wait_until_ready()
            self._manager = manager
        central = self._manager.central_manager

        def wanted(p):
            ident, name = p.identifier().UUIDString(), p.name() or ""
            if self.address:
                return ident.lower() == self.address.lower()
            return not self.name or self.name.lower() in name.lower()

        # Our service first. macOS answers from what it has discovered on the peripheral, which may
        # not include a service of ours until something has asked, so HID (0x1812) is the fallback:
        # every ZMK keyboard has it, and start_notify says soon enough whether ours is there too.
        for uuid in (BLE_SERVICE_UUID, "1812"):
            found = [p for p in central.retrieveConnectedPeripheralsWithServices_([CBUUID.UUIDWithString_(uuid)])
                     if wanted(p)]
            if found:
                p = found[0]
                return BLEDevice(p.identifier().UUIDString(), p.name(), (p, self._manager))
        return None

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
                note = failed or ("no BLE keyboard with the layer signal connected or advertising yet" + (
                    "" if self.address or sys.platform == "darwin" else
                    "; a connected keyboard does not advertise, so it may need `ble: {address: ...}`"))
                if note != said:
                    said = note
                    self.log(f"hudfeed: {note}")
                await asyncio.sleep(wait)
                wait = min(wait * 2, BLE_RETRY_MAX_S)
                continue

            failed = await self._session(BleakClient, device)
            if failed is None:
                said, wait = None, self.rescan  # it was read: look again straight away when it goes
                continue
            # Connected but never read (no service of ours on it, notify refused): the same
            # keyboard is found again at once, so this backs off and says it once, like a scan.
            if failed != said:
                said = failed
                self.log(f"hudfeed: {failed}")
            await asyncio.sleep(wait)
            wait = min(wait * 2, BLE_RETRY_MAX_S)

    async def _session(self, BleakClient, device):
        """Reads one connection until it drops. -> None once it has been read, else why not."""
        name = device.name or str(device.address)
        reading = False
        stream = Stream(self.emit, name, self.dead_key_ms, self.raw, self.log, self.altgr)
        self._streams.add(stream)
        gone = asyncio.Event()
        try:
            async with BleakClient(device, disconnected_callback=lambda _: gone.set()) as client:
                import time

                def on_notify(_sender, data):
                    stream.feed(bytes(data), int(time.monotonic() * 1000))

                await client.start_notify(BLE_SIGNAL_UUID, on_notify)
                reading = True
                self.log(f"hudfeed: reading {name} over BLE")
                self.emit({"kind": "device", "name": name})
                while not gone.is_set() and not self._stop.is_set():
                    # bleak has no idle callback, so the dead-key window is closed on a timer.
                    try:
                        await asyncio.wait_for(gone.wait(), timeout=self.dead_key_ms / 1000)
                    except asyncio.TimeoutError:
                        stream.idle(int(time.monotonic() * 1000))
        except Exception as e:
            if reading:
                self.log(f"hudfeed: {name} over BLE ({type(e).__name__}: {e})")
            else:
                return f"{name} over BLE, not read ({type(e).__name__}: {e})"
        finally:
            self._streams.discard(stream)
            stream.close()
        return None if reading else f"{name} over BLE, not read"


KEYBOARD_REPORT_ID = 1


def split_report(report, report_id=KEYBOARD_REPORT_ID):
    """-> (modifiers byte, key usages) of a keyboard report, or None for other reports.

    ZMK's keyboard report is [id, modifiers, reserved, key...]; pass report_id=None for firmware
    without report ids. The pair is exactly what a `keys` frame carries, which is the point: both
    sources hand the decoder the same thing."""
    data = bytes(report)
    if report_id is not None:
        if len(data) < 3 or data[0] != report_id:
            return None
        return data[1], data[3:]
    if len(data) < 2:
        return None
    return data[0], data[2:]


HIDRAW_SYSFS = "/sys/class/hidraw"


def hidraw_match(uevent, vid, pid, name=None):
    """-> the HID_NAME of this hidraw node when it is the keyboard we want, else None.

    The kernel publishes HID_ID=bus:VVVVVVVV:PPPPPPPP and HID_NAME in the node's parent uevent,
    and both are world-readable, so which /dev/hidrawN belongs to which keyboard is answerable
    without opening anything at all.
    """
    fields = {}
    for line in uevent.splitlines():
        key, _, value = line.partition("=")
        fields[key.strip()] = value.strip()
    parts = fields.get("HID_ID", "").split(":")
    if len(parts) != 3:
        return None
    try:
        if (int(parts[1], 16), int(parts[2], 16)) != (vid, pid):
            return None
    except ValueError:
        return None
    product = fields.get("HID_NAME") or "?"
    if name and name.lower() not in product.lower():
        return None
    return product


class HidrawReader:
    """Reads what is typed straight from /dev/hidrawN. Linux only, and no hidapi.

    hidapi has two Linux backends and the wheel decides which, silently. The PyPI wheel bundles
    libusb, whose backend wants /dev/bus/usb -- which no udev rule here grants -- and which
    detaches the kernel HID driver when it opens a device, so a keyboard being read would stop
    working as a keyboard. What it reports when that fails is "open failed", with an empty product
    name, because reading the name needs the same access the open did.

    The kernel's own interface needs none of it: the node is found through sysfs, and reading it is
    an ordinary file read of one report per read -- the same bytes hidapi would have handed over,
    so split_report and the decoder below it are untouched. It is opened read-only, which is less
    than hidapi asks for and exactly what contrib/udev/60-zmk-layer-hud.rules grants."""

    def __init__(self, emit, vid=ZMK_VID, pid=ZMK_PID, name=None, report_id=KEYBOARD_REPORT_ID,
                 rescan=2.0, log=print, raw=False, dead_key_ms=DEAD_KEY_MS, sysfs=HIDRAW_SYSFS, altgr=False):
        self.emit, self.vid, self.pid, self.name = emit, vid, pid, name
        self.report_id, self.rescan, self.log = report_id, rescan, log
        self.raw = raw  # debug only: dumps every report, i.e. also what you type
        self.dead_key_ms, self.sysfs, self.altgr = dead_key_ms, sysfs, altgr
        self._open = {}
        self._streams = set()
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._scan_loop, name="hidraw-scan", daemon=True).start()

    def stop(self):
        self._stop.set()

    def resync(self):
        pass  # holds no layer state: it only ever reports keys

    def _paths(self):
        """(node, product name) of every hidraw node that is this keyboard."""
        out = []
        try:
            nodes = sorted(os.listdir(self.sysfs))
        except OSError:
            return out
        for node in nodes:
            try:
                with open(os.path.join(self.sysfs, node, "device", "uevent"), encoding="utf-8") as f:
                    product = hidraw_match(f.read(), self.vid, self.pid, self.name)
            except OSError:
                continue
            if product:
                out.append((f"/dev/{node}", product))
        return out

    def _scan_loop(self):
        said = None
        while not self._stop.is_set():
            paths = self._paths()
            note = hid_scan_note(len(paths), self.vid, self.pid, self.name)
            if note != said:
                said = note
                if note:
                    self.log(note)
            live = {p for p, _ in paths}
            for gone in [p for p, fd in self._open.items() if fd is None and p not in live]:
                del self._open[gone]
            for path, product in paths:
                if path in self._open:
                    continue
                try:
                    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                except OSError as e:
                    self.log(f"hudfeed: cannot read what is typed on {product} ({path}): {e}")
                    self.log(f"hudfeed: `ls -l {path}` and `getfacl {path}` say who may read it; the "
                             "uaccess tag in contrib/udev/60-zmk-layer-hud.rules grants it to the "
                             "active seat's user, and applies to nodes created after the rule, so "
                             "replug the keyboard")
                    self._open[path] = None  # do not retry every scan
                    continue
                self._open[path] = fd
                self.log(f"hudfeed: reading what is typed on {product} ({path})")
                threading.Thread(target=self._read_loop, args=(path, fd, product),
                                 name="hidraw-read", daemon=True).start()
            self._stop.wait(self.rescan)

    def _read_loop(self, path, fd, product):
        import select
        import time
        stream = Stream(self.emit, product, self.dead_key_ms, self.raw, self.log, self.altgr)
        self._streams.add(stream)
        try:
            while not self._stop.is_set():
                # Wake early while a dead key waits, so it is released on time when no letter follows.
                timeout = (self.dead_key_ms if stream.decoder.pending else 500) / 1000
                ready, _, _ = select.select([fd], [], [], timeout)
                now = int(time.monotonic() * 1000)
                if not ready:
                    stream.idle(now)
                    continue
                report = os.read(fd, 64)  # hidraw hands over exactly one report per read
                if not report:
                    continue
                parts = split_report(report, self.report_id)
                if parts is None:
                    continue  # a consumer or mouse report: not ours
                stream.message({"kind": "keys", "mods": parts[0], "keys": list(parts[1])}, now)
        except OSError as e:
            if not self._stop.is_set():
                self.log(f"hudfeed: {product} gone ({e})")
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            self._open.pop(path, None)
            self._streams.discard(stream)
            stream.close()


def hid_scan_note(found, vid, pid, name=None, platform=None):
    """The line to log for a scan that turned up `found` keyboards, or None when it found one.

    The quietest failure in the feed, and the reason it is worth a function of its own.
    _scan_loop iterates the paths it finds, so a scan that finds none runs no body at all: it logs
    nothing and tries again every rescan, for ever. Both of the things these reports carry -- the
    typed-keys strip, and the shift flag the board draws its capitals from -- come from here and
    from nowhere else, so that silence reads on screen as two unrelated features quietly not
    working, with nothing anywhere to say why.
    """
    if found:
        return None
    which = f"{vid:04x}:{pid:04x}" + (f" whose product name contains {name!r}" if name else "")
    note = (f"hudfeed: no HID keyboard {which} to read what is typed from; the typed-keys strip "
            "and the board's shift capitalisation stay off until there is one")
    if (platform or sys.platform) != "darwin":
        note += (" (install contrib/udev/60-zmk-layer-hud.rules, then replug the keyboard -- a "
                 "rule only applies to nodes created after it)")
    return note


class HidKeysReader:
    """Reads what is being typed from the keyboard's HID reports, with hidapi.

    The firmware sent this itself for a while, and it dropped keys. The snapshot could only be
    read after ZMK had updated the report, so it went through a k_work item, and k_work_submit on
    an already-queued item does nothing -- a press and its release arriving before the work ran
    collapsed into one snapshot, and the key was never held as far as the host could tell. Measured
    against `positions`, which go out synchronously and so survived: seven presses on the board, two
    on the strip. The module no longer has that path, and a keyboard still running a build that does
    is simply not listened to -- the frame kind is gone from the wire format, so those frames decode
    as unknown and are skipped.

    Reading the report here cannot lose a transition, because ZMK already sends one report per
    change; there is nothing to coalesce. This is how the HUD worked before the signal moved to its
    own channel, and it is the part of that design that was never at fault. What was at fault was
    the layer signal travelling *inside* the report as reserved usages, which Linux delivers as
    KEY_UNKNOWN; that is what the CDC-ACM channel fixed, and layers and positions still arrive
    there. Only the typed characters come from here.

    The cost is the permission the other channel does not need: Input Monitoring on macOS, and a
    keyboard Karabiner-Elements modifies is seized by it and gives us no reports."""

    def __init__(self, emit, vid=ZMK_VID, pid=ZMK_PID, name=None, report_id=KEYBOARD_REPORT_ID,
                 rescan=2.0, log=print, raw=False, dead_key_ms=DEAD_KEY_MS, altgr=False):
        self.emit, self.vid, self.pid, self.name = emit, vid, pid, name
        self.report_id, self.rescan, self.log = report_id, rescan, log
        self.raw = raw  # debug only: dumps every report, i.e. also what you type
        self.dead_key_ms, self.altgr = dead_key_ms, altgr
        self._open = {}
        self._streams = set()
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._scan_loop, name="hid-scan", daemon=True).start()

    def stop(self):
        self._stop.set()

    def resync(self):
        pass  # holds no layer state: it only ever reports keys

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
            self.log("hudfeed: hidapi is required for the typed-keys strip (make venv, or pip "
                     "install hidapi; macOS also brew install hidapi). Pass --no-hid-keys to run "
                     "without it: layers and positions still work.")
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
        said = None
        while not self._stop.is_set():
            paths = self._paths(hid)
            # Said on each transition rather than every rescan, the way the BLE reader does it: a
            # keyboard that is not there is no more absent for being mentioned every two seconds.
            note = hid_scan_note(len(paths), self.vid, self.pid, self.name)
            if note != said:
                said = note
                if note:
                    self.log(note)
            # A path that failed is not retried every scan, but it is forgotten once the device
            # goes away, so replugging after fixing a permission is enough -- the same bargain
            # SerialReader makes with _quiet.
            live = {p for p, _ in paths}
            for gone in [p for p, d in self._open.items() if d is None and p not in live]:
                del self._open[gone]
            for path, product in paths:
                if path in self._open:
                    continue
                try:
                    dev = hid.device()
                    dev.open_path(path)
                except (OSError, IOError, ValueError) as e:
                    # The node, not just the product name: hidapi reports an unreadable device with
                    # an empty name, so "cannot read what is typed on ?" named nothing anyone could
                    # go and look at, and "open failed" is all the reason it ever gives.
                    where = path.decode("utf-8", "replace") if isinstance(path, bytes) else str(path)
                    self.log(f"hudfeed: cannot read what is typed on {product} ({where}): {e}")
                    if sys.platform == "darwin":
                        self.log("hudfeed: on macOS this means the app running Python (your terminal, or "
                                 "Hammerspoon) lacks Input Monitoring (System Settings > Privacy & Security), "
                                 "or Karabiner-Elements modifies this keyboard's events and has seized it "
                                 "(Karabiner > Devices: untick it)")
                    else:
                        self.log(f"hudfeed: hidapi opens it read-write, so read alone is not enough. "
                                 f"`ls -l {where}` and `getfacl {where}` say who may; the uaccess tag in "
                                 "contrib/udev/60-zmk-layer-hud.rules grants it to the active seat's user "
                                 "and applies to nodes created after the rule, so replug the keyboard")
                    self._open[path] = None  # do not retry every scan
                    continue
                self._open[path] = dev
                self.log(f"hudfeed: reading what is typed on {product}")
                threading.Thread(target=self._read_loop, args=(path, dev, product),
                                 name="hid-read", daemon=True).start()
            self._stop.wait(self.rescan)

    def _read_loop(self, path, dev, product):
        import time
        stream = Stream(self.emit, product, self.dead_key_ms, self.raw, self.log, self.altgr)
        self._streams.add(stream)
        try:
            while not self._stop.is_set():
                # Wake early while a dead key waits, so it is released on time when no letter follows.
                report = dev.read(64, timeout_ms=self.dead_key_ms if stream.decoder.pending else 500)
                now = int(time.monotonic() * 1000)
                if not report:
                    stream.idle(now)
                    continue
                parts = split_report(report, self.report_id)
                if parts is None:
                    continue  # a consumer or mouse report: not ours
                stream.message({"kind": "keys", "mods": parts[0], "keys": list(parts[1])}, now)
        except (OSError, IOError, ValueError) as e:
            if not self._stop.is_set():
                self.log(f"hudfeed: {product} gone ({e})")
        finally:
            try:
                dev.close()
            except Exception:
                pass
            self._open.pop(path, None)
            self._streams.discard(stream)
            stream.close()


# ---------- keymap (the config + its definitions, live reload) ----------

class KeymapWatcher(threading.Thread):
    """Emits the keymap message on start and whenever the config or its definitions change (a
    sync writes them again). A broken edit is logged and the last good keymap stays on screen."""

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


# ---------- secure input ----------

def carbon_probe():
    """macOS's IsSecureEventInputEnabled, or None where there is none (not macOS, or no Carbon)."""
    if sys.platform != "darwin":
        return None
    try:
        import ctypes
        fn = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Carbon.framework/Carbon").IsSecureEventInputEnabled
    except (OSError, AttributeError):
        return None
    fn.restype, fn.argtypes = ctypes.c_bool, []
    return fn


# Where this user's terminals are, by platform: the slave end of each pty, which is what a program
# at a prompt reads from.
PTY_DIRS = {"darwin": ("/dev", re.compile(r"ttys\d+$")), "linux": ("/dev/pts", re.compile(r"\d+$"))}


def asks_secret(path):
    """Whether the terminal at `path` is reading a secret: echo off, and still a line at a time.
    That is how sudo, ssh, su, passwd and getpass take a password. A shell's line editor and every
    full-screen program turn echo off too, but take the keys raw, one at a time (ICANON off), and a
    terminal with nothing asked of it echoes. O_NOCTTY, because the feed is a session leader on
    Linux, and opening a terminal without it would make that terminal the feed's own."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
    except OSError:          # another user's, or gone
        return False
    try:
        lflag = termios.tcgetattr(fd)[3]
    except (termios.error, OSError):
        return False
    finally:
        os.close(fd)
    return not lflag & termios.ECHO and bool(lflag & termios.ICANON)


def terminal_probe(where=None):
    """-> whether one of this user's terminals is asking for a secret (asks_secret), or None where
    the terminals cannot be found. Linux has no switch like macOS's, and a terminal's prompt is
    where a password is typed on it that the feed can tell: sudo's, ssh's, su's. On macOS it stands
    beside the switch, for a terminal that does not throw it at a password prompt. A password field
    in a window -- a browser's, a password manager's -- shows nothing this way."""
    where = where or PTY_DIRS.get(sys.platform)
    if where is None or not os.path.isdir(where[0]):
        return None
    directory, pattern = where

    def probe():
        try:
            names = os.listdir(directory)
        except OSError:
            return False
        return any(asks_secret(os.path.join(directory, n)) for n in names if pattern.match(n))
    return probe


def any_of(probes):
    """One probe from several: True at the first that says so, None if one does not know yet
    (a11y.PasswordFocus while an app is asked), else False."""
    def probe():
        unknown = False
        for p in probes:
            got = p()
            if got:
                return True
            if got is None:
                unknown = True
        return None if unknown else False
    return probe


# What each probe is, for the log and the doctor.
PROBE_WORDS = {"carbon": "macOS's secure input", "terminals": "the terminals' prompts",
               "a11y": "the accessibility bus", "lock": "Hyprland's session lock"}


def probe_report(environ=os.environ):
    """One line per probe, for `--probes` and `zmk-layer-hud doctor`: its name, `on`, `off` or
    `none` (not for this system), and why. Asked here rather than in the doctor because the probes
    need the venv (jeepney) and the feed's own tables."""
    lines = []

    def say(name, state, detail):
        lines.append(f"{name}\t{state}\t{detail}")
    if sys.platform == "darwin":
        say("carbon", "on" if carbon_probe() else "off",
            "IsSecureEventInputEnabled is asked at every keystroke" if carbon_probe() else "Carbon could not be loaded")
    else:
        say("carbon", "none", "not macOS")
    where = PTY_DIRS.get(sys.platform)
    if terminal_probe():
        say("terminals", "on", f"a prompt reading a line with echo off on {where[0]} is seen")
    else:
        say("terminals", "off", "no terminals' directory on this system")
    if sys.platform != "linux":
        say("a11y", "none", "macOS has its switch")
        say("lock", "none", "macOS's lock screen turns secure input on")
        return lines
    if not a11y.HAVE_JEEPNEY:
        say("a11y", "off", "jeepney is not in the venv (zmk-layer-hud setup)")
    else:
        try:
            session, bus = a11y.connect_buses()
        except Exception as e:
            say("a11y", "off", f"the accessibility bus cannot be reached: {type(e).__name__}: {e} (at-spi2-core)")
        else:
            try:
                enabled = a11y.is_enabled(session)
            except Exception as e:
                enabled = None
                say("a11y", "off", f"the accessibility bus does not answer: {type(e).__name__}: {e}")
            finally:
                for c in (bus, session):
                    try:
                        c.close()
                    except Exception:
                        pass
            if enabled is not None:
                say("a11y", "on", "the apps expose their widgets (org.a11y.Status IsEnabled)" if enabled else
                    "IsEnabled is off: the feed turns it on when it starts (feed.accessibility), and a browser "
                    "running since before needs starting again")
    path = sessionlock.socket_path(environ)
    if not path:
        say("lock", "off", "no Hyprland (HYPRLAND_INSTANCE_SIGNATURE unset, or no control socket): a lock screen is not seen")
    else:
        got = sessionlock.locked(path)
        say("lock", "on", "Hyprland does not say (no solitaryBlockedBy in `hyprctl -j monitors`): a locker is looked for "
            f"by name ({', '.join(sessionlock.LOCKERS)})" if got is None else
            f"Hyprland says the session is {'locked' if got else 'unlocked'}")
    return lines


class SecureInput:
    """When what is being typed is a secret, nothing of it goes on. macOS says so: while a password
    field has focus, or a terminal's Secure Keyboard Entry is on, secure event input is enabled and
    every app is asked not to look (carbon_probe). A terminal says so by its mode: at a password
    prompt it reads a line with echo off (terminal_probe), on Linux as on macOS. The feed cannot
    help seeing -- it reads the keyboard's own channel and its HID reports, which neither reaches
    -- so for as long as either says so it passes on nothing that could spell the secret: no
    character typed, no key pressed, and no layer change either, which would say of each
    character whether it was a digit, a symbol or a capital, and when. The page is told
    (`{"kind": "secure", "on": true}`), clears what is on screen and says why. The latest layers
    of each keyboard are kept and follow the off message, so the board catches up at once
    rather than at the next heartbeat; `device`, `keymap`, `session` and `secure` go through.

    Asked at every keystroke, not only by the poll: the first key typed into a password field must
    not slip through in the moment before a poll would have noticed. A probe that does not know
    yet (None: the accessibility watch, while the app is asked what the widget is) holds the keys
    the same, with nothing said to the page, whose clearing of the screen is not for a lookup of
    a few milliseconds. The poll is what tells the page when nothing is being typed."""

    def __init__(self, emit, probe, poll_s=0.25, log=print, watched=()):
        self.emit, self.probe, self.poll_s, self.log = emit, probe, poll_s, log
        self.watched = tuple(watched)      # the probes' names, on every message: what the page may trust
        self.on = False
        self.held = {}          # device -> the latest layers message kept back while it is on
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def check(self, hold=None):
        """Whether a secret is being typed now (True, or while it is not known yet), the page told
        of a change. `hold` is a layers message to keep back meanwhile: the latest of each
        keyboard follows the off message, or goes on at once when nothing was being typed."""
        try:
            on = self.probe()
            on = None if on is None else bool(on)
        except Exception:
            on = self.on   # a probe that failed once says nothing new
        with self.lock:
            if hold is not None and on is not False:
                self.held[hold.get("device")] = hold
            if on is None:
                return True                 # held back; nothing said until it is known
            if on != self.on:
                self.on = on
                self.emit(self._message(on))
            if not on and self.held:
                held, self.held = self.held, {}
                for msg in held.values():
                    self.emit(msg)
        return on

    def allows(self, msg):
        """Whether `msg` may go on: not a key, a position or a layer change while secure input is on."""
        kind = msg.get("kind")
        if kind not in ("key", "press", "release", "layers"):
            return True
        return not self.check(hold=msg if kind == "layers" else None)

    def _message(self, on):
        return {"kind": "secure", "on": on, "watched": list(self.watched)}

    def start(self):
        if self.check() is not True:
            self.emit(self._message(False))     # said at the start either way: the page learns what watches
        self._thread = threading.Thread(target=self._run, name="secure-input", daemon=True)
        self._thread.start()
        return self

    def _run(self):
        while not self._stop.wait(self.poll_s):
            self.check()

    def stop(self):
        self._stop.set()


# ---------- the whole feed, embeddable ----------

def read_config(path=None):
    """The config as written, or {} when there is none to read -- without its keymap, which may be
    what failed."""
    try:
        cfg = keymap_mod.load_yaml(keymap_mod.find_config(path))
    except Exception:
        return {}
    return cfg if isinstance(cfg, dict) else {}


class Feed:
    """Everything a host needs: load the config, watch the keymap, read the keyboard(s). `emit`
    is called from worker threads with each message; hosts marshal it to their UI thread.
    Used in-process by host/macos/panel.py and by main() below for the WebSocket/stdout modes."""

    def __init__(self, emit, log=print, config=None, keys=True, keymap=True, vid=None, pid=None, name=None,
                 port=None, ble=True, ble_address=None, hid_keys=True, raw=False, sessions=True):
        self.emit, self.log = emit, log
        self.source = None
        try:
            self.source = keymap_mod.KeymapSource(config)
            self.source.load()  # fail early with a readable reason
        except keymap_mod.KeymapError as e:
            log(f"hudfeed: {e}")
            if keymap:
                log("hudfeed: continuing without a keymap; the pages show nothing until one arrives")
        except Exception as e:
            log(f"hudfeed: config/keymap failed: {type(e).__name__}: {e}")
        # Which keyboard to read, on which port and with which timings is the config's own: a keymap
        # that does not convert must not also send the reader to the default keyboard. The source
        # has the config (and what was imported) once it got that far; else it is read on its own.
        self.cfg = cfg = (self.source.cfg if self.source is not None else {}) or read_config(config)
        kb = cfg.get("keyboard") or {}
        ser = cfg.get("serial") or {}
        bt = cfg.get("ble") or {}
        feed_cfg = dict(keymap_mod.FEED_DEFAULTS)
        feed_cfg.update(cfg.get("feed") or {})
        self.keymap = keymap
        self.keys = keys
        rescan, dead_key_ms = float(feed_cfg["rescan_s"]), int(feed_cfg["dead_key_ms"])
        altgr = bool(int(feed_cfg["altgr"]))
        kb_name = name if name is not None else kb.get("name")
        # --raw logs what the readers decode, which is the typing: asked at every message, like a
        # keystroke is, so that nothing is logged while a secret is being typed (self.secure is
        # made below and read when a message arrives).
        raw_log = (lambda: self.secure is None or not self.secure.check()) if raw else False
        self.reader = SerialReader(
            self._emit, log=log, raw=raw_log, rescan=rescan, dead_key_ms=dead_key_ms, altgr=altgr,
            vid=vid if vid is not None else int(kb.get("vid", ZMK_VID)),
            pid=pid if pid is not None else int(kb.get("pid", ZMK_PID)),
            name=kb_name, port=port if port is not None else ser.get("port"),
            probe_s=float(ser.get("probe_s", 8.0)))
        self.ble = None
        if ble and bt.get("enabled", True):
            self.ble = BleReader(self._emit, log=log, raw=raw_log, rescan=rescan, dead_key_ms=dead_key_ms, altgr=altgr,
                                 address=ble_address if ble_address is not None else bt.get("address"),
                                 name=kb_name)
        # What is typed comes from the keyboard's HID reports, not from the signal channel: one
        # report per change, so nothing can be coalesced away.
        self.hid = None
        if keys and hid_keys:
            # Linux reads the kernel's hidraw node itself; everywhere else that is hidapi's job.
            # Not a preference: the PyPI wheel's backend is libusb, which needs an access no rule
            # here grants and detaches the kernel driver from the keyboard it opens.
            reader = HidrawReader if sys.platform.startswith("linux") else HidKeysReader
            self.hid = reader(
                self._emit, log=log, raw=raw_log, rescan=rescan, dead_key_ms=dead_key_ms, altgr=altgr,
                vid=vid if vid is not None else int(kb.get('vid', ZMK_VID)),
                pid=pid if pid is not None else int(kb.get('pid', ZMK_PID)),
                name=kb_name)
        self.watcher = KeymapWatcher(self.source, self._emit, log) if (self.source is not None and keymap) else None
        # The active session: the page counts what it draws and reports it here (Hub.handler on
        # a WebSocket host, the panel's bridge on macOS), and this keeps it in the session's file.
        self.sessions = None
        if sessions and int(feed_cfg.get("sessions", 1)):
            self.sessions = session_mod.Store(emit, flush_s=feed_cfg["session_flush_s"],
                                              poll_s=feed_cfg["session_poll_s"], log=log)
            if self.source is not None and self.source.message:
                self.sessions.set_keymap(self.source.message)
        # While a secret is being typed -- macOS says so, a terminal is asking for one, or on Linux a
        # password field has focus on the accessibility bus or the session is locked -- nothing
        # typed goes on (SecureInput). `watched` names the probes this run has, for the log.
        self.secure = None
        self.focus = None
        self.watched = ()
        self.secure_wanted = bool(int(feed_cfg.get("secure_input", 1)))
        if self.secure_wanted:
            probes = {"carbon": carbon_probe(), "terminals": terminal_probe(), "lock": sessionlock.probe(log)}
            if a11y.available():
                self.focus = a11y.PasswordFocus(log=log, enable=bool(int(feed_cfg.get("accessibility", 1))))
                probes["a11y"] = self.focus.probe
            probes = {name: p for name, p in probes.items() if p is not None}
            self.watched = tuple(probes)
            if probes:
                self.secure = SecureInput(emit, any_of(list(probes.values())), log=log, watched=self.watched)

    def _emit(self, msg):
        # --no-keys: layers only. The positions go with the keys: with the keymap they are the typing.
        if not self.keys and msg["kind"] in ("key", "press", "release"):
            return
        if self.secure is not None and not self.secure.allows(msg):
            return
        if msg["kind"] in ("press", "release"):
            self._check_position(msg["pos"])
        if msg["kind"] == "keymap" and self.sessions is not None:
            self.sessions.set_keymap(msg)     # a keymap edited while it runs: its layers from now on
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
        if self.sessions:
            try:
                self.sessions.start()
            except (OSError, session_mod.SessionError) as e:
                self.log(f"hudfeed: no sessions this run: {e}")
                self.sessions = None
        if self.focus:
            self.focus.start()
        # What watches for a secret being typed is said once, and so is nothing watching: the page
        # is told too (`watched`), and says `typing not watched` on its banner.
        if self.secure:
            self.log("hudfeed: a secret being typed is seen by " + ", ".join(PROBE_WORDS[n] for n in self.watched))
            self.secure.start()
        else:
            self.log("hudfeed: secure input is off (feed.secure_input: 0): what is typed into a password field reaches "
                     "the pages" if not self.secure_wanted else
                     "hudfeed: nothing here sees a secret being typed (no macOS switch, no terminals, no accessibility "
                     "bus, no Hyprland): what is typed into a password field reaches the pages")
            self.emit({"kind": "secure", "on": False, "watched": []})
        if self.watcher:
            self.watcher.start()
        self.reader.start()
        if self.ble:
            self.ble.start()
        if self.hid:
            self.hid.start()
        return self

    def resync(self):
        """Draw the keyboard's real state again, after something else drew over it."""
        self.reader.resync()
        if self.ble:
            self.ble.resync()

    def stop(self):
        if self.watcher:
            self.watcher.stop()
        self.reader.stop()
        if self.ble:
            self.ble.stop()
        if self.hid:
            self.hid.stop()
        if self.secure:
            self.secure.stop()
        if self.focus:
            self.focus.stop()
        if self.sessions:
            self.sessions.stop()   # writes what is still only counted in memory


# ---------- outputs ----------

# Kinds a client may send in, to be fanned out as if the keyboard had produced them. The keymap is
# not among them: it is large, it is built from files this already watches, and a page fed a wrong
# one has no way back to the right one.
# `heat`: what the keys glow with, for a moment of a demo (a slow-motion part with the heat off) --
# on the pages only, never the session's choice, which `heatmap` from the panel's own page is.
INJECTABLE = ("layers", "press", "release", "key", "device", "heat")

# Whether typing sent in is drawn with the keymap's combos. A `key` a client sends says it with
# `combos`; one that does not say gets this. It is off because a combo is a choice of technique,
# not something a character implies -- z is both the r+a chord and Alpha 2's key -- and typing
# sent in is typically a take being staged, where the chords are the typist's to show or not.
COMBOS_DEFAULT = False


def sent_in(msg):
    """An injected message as the pages get it: `sent`, and a `key` says whether combos count.

    `sent` is on every kind, whatever the client said: only the keyboard's own typing is counted
    into a session, and a press sent in lights its key like any other -- nothing but this says
    it was not the keyboard's. `combos` is also how a page tells typing sent in from the
    keyboard's own reports, which never carry it: those are placed on the layers the keyboard
    says are up, exactly, while typing sent in did not come from those layers and is placed by
    technique (hud/hud.js handleKey)."""
    msg = {**msg, "sent": True}
    if msg.get("kind") == "key" and not isinstance(msg.get("combos"), bool):
        msg["combos"] = COMBOS_DEFAULT
    return msg


# Where a browser page that may hold the socket is: the panels' pages are files, the demo's and a
# developer's are served from this machine. A page from anywhere else is refused whatever it holds.
# "null" is what a file: page says of itself in some engines (and what a sandboxed frame says, which
# is why the Origin is the second gate and the token the first). A Python client sends no Origin.
PAGE_ORIGINS = ("null", "file://")
LOCAL_ORIGIN = re.compile(r"https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?")
REFUSAL = ("zmk-layer-hud: this socket takes a per-run token in its path, ws://127.0.0.1:PORT/<token>; "
           "a running HUD keeps it in $STATE/token\n")


class Hub:
    """Fans messages out to WebSocket clients and/or stdout; replays the cached keymap and layer
    set to new clients so a page that (re)connects is right immediately.

    Every client holds the run's token (process_request): what travels here is the user's typing,
    and the token is what keeps a web page on another origin, or another user on the machine, from
    reading it. The same gate covers what comes in -- {"kind":"close"} and the INJECTABLE kinds --
    so nothing without the token can take the HUD down or draw on it.

    Messages also travel the other way. A client may send any of INJECTABLE and it is broadcast to
    every client exactly as a keyboard's would be, which is how the page is exercised without one --
    host/hudpoke.py is the sender. Only the keyboard can produce a real frame, so this tests the
    page and not the firmware, and it is worth being clear about which of the two you just proved.
    On by default: a client that holds the token is the user's own, and the channel already carries
    `close`, which is more power than a cosmetic message. --no-inject closes it."""

    def __init__(self, stdout=False, debug=False, inject=True, on_inject=None, sessions=None, tally_token=None,
                 on_sent_in=None, token=None):
        self.stdout, self.debug, self.inject = stdout, debug, inject
        # The run's socket token: a client connects to ws://host:port/<token>, or not at all.
        self.token = token
        # Called after an injected message, so the keyboard's own state can be re-asserted.
        self.on_inject = on_inject
        # Given each message sent in, as the clients got it: for a page that is not one of them
        # (the macOS panel's, which the feed reaches through the panel's bridge).
        self.on_sent_in = on_sent_in
        # The active session (session.Store), and the token a page must send its counts with: the
        # panel makes one per run and gives it to its own page alone, so a second page on the
        # socket (a browser pointed at it) shows the session but cannot add to it twice.
        self.sessions, self.tally_token = sessions, tally_token
        self.clients = set()
        self.cache = {}  # kind -> last message, for "keymap", "layers", "device" and "session"

    def log(self, *a):
        print(*a, file=sys.stderr, flush=True)

    def process_request(self, connection, request):
        """websockets' hook, before the upgrade: the token in the path, then the Origin. A client
        that fails either gets HTTP 403 and never sees a message. What it offered is never logged."""
        given = urllib.parse.urlsplit(request.path).path.strip("/")
        origin = request.headers.get("Origin")
        token_ok = bool(self.token) and hmac.compare_digest(given.encode("utf-8"), self.token.encode("utf-8"))
        origin_ok = origin is None or origin in PAGE_ORIGINS or LOCAL_ORIGIN.fullmatch(origin) is not None
        if token_ok and origin_ok:
            return None
        why = "no token" if not given else "wrong token" if not token_ok else f"origin {origin}"
        self.log(f"hudfeed: refused a client ({why})")
        return connection.respond(http.HTTPStatus.FORBIDDEN, REFUSAL)

    async def send(self, msg):
        if msg["kind"] in ("keymap", "layers", "device", "session", "secure"):
            self.cache[msg["kind"]] = msg
        # Layers only: a press with a timestamp is typing, and this log is a plain file.
        if self.debug and msg["kind"] == "layers":
            import time
            self.log(f"hudfeed: {time.monotonic() * 1000:.0f}ms", json.dumps(msg, ensure_ascii=False))
        data = json.dumps(msg, ensure_ascii=False)
        if self.stdout:
            print(data, flush=True)
        if self.clients:
            await asyncio.gather(*(c.send(data) for c in list(self.clients)), return_exceptions=True)

    async def send_in(self, msg):
        """A message sent in -- by a client, or by the demo's player -- fanned out as the keyboard's
        would be, and marked as not the keyboard's (sent_in). The keyboard's own state is then
        asserted again, so a HUD with a keyboard behind it goes back to the truth by itself."""
        msg = sent_in(msg)
        await self.send(msg)
        if self.on_sent_in is not None:
            self.on_sent_in(msg)
        if self.on_inject is not None:
            self.on_inject()

    async def handler(self, ws):
        self.clients.add(ws)
        try:
            for msg in self.cache.values():
                await ws.send(json.dumps(msg, ensure_ascii=False))
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict):
                    continue
                kind = msg.get("kind")
                if kind == "close":
                    self.log("hudfeed: close requested by the page")
                    if self.sessions is not None:
                        self.sessions.flush()   # os._exit runs no cleanup: what was counted goes first
                    os._exit(0)
                if kind in ("tally", "heatmap", "pref"):
                    # A page's counts, or the heatmap or a preference it switched to: only from the
                    # page the tally token was given to -- a second secret, because a second page
                    # holding the socket's must still not count twice. `--no-inject` is about
                    # drawing, not this.
                    if self.sessions is not None and self.tally_token and self._tally_ok(msg.get("token")):
                        if kind == "tally":
                            self.sessions.apply(msg)
                        elif kind == "heatmap":
                            self.sessions.set_heatmap(msg.get("mode"))
                        else:
                            self.sessions.set_pref(msg.get("name"), msg.get("value"))
                    continue
                if self.inject and kind in INJECTABLE:
                    await self.send_in(msg)
        finally:
            self.clients.discard(ws)

    def _tally_ok(self, token):
        return isinstance(token, str) and hmac.compare_digest(token.encode("utf-8"), self.tally_token.encode("utf-8"))


def serve_hub(hub, host, port, **kw):
    """websockets.serve for a Hub, with its gate in front: the one way a Hub is served, so no
    socket is ever opened without the token."""
    if not hub.token:
        raise ValueError("a Hub serves only with a token: Hub(token=secrets.token_hex(16))")
    import websockets
    return websockets.serve(hub.handler, host, port, process_request=hub.process_request, **kw)


class HubThread:
    """A Hub served on a thread and an event loop of its own, for a host whose main thread is a
    GUI's: the macOS panel runs the feed in-process, and this is its socket, the one the Linux
    panel's feed serves -- a browser can watch the HUD, and `zmk-layer-hud poke` can drive it.
    `send` may be called from any thread, before the socket is up too: the loop exists from the
    start, and what is sent early (the keymap) waits for it and is cached for replay like the rest.
    A port already taken is said, and the panel goes on without a socket."""

    def __init__(self, hub, port, log=print, host="127.0.0.1"):
        self.hub, self.port, self.log, self.host = hub, port, log, host
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()      # set once it serves, or has given up
        self.serving = False
        self._stopping = None
        self._thread = threading.Thread(target=self._run, name="socket", daemon=True)

    def start(self):
        self._thread.start()
        return self

    def send(self, msg):
        # A socket that could not be opened has closed its loop: what it is sent goes nowhere.
        with contextlib.suppress(RuntimeError):
            self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self.hub.send(msg)))

    def stop(self):
        if self._stopping is not None:
            with contextlib.suppress(RuntimeError):
                self.loop.call_soon_threadsafe(self._stopping.set)
        self._thread.join(timeout=2)

    def _run(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._serve())
        finally:
            # Sends still waiting when it stopped (or never started) are dropped, not left pending.
            pending = asyncio.all_tasks(self.loop)
            for task in pending:
                task.cancel()
            self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self.ready.set()
            self.loop.close()

    async def _serve(self):
        self._stopping = asyncio.Event()
        try:
            import websockets
        except ImportError:
            self.log("hudfeed: no socket: python-websockets is missing (make venv)")
            return
        try:
            async with serve_hub(self.hub, self.host, self.port) as server:
                self.port = server.sockets[0].getsockname()[1]      # the one taken, when asked for any (0)
                self.serving = True
                self.log(f"hudfeed: ws://{self.host}:{self.port}/<token> (the token is in $STATE/token)")
                self.ready.set()
                await self._stopping.wait()
        except OSError as e:
            self.log(f"hudfeed: no socket on {self.host}:{self.port} ({e.strerror or e}); "
                     "`zmk-layer-hud poke` cannot reach this HUD")
        finally:
            self.serving = False


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--config", help=f"zmk-layer-hud config (default: $ZMKHUD_CONFIG or {keymap_mod.DEFAULT_CONFIG})")
    p.add_argument("--stdout", action="store_true", help="print messages as JSON lines (macOS host)")
    p.add_argument("--no-ws", action="store_true", help="do not serve the WebSocket")
    p.add_argument("--port", type=int, default=int(os.environ.get("ZMKHUD_PORT", "8766")))
    p.add_argument("--no-keymap", action="store_true", help="skip the keymap feed (pages keep whatever they have)")
    p.add_argument("--no-keys", action="store_true",
                   help="send layers only: no typed keys and no key positions (--no-hid-keys keeps the positions)")
    p.add_argument("--vid", type=lambda s: int(s, 0), help="keyboard vendor id (default: config `keyboard.vid`, else ZMK's)")
    p.add_argument("--pid", type=lambda s: int(s, 0), help="keyboard product id (default: config `keyboard.pid`, else ZMK's)")
    p.add_argument("--name", help="substring of the product name to select one keyboard (default: config `keyboard.name`)")
    p.add_argument("--serial", metavar="DEV", help="the keyboard's serial port, instead of finding it by vid/pid "
                                                   "(default: config `serial.port`)")
    p.add_argument("--no-ble", action="store_true", help="do not look for BLE keyboards")
    p.add_argument("--ble-address", help="address of the BLE keyboard (default: config `ble.address`); needed where a "
                                         "connected keyboard no longer advertises")
    p.add_argument("--no-hid-keys", action="store_true",
                   help="do not read the keyboard's HID reports for the typed-keys strip "
                        "(needs Input Monitoring on macOS); layers and positions are unaffected")
    p.add_argument("--no-inject", action="store_true",
                   help=f"refuse messages sent in by a WebSocket client ({', '.join(INJECTABLE)}), "
                        "which host/hudpoke.py uses to drive the page without a keyboard")
    p.add_argument("--no-remote", action="store_true",
                   help="never serve a tablet, whatever `zmk-layer-hud remote on` asks (Linux; host/remote.py)")
    p.add_argument("--no-sessions", action="store_true",
                   help="keep no session files (config `feed.sessions: 0`): the counts last as long as the page")
    p.add_argument("--debug", action="store_true", help="log layer messages to stderr")
    p.add_argument("--probes", action="store_true",
                   help="say which probes see a secret being typed on this system, one per line (name, on|off|none, "
                        "why), and exit; what `zmk-layer-hud doctor` reads")
    p.add_argument("--raw", action="store_true",
                   help="DEBUG ONLY: log every decoded frame and HID report, your typing included; "
                        "nothing is logged while secure input is on")
    return p.parse_args(argv)


def remote_typing(loop, tell, log):
    """Remote mode's keys on Linux (host/remote.py): a virtual keyboard of its own beside the
    panel's, made when remote mode starts serving and gone when it stops. Here rather than in the
    panel because the panel runs the system Python, which has no websockets."""
    import inject
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "linux"))
    import uinput

    def make():
        try:
            return uinput.Uinput().open()
        except OSError as e:
            raise inject.Unavailable(uinput.state_of(e), str(e)) from None
    return inject.Typing(make, tell, lambda seconds, fn: loop.call_later(seconds, fn), log=log)


async def main(args):
    # The Linux panel makes the token and gives it to its pages and to this (ZMKHUD_TOKEN); run by
    # hand, the feed makes its own. Either way $STATE/token is where `poke` finds it.
    token = os.environ.get("ZMKHUD_TOKEN") or secrets.token_hex(16)
    hub = Hub(stdout=args.stdout, debug=args.debug, inject=not args.no_inject,
              tally_token=os.environ.get("ZMKHUD_TALLY_TOKEN") or None, token=token)
    loop = asyncio.get_running_loop()
    # Remote mode, on Linux: a paired tablet's browser typing here (host/remote.py). It serves
    # nothing until `zmk-layer-hud remote on` asks; the macOS panel runs its own.
    remote = None
    if sys.platform.startswith("linux") and not args.no_remote:
        import remote as remote_mod
        remote = remote_mod.Controller(panelstate.default_dir(), None, log=hub.log, loop=loop)
        remote.typing = remote_typing(loop, remote.tell, hub.log)
        remote_task = asyncio.ensure_future(remote.run())

    def emit(msg):
        loop.call_soon_threadsafe(lambda: asyncio.ensure_future(hub.send(msg)))
        if remote is not None:
            remote.hub_said(msg)

    feed = Feed(emit, log=hub.log, config=args.config, keys=not args.no_keys, keymap=not args.no_keymap,
                vid=args.vid, pid=args.pid, name=args.name, port=args.serial,
                ble=not args.no_ble, ble_address=args.ble_address,
                hid_keys=not args.no_hid_keys, raw=args.raw, sessions=not args.no_sessions).start()
    hub.on_inject = feed.resync
    hub.sessions = feed.sessions

    # The Linux panel stops this with SIGTERM. Unhandled, it ends the process where it stands and
    # the last seconds of counting with it; handled, the feed stops and writes them first.
    stopping = asyncio.Event()
    import signal
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stopping.set)
    try:
        if not args.no_ws:
            try:
                import websockets
            except ImportError:
                sys.exit("python-websockets is required for the WebSocket (or pass --no-ws): pip install websockets")
            state = panelstate.default_dir()
            async with serve_hub(hub, "127.0.0.1", args.port):
                panelstate.write_token(state, token)
                hub.log(f"hudfeed: ws://127.0.0.1:{args.port}/<token> (the token is in {state}/{panelstate.TOKEN_FILE})")
                try:
                    await stopping.wait()
                finally:
                    panelstate.remove_token(state, token)
        else:
            await stopping.wait()
    finally:
        feed.stop()
        if remote is not None:
            remote.stop()                   # its keys up and its address closed
            with contextlib.suppress(Exception):
                await asyncio.wait_for(remote_task, 5)


if __name__ == "__main__":
    args = parse_args()
    if args.probes:
        print("\n".join(probe_report()))
        sys.exit(0)
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        pass
