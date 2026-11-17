#!/usr/bin/env python3
"""End-to-end check of the real HUD: start it, look at what the platform shows of it, drive it, stop it.

    python3 e2e/driver.py --artifacts DIR        # in CI: .github/workflows/e2e.yml

The same script runs on every platform; only the probe of the HUD's surfaces differs:

    macOS      the panel process's window in the window list (Quartz), and its alpha
    Hyprland   `hyprctl layers -j`: the namespaces zmkhud-layer and zmkhud-keys

What it proves, in order, with an isolated $ZMKHUD_STATE, the 3x5 sample config (it ships its own
definitions) and the Bluetooth reader off:

    start            `zmk-layer-hud start` exits 0 and a panel comes up whose panel.json a live pid backs
    page             the page loaded and said so over the panel's bridge (its stats column is in `status --json`)
    surfaces         the platform shows the HUD's windows
    socket           the token file is 0600; the right token upgrades, a wrong or missing token, or a foreign
                     Origin, gets HTTP 403 (docs/security-review.md finding 1, which was only tested by hand)
    poke             typing sent in over that socket is accepted and fanned out: a client that connects
                     afterwards is replayed the layers that were poked
    hide/show/toggle `shown` flips in `status --json` and panel.json, and the surfaces follow as they are
                     meant to: macOS draws a hidden HUD at alpha 0, Hyprland keeps its surfaces mapped
                     (hidden is drawn as nothing, not unmapped, so the page's timers keep counting)
    doctor           runs and names the platform
    stop             no panel, feed or socket is left

What it does not prove, and no run of it will: that anything is drawn on the surfaces (no legend or pixel
is asserted; screenshots are artifacts, best effort), keyboard behaviour, Bluetooth, lock screens, how a drag
feels. docs/development.md says so too.

It starts and stops the user's real HUD of this tree (`stop` kills every panel of it), so it refuses to run
outside CI unless asked (--local).

Standard library only, and Python 3.9: the helpers above the line "the run itself" are pure and have
e2e/driver_test.py, which ordinary CI runs on every platform.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import itertools
import json
import os
import re
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "bin" / "zmk-layer-hud"
sys.path.insert(0, str(ROOT / "host"))
import panelstate  # noqa: E402  (host/panelstate.py: stdlib only, the one definition of $STATE's files)

# Where the three phases of a wait are given up on (seconds), unless --timeout says otherwise.
TIMEOUT = 90.0
HYPR_NAMESPACES = ("zmkhud-layer", "zmkhud-keys")
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
FOREIGN_ORIGIN = "https://e2e-foreign-origin.invalid"


class DriverError(Exception):
    """Something this run depends on is not as it needs to be; the message says what."""


# ---------- what happened ----------

PASS, FAIL, SKIP, NOTE = "pass", "fail", "skip", "note"


class Report:
    """Every check, in order. A skipped check says why and is never counted as a pass; a note is
    something seen and not asserted."""

    def __init__(self, echo=print):
        self.results = []
        self.echo = echo

    def add(self, status, name, detail=""):
        self.results.append({"status": status, "name": name, "detail": str(detail)})
        self.echo(f"{status.upper():5} {name}" + (f" -- {detail}" if detail else ""))

    def passed(self, name, detail=""):
        self.add(PASS, name, detail)

    def failed(self, name, detail=""):
        self.add(FAIL, name, detail)

    def skipped(self, name, why):
        self.add(SKIP, name, why)

    def note(self, name, detail=""):
        self.add(NOTE, name, detail)

    def check(self, name, ok, detail=""):
        self.add(PASS if ok else FAIL, name, detail)
        return ok

    @property
    def ok(self):
        return not any(r["status"] == FAIL for r in self.results)

    def count(self, status):
        return sum(1 for r in self.results if r["status"] == status)


def render_summary(results, title):
    """The checks as a markdown table, for the job's summary page (GITHUB_STEP_SUMMARY)."""
    icon = {PASS: "✅", FAIL: "❌", SKIP: "⏭️", NOTE: "ℹ️"}
    rows = [f"### {title}", "", "| | check | detail |", "|---|---|---|"]
    for r in results:
        name = r["name"].replace("|", "\\|")
        detail = r["detail"].replace("|", "\\|").replace("\n", " ")
        rows.append(f"| {icon.get(r['status'], r['status'])} | {name} | {detail[:300]} |")
    failed = sum(1 for r in results if r["status"] == FAIL)
    skipped = sum(1 for r in results if r["status"] == SKIP)
    passed = sum(1 for r in results if r["status"] == PASS)
    rows += ["", f"{passed} passed, {failed} failed, {skipped} skipped."]
    return "\n".join(rows) + "\n"


# ---------- waiting ----------

def wait_for(probe, what, timeout, interval=0.5, clock=time.monotonic, sleep=time.sleep):
    """Poll `probe()` -> (done, detail) until it is done; returns that detail. Raises DriverError
    saying what was being waited for and the last thing the probe said, so a timeout is never just
    'timed out'. The clock and the sleep are parameters for the tests."""
    deadline = clock() + timeout
    detail = "never polled"
    while True:
        done, detail = probe()
        if done:
            return detail
        if clock() >= deadline:
            raise DriverError(f"gave up after {timeout:.0f} s waiting for {what}: {detail}")
        sleep(interval)


# ---------- reading what the HUD says of itself ----------

def parse_status(text):
    """`zmk-layer-hud status --json`: one JSON object (host/panelstate.hud). DriverError for anything else."""
    text = (text or "").strip()
    for candidate in (text, text.splitlines()[-1] if text else ""):
        try:
            obj = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    raise DriverError(f"`status --json` did not print a JSON object: {text[:200]!r}")


def panel_up(st, feed_where):
    """Whether a panel stands behind `status --json`, as (ok, why not): running, panel.json said by
    a live pid, and it is this copy's. `feed_where`: what the feed is called on this platform, "running"
    (Linux: its own process) or "in the panel" (macOS runs it in-process)."""
    for key in ("running", "said", "ours"):
        if st.get(key) is not True:
            return False, f"status says {key}: {st.get(key)!r} (pid {st.get('pid')!r}, from {st.get('where')!r})"
    if not isinstance(st.get("pid"), int):
        return False, f"no pid: {st.get('pid')!r}"
    if st.get("feed") != feed_where:
        return False, f"the feed is {st.get('feed')!r}, expected {feed_where!r}"
    return True, f"pid {st['pid']}, feed {st['feed']}"


def page_said(st):
    """Whether the page has loaded and posted to the panel: the rows of its stats column are in
    panel.json only once its script has run and reached the panel's bridge (hud.js postStats)."""
    rows = st.get("stats")
    if isinstance(rows, list) and rows:
        return True, f"{len(rows)} rows, e.g. {rows[0]}"
    return False, f"panel.json carries no stats column yet (shown={st.get('shown')!r}, wpm={st.get('wpm')!r})"


def layer_surfaces(doc, namespaces=HYPR_NAMESPACES):
    """{namespace: entry} of this HUD's surfaces in `hyprctl layers -j`: monitor -> {"levels": {level:
    [entry]}}, the shape host/cli.py hypr_surfaces reads."""
    found = {}
    if isinstance(doc, dict):
        for monitor in doc.values():
            levels = monitor.get("levels") if isinstance(monitor, dict) else None
            for entries in (levels or {}).values():
                for e in entries if isinstance(entries, list) else []:
                    if isinstance(e, dict) and e.get("namespace") in namespaces:
                        found[e["namespace"]] = e
    return found


def hypr_present(doc):
    """(ok, detail): both of the HUD's surfaces are on Hyprland's layers."""
    found = layer_surfaces(doc)
    missing = [n for n in HYPR_NAMESPACES if n not in found]
    if missing:
        return False, f"missing from `hyprctl layers`: {', '.join(missing)}"
    return True, ", ".join(f"{n} {found[n].get('w')}x{found[n].get('h')} at {found[n].get('x')},{found[n].get('y')}"
                           for n in HYPR_NAMESPACES)


def windows_of(pid, infos):
    """The window list's entries (Quartz CGWindowListCopyWindowInfo) that are `pid`'s, as plain dicts."""
    out = []
    for info in infos:
        if info.get("kCGWindowOwnerPID") != pid:
            continue
        bounds = info.get("kCGWindowBounds") or {}
        out.append({"number": info.get("kCGWindowNumber"), "layer": info.get("kCGWindowLayer"),
                    "alpha": float(info.get("kCGWindowAlpha", 1.0)), "onscreen": bool(info.get("kCGWindowIsOnscreen", False)),
                    "x": float(bounds.get("X", 0)), "y": float(bounds.get("Y", 0)),
                    "width": float(bounds.get("Width", 0)), "height": float(bounds.get("Height", 0))})
    return out


def panel_window(windows):
    """The HUD's window: the biggest of its process's."""
    return max(windows, key=lambda w: w["width"] * w["height"], default=None)


def mac_shown(windows):
    """(ok, detail): the panel's window is there, on screen, opaque and a real size (panel.py WIDTH x HEIGHT)."""
    w = panel_window(windows)
    if w is None:
        return False, "the panel process has no window in the window list"
    ok = w["onscreen"] and w["alpha"] > 0.99 and w["width"] >= 100 and w["height"] >= 50
    return ok, f"window {w['number']}: {w['width']:.0f}x{w['height']:.0f}, alpha {w['alpha']}, on screen {w['onscreen']}, layer {w['layer']}"


def mac_hidden(windows):
    """(ok, detail): hidden is drawn as nothing: the window stays, at alpha 0 (host/macos/panel.py show_window)."""
    w = panel_window(windows)
    if w is None:
        return False, "the panel process has no window in the window list"
    return w["alpha"] < 0.01, f"window {w['number']}: alpha {w['alpha']}, on screen {w['onscreen']}"


def config_with_ble_off(text):
    """The sample config, with the Bluetooth reader off: on macOS it would ask the OS for Bluetooth,
    and on a runner there is none. A config that already says is refused rather than overridden."""
    if re.search(r"^ble\s*:", text, re.M):
        raise DriverError("the sample config already has a `ble:` key; the driver would have to merge it")
    return text.rstrip("\n") + "\n\n# the end-to-end run has no keyboard and no Bluetooth: e2e/driver.py\nble:\n  enabled: false\n"


# ---------- the feed's socket, by hand ----------

def ws_accept(key):
    return base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()


def ws_request(path, host, key, origin=None):
    lines = [f"GET {path} HTTP/1.1", f"Host: {host}", "Upgrade: websocket", "Connection: Upgrade",
             f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13"]
    if origin:
        lines.append(f"Origin: {origin}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


def parse_response_head(data):
    """(status, headers, rest) from the start of an HTTP response; None while the head is incomplete."""
    head, sep, rest = data.partition(b"\r\n\r\n")
    if not sep:
        return None
    lines = head.decode("latin-1").split("\r\n")
    parts = lines[0].split(" ", 2)
    if len(parts) < 2 or not parts[0].startswith("HTTP/") or not parts[1].isdigit():
        raise DriverError(f"not an HTTP response: {lines[0][:80]!r}")
    headers = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    return int(parts[1]), headers, rest


def parse_frame(data):
    """One WebSocket frame from the front of `data` -> (fin, opcode, payload, rest); None while it is
    incomplete. A server's frames are never masked."""
    if len(data) < 2:
        return None
    fin, opcode = bool(data[0] & 0x80), data[0] & 0x0F
    masked, n, i = bool(data[1] & 0x80), data[1] & 0x7F, 2
    if n == 126:
        if len(data) < 4:
            return None
        n, i = struct.unpack(">H", data[2:4])[0], 4
    elif n == 127:
        if len(data) < 10:
            return None
        n, i = struct.unpack(">Q", data[2:10])[0], 10
    if masked:
        raise DriverError("a server frame is masked")
    if len(data) < i + n:
        return None
    return fin, opcode, data[i:i + n], data[i + n:]


class Messages:
    """Text messages out of a WebSocket's bytes, as they arrive: frames put back together, control
    frames left out."""

    def __init__(self):
        self.buf, self.partial = b"", b""
        self.closed = False

    def feed(self, data):
        self.buf += data
        out = []
        while True:
            frame = parse_frame(self.buf)
            if frame is None:
                return out
            fin, opcode, payload, self.buf = frame
            if opcode == 8:
                self.closed = True
            elif opcode in (1, 2, 0):
                self.partial += payload
                if fin:
                    if opcode != 2:
                        out.append(self.partial.decode("utf-8", "replace"))
                    self.partial = b""


def ws_connect(port, path, host="127.0.0.1", origin=None, timeout=5.0):
    """The upgrade, by hand: (HTTP status, the open socket and what followed the response head, or
    None, None when it was refused). The socket is the caller's to close."""
    key = base64.b64encode(os.urandom(16)).decode()
    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        sock.settimeout(timeout)
        sock.sendall(ws_request(path, f"{host}:{port}", key, origin))
        data = b""
        while True:
            parsed = parse_response_head(data)
            if parsed:
                break
            chunk = sock.recv(65536)
            if not chunk:
                raise DriverError("the socket closed before it answered")
            data += chunk
        status, headers, rest = parsed
        if status != 101:
            sock.close()
            return status, None, b""
        if headers.get("sec-websocket-accept") != ws_accept(key):
            raise DriverError("the upgrade came back with the wrong Sec-WebSocket-Accept")
        return status, sock, rest
    except BaseException:
        sock.close()
        raise


def client_frame(opcode, payload=b""):
    """A client's frame, which a server only takes masked (RFC 6455 5.3)."""
    mask = os.urandom(4)
    n = len(payload)
    if n >= 126:
        raise ValueError("only short control frames are made here")
    return bytes([0x80 | opcode, 0x80 | n]) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


def ws_close(sock, wait=2.0):
    """End a connection the way a WebSocket client does, with a close frame, and read what is left until the
    server hangs up. A client that just drops the connection is an abnormal closure to the feed, which logs
    it with a traceback of its own -- and this run asserts that the logs hold none."""
    try:
        sock.sendall(client_frame(8, struct.pack(">H", 1000)))
        sock.settimeout(wait)
        while sock.recv(4096):
            pass
    except OSError:
        pass
    finally:
        sock.close()


def replayed(port, token, until, timeout=5.0):
    """What the feed says to a client that connects now: the messages it replays (the keymap, the layers,
    the device...), read until `until(msg)` or until the feed stops talking. -> (messages, found)."""
    status, sock, rest = ws_connect(port, "/" + token)
    if status != 101:
        raise DriverError(f"the feed refused the right token: HTTP {status}")
    messages, parser = [], Messages()
    deadline = time.monotonic() + timeout
    try:
        data = rest
        while True:
            for text in parser.feed(data):
                try:
                    msg = json.loads(text)
                except ValueError:
                    continue
                messages.append(msg)
                if isinstance(msg, dict) and until(msg):
                    return messages, msg
            left = deadline - time.monotonic()
            if left <= 0 or parser.closed:
                return messages, None
            sock.settimeout(left)
            try:
                data = sock.recv(1 << 16)
            except socket.timeout:
                return messages, None
            if not data:
                return messages, None
    finally:
        ws_close(sock)


def logs_say(text, needle):
    return any(needle in line for line in text.splitlines())


# ---------- the platform's probes ----------

class Probe:
    """What a platform can see of the HUD. `shown` and `hidden` say what a visible and a hidden HUD look
    like from outside; both return (ok, detail)."""

    name = "?"
    feed = "?"                 # what `status --json` calls the feed there
    doctor_says = "?"          # what `doctor` says of the platform

    def __init__(self, drv):
        self.drv = drv

    def preflight(self):
        """Raise DriverError for what this run needs of the machine and does not have."""

    def present(self):
        raise NotImplementedError

    def shown(self):
        return self.present()

    def hidden(self):
        return self.present()

    def gone(self):
        """(ok, detail): nothing of the HUD is left in the platform's view of it."""
        raise NotImplementedError

    def screenshot(self, path):
        raise NotImplementedError

    def collect(self, directory):
        """Whatever else the platform can say, for the artifacts."""

    def extras(self, report):
        """Checks only this platform has."""


class HyprlandProbe(Probe):
    name = "hyprland"
    feed = "running"
    doctor_says = "platform: Linux with Hyprland"

    def hyprctl(self, *args, timeout=15):
        try:
            p = subprocess.run(["hyprctl", *args], capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as e:
            raise DriverError(f"hyprctl {' '.join(args)}: {e}")
        if p.returncode != 0:
            raise DriverError(f"hyprctl {' '.join(args)} exited {p.returncode}: {(p.stderr or p.stdout).strip()[:200]}")
        return p.stdout

    def layers(self):
        try:
            return json.loads(self.hyprctl("layers", "-j"))
        except ValueError as e:
            raise DriverError(f"`hyprctl layers -j` is not JSON: {e}")

    def preflight(self):
        for var in ("HYPRLAND_INSTANCE_SIGNATURE", "XDG_RUNTIME_DIR"):
            if not os.environ.get(var):
                raise DriverError(f"{var} is not set: this needs a running Hyprland (e2e/linux/run-in-arch.sh starts one)")
        self.drv.log("hyprctl version: " + self.hyprctl("version").splitlines()[0])
        monitors = json.loads(self.hyprctl("monitors", "-j"))
        if not monitors:
            raise DriverError("Hyprland has no monitor to put the HUD on")

    def present(self):
        return hypr_present(self.layers())

    def gone(self):
        left = layer_surfaces(self.layers())
        if left:
            return False, "still on Hyprland's layers: " + ", ".join(sorted(left))
        return True, "no zmkhud surface on Hyprland's layers"

    def screenshot(self, path):
        p = subprocess.run(["grim", str(path)], capture_output=True, text=True, timeout=30)
        return p.returncode == 0 and Path(path).exists(), (p.stderr or "").strip()[:200]

    def collect(self, directory):
        for name, args in (("layers", ("layers", "-j")), ("clients", ("clients", "-j")), ("monitors", ("monitors", "-j")),
                           ("binds", ("binds", "-j")), ("configerrors", ("configerrors",)), ("version", ("version", "-j"))):
            try:
                (directory / f"hyprctl-{name}.txt").write_text(self.hyprctl(*args))
            except (DriverError, OSError) as e:
                (directory / f"hyprctl-{name}.txt").write_text(f"unavailable: {e}\n")

    def extras(self, report):
        """hud.sh writes the global shortcuts (host/shortcuts.py) into ~/.config/hypr and has Hyprland read its
        config again: the files are there, and Hyprland has them as binds, and no error."""
        hypr = Path(os.path.expanduser("~/.config/hypr"))
        written = hypr / "zmk-layer-hud.lua"
        if not written.exists():
            written = hypr / "zmk-layer-hud.conf"
        report.check("shortcuts: hud.sh wrote the binds", written.exists(), str(written))
        try:
            errors = self.hyprctl("configerrors").strip()
            report.check("shortcuts: Hyprland read its config again without errors", not errors, errors[:300])
            binds = self.hyprctl("binds", "-j")
            has = "zmk-layer-hud" in binds or "ZMK HUD" in binds
            report.check("shortcuts: Hyprland lists the toggle bind", has, "a bind naming zmk-layer-hud" if has else "none in `hyprctl binds -j`")
        except DriverError as e:
            report.failed("shortcuts: asking Hyprland", str(e))


class MacProbe(Probe):
    name = "macos"
    feed = "in the panel"
    doctor_says = "platform: macOS, native overlay panel"

    def __init__(self, drv):
        super().__init__(drv)
        self._quartz = None

    def quartz(self):
        if self._quartz is None:
            try:
                import Quartz
            except ImportError as e:
                raise DriverError(f"cannot import Quartz ({e}): run this under the venv, with pyobjc-framework-Quartz installed")
            self._quartz = Quartz
        return self._quartz

    def preflight(self):
        self.quartz()
        off = Path.home() / ".config" / "zmk-layer-hud" / "menubar-off"
        if not off.exists():
            raise DriverError(f"the menubar icon is a process of its own that `stop` leaves running: touch {off} first (CI does)")

    def all_windows(self):
        Q = self.quartz()
        infos = Q.CGWindowListCopyWindowInfo(Q.kCGWindowListOptionAll, Q.kCGNullWindowID) or []
        return [dict(i) for i in infos]

    def windows(self):
        pid = self.drv.panel_pid
        if pid is None:
            raise DriverError("no panel pid to look for")
        return windows_of(pid, self.all_windows())

    def present(self):
        return mac_shown(self.windows())

    def shown(self):
        return mac_shown(self.windows())

    def hidden(self):
        return mac_hidden(self.windows())

    def gone(self):
        pid = self.drv.panel_pid
        left = windows_of(pid, self.all_windows()) if pid is not None else []
        if left:
            return False, f"{len(left)} window(s) of pid {pid} still listed"
        return True, f"no window of pid {pid}"

    def screenshot(self, path):
        p = subprocess.run(["screencapture", "-x", str(path)], capture_output=True, text=True, timeout=30)
        return p.returncode == 0 and Path(path).exists(), (p.stderr or "").strip()[:200]

    def collect(self, directory):
        """Every window the list shows, whoever's: if the panel's is missing, what there is instead says why."""
        def plain(v):
            if v is None or isinstance(v, (bool, int, float, str)):
                return v
            if hasattr(v, "keys"):
                return {str(k): plain(v[k]) for k in v.keys()}
            return str(v)

        try:
            keys = ("kCGWindowOwnerPID", "kCGWindowOwnerName", "kCGWindowNumber", "kCGWindowLayer",
                    "kCGWindowAlpha", "kCGWindowIsOnscreen", "kCGWindowBounds", "kCGWindowName")
            summary = [{k: plain(info.get(k)) for k in keys} for info in self.all_windows()]
            (directory / "windows-all.json").write_text(json.dumps(summary, indent=1))
        except (DriverError, OSError, ValueError, TypeError) as e:
            (directory / "windows-all.json").write_text(f"unavailable: {e}\n")


PROBES = {"hyprland": HyprlandProbe, "macos": MacProbe}


# ---------- the run itself ----------

class Driver:
    def __init__(self, args):
        self.args = args
        self.timeout = args.timeout
        self.artifacts = Path(args.artifacts).resolve()
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.work = Path(args.work).resolve() if args.work else Path(tempfile.mkdtemp(prefix="zmkhud-e2e-"))
        self.state = self.work / "state"
        self.cmds = self.artifacts / "commands"
        self.cmds.mkdir(exist_ok=True)
        self.logfile = open(self.artifacts / "driver.log", "w", encoding="utf-8")
        self.report = Report(echo=self.log)
        self.port = int(os.environ.get("ZMKHUD_PORT") or 8766)
        self.panel_pid = None
        self.started = False        # `start` was run by this driver: only then is `stop` its to run
        self.seq = itertools.count(1)
        self.env = None
        self.probe = PROBES[args.probe](self)

    def log(self, msg):
        print(msg, flush=True)
        self.logfile.write(msg + "\n")
        self.logfile.flush()

    # ----- the command line -----

    def cli(self, *args, timeout=60.0):
        """`zmk-layer-hud ...`, its output kept in the artifacts. -> (exit code or None on a timeout, output)."""
        path = self.cmds / f"{next(self.seq):02d}-{args[0] if args else 'help'}.log"
        with open(path, "wb") as out:
            try:
                rc = subprocess.run([str(CLI), *args], stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                    env=self.env, timeout=timeout).returncode
            except subprocess.TimeoutExpired:
                rc = None
        text = path.read_text(errors="replace")
        self.log(f"$ zmk-layer-hud {' '.join(args)} -> {rc}\n" + "".join("    " + l + "\n" for l in text.splitlines()[-30:]).rstrip("\n"))
        return rc, text

    def status(self):
        """`status --json`, parsed."""
        try:
            p = subprocess.run([str(CLI), "status", "--json"], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                               env=self.env, timeout=60)
        except subprocess.TimeoutExpired:
            raise DriverError("`status --json` did not answer in 60 s")
        if p.returncode != 0:
            raise DriverError(f"`status --json` exited {p.returncode}: {(p.stderr or p.stdout).strip()[:200]}")
        return parse_status(p.stdout)

    def logs(self):
        text = ""
        for name in ("panel.log", "hudfeed.log"):
            try:
                text += (self.state / name).read_text(errors="replace")
            except OSError:
                pass
        return text

    def processes(self):
        """{pid: command} of this tree's panels and feeds."""
        here = os.path.realpath(ROOT)
        found = {}
        for pattern in (panelstate.PANEL_PROCESS, panelstate.FEED_PROCESS):
            for pid, command in panelstate.processes(pattern).items():
                if panelstate.tree_of(command) == here:
                    found[pid] = command
        return found

    def port_open(self):
        try:
            socket.create_connection(("127.0.0.1", self.port), timeout=1).close()
        except OSError:
            return False
        return True

    # ----- before -----

    def prepare(self):
        r = self.report
        if not (os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS") or self.args.local):
            raise DriverError("this starts and stops the HUD of this tree, and `stop` ends every panel of it: "
                              "run it in CI, or pass --local to say you mean it")
        if not (ROOT / ".venv" / "bin" / "python3").exists():
            raise DriverError("no .venv: `bin/zmk-layer-hud setup --venv-only` first (the verbs that start the HUD need it)")
        shutil.rmtree(self.state, ignore_errors=True)
        self.state.mkdir(parents=True, mode=0o700)
        config = self.work / "config" / "example-3x5.yaml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(config_with_ble_off((ROOT / "config" / "example-3x5.yaml").read_text(encoding="utf-8")), encoding="utf-8")
        shutil.copy(ROOT / "config" / "example-3x5.definitions.json", config.parent / "example-3x5.definitions.json")
        (self.work / "examples").mkdir(exist_ok=True)       # the config's `keymap: ../examples/3x5.yaml`, which only import reads
        shutil.copy(ROOT / "examples" / "3x5.yaml", self.work / "examples" / "3x5.yaml")
        self.env = dict(os.environ, ZMKHUD_STATE=str(self.state), ZMKHUD_CONFIG=str(config), ZMKHUD_ROOT=str(ROOT))
        self.env.pop("ZMKHUD_TOKEN", None)         # poke reads the run's own from $STATE/token
        self.env.pop("ZMKHUD_HIDDEN", None)
        self.env.pop("ZMKHUD_RESERVE", None)
        self.probe.preflight()
        stray = self.processes()
        if stray:
            raise DriverError(f"a HUD of this tree is already running ({stray}); this would end it")
        r.passed("setup", f"{self.probe.name}; state {self.state}; config {config} with ble off; port {self.port}")

    # ----- the run -----

    def start(self):
        r = self.report
        self.started = True
        rc, text = self.cli("start", timeout=180)
        if rc != 0:
            r.failed("start", f"`zmk-layer-hud start` exited {rc}: {text.strip()[-300:]}")
            raise DriverError("the HUD did not start")
        r.passed("start", "`zmk-layer-hud start` exited 0")

    def assert_up(self):
        r = self.report
        feed = self.probe.feed
        detail = wait_for(lambda: panel_up(self.status(), feed), "a panel behind `status --json`", self.timeout, interval=1.0)
        r.passed("panel is up", detail)
        st = self.status()
        self.panel_pid = st["pid"]
        panel_json = panelstate.read(str(self.state))
        r.check("panel.json is the live panel's", bool(panel_json) and panel_json["pid"] == self.panel_pid,
                f"panel.json {panel_json and panel_json.get('pid')}, status {self.panel_pid}")
        try:
            detail = wait_for(lambda: page_said(self.status()), "the page to say its stats column", self.timeout, interval=1.0)
            r.passed("page loaded", detail)
        except DriverError as e:
            r.failed("page loaded", str(e))

    def assert_surfaces(self):
        r = self.report
        try:
            r.passed("surfaces are shown", wait_for(self.probe.shown, "the HUD's surfaces", self.timeout, interval=1.0))
        except DriverError as e:
            if self.args.surfaces == "best-effort":
                r.skipped("surfaces are shown", f"{e} -- the platform's window list does not show this HUD, so only panel.json and the process back it")
            else:
                r.failed("surfaces are shown", str(e))

    def assert_doctor(self):
        r = self.report
        rc, text = self.cli("doctor", timeout=180)
        r.check("doctor runs", rc is not None and "Traceback" not in text, f"exit {rc}")
        r.check("doctor names the platform", self.probe.doctor_says in text, self.probe.doctor_says)
        r.check("doctor reports the panel", "panel: running" in text, "panel: running")
        rc, text = self.cli("status", timeout=60)
        r.check("status says running, shown", rc == 0 and "panel: running, shown" in text, text.strip().splitlines()[0] if text.strip() else "no output")

    def assert_socket(self):
        r = self.report
        token_path = self.state / panelstate.TOKEN_FILE
        try:
            wait_for(lambda: (token_path.exists(), f"{token_path} is not there"), "the feed's token file", self.timeout)
        except DriverError as e:
            r.failed("socket: token file", str(e))
            return
        mode = stat.S_IMODE(token_path.stat().st_mode)
        r.check("socket: token file is 0600", mode == 0o600, oct(mode))
        token = panelstate.read_token(str(self.state))
        wait_for(self.port_is_serving, "the feed's socket", self.timeout)

        status, sock, _ = ws_connect(self.port, "/" + token)
        if sock:
            ws_close(sock)
        r.check("socket: the right token upgrades", status == 101, f"HTTP {status}")
        for name, path, origin in (("a wrong token", "/" + "0" * len(token), None), ("no token", "/", None),
                                   ("the right token from a foreign Origin", "/" + token, FOREIGN_ORIGIN)):
            status, sock, _ = ws_connect(self.port, path, origin=origin)
            if sock:
                ws_close(sock)
            r.check(f"socket: {name} gets HTTP 403", status == 403, f"HTTP {status}")
        text = self.logs()
        for needle in ("refused a client (wrong token)", "refused a client (no token)", f"refused a client (origin {FOREIGN_ORIGIN})"):
            r.check(f"socket: the feed logged `{needle}`", logs_say(text, needle))
        rc, out = self.cli("poke", "--url", f"ws://127.0.0.1:{self.port}/" + "0" * len(token), "--layers", "1", timeout=60)
        r.check("poke: a wrong token is refused", rc not in (0, None) and "HTTP 403" in out, f"exit {rc}")

        rc, out = self.cli("poke", "--layers", "1", timeout=60)
        r.check("poke: accepted", rc == 0 and "sent 1 message" in out, f"exit {rc}")
        messages, found = replayed(self.port, token, lambda m: m.get("kind") == "layers" and m.get("sent") is True)
        r.check("poke: fanned out (a client that connects now is replayed the layers poked)",
                found is not None and found.get("ids") == [1],
                f"replayed {[m.get('kind') for m in messages if isinstance(m, dict)]}; layers {found and found.get('ids')}")
        rc, out = self.cli("poke", "--type", "e2e", timeout=60)
        r.check("poke: typing is accepted", rc == 0 and "sent" in out, f"exit {rc}")

    def port_is_serving(self):
        return self.port_open(), f"nothing listens on 127.0.0.1:{self.port}"

    def assert_control(self):
        r = self.report

        def shown_is(want):
            def probe():
                st = self.status()
                return st.get("shown") is want, f"status says shown={st.get('shown')!r}"
            return probe

        for verb, want in (("hide", False), ("show", True), ("toggle", False), ("toggle", True)):
            label = f"{verb} (to {'shown' if want else 'hidden'})"
            rc, out = self.cli(verb, timeout=60)
            if not r.check(f"{label}: exits 0", rc == 0, f"exit {rc}: {out.strip()[-120:]}"):
                continue
            try:
                wait_for(shown_is(want), f"`status --json` to say shown={want}", 15.0, interval=0.5)
                from_file = panelstate.read(str(self.state))
                r.check(f"{label}: status and panel.json say shown={want}", bool(from_file) and from_file["shown"] is want, f"panel.json {from_file and from_file.get('shown')}")
            except DriverError as e:
                r.failed(f"{label}: shown becomes {want}", str(e))
                continue
            look = self.probe.shown if want else self.probe.hidden
            try:
                r.passed(f"{label}: the surfaces follow", wait_for(look, "the surfaces to follow", 15.0, interval=0.5))
            except DriverError as e:
                if self.args.surfaces == "best-effort":
                    r.skipped(f"{label}: the surfaces follow", f"{e} -- not visible to the window list on this machine")
                else:
                    r.failed(f"{label}: the surfaces follow", str(e))

    def screenshot(self, name):
        path = self.artifacts / f"screenshot-{name}.png"
        try:
            ok, why = self.probe.screenshot(path)
        except (OSError, subprocess.SubprocessError) as e:
            ok, why = False, str(e)
        self.report.note(f"screenshot {name}", f"saved {path.name}" if ok else f"not taken: {why}")

    # ----- after -----

    def shutdown(self):
        r = self.report
        if not self.started:
            return
        rc, out = self.cli("stop", timeout=120)
        r.check("stop: exits 0", rc == 0, f"exit {rc}")
        try:
            detail = wait_for(lambda: (lambda left: (not left, f"still running: {left}" if left else "none left"))(self.processes()),
                              "the panel and feed to go", 30.0, interval=0.5)
            r.passed("stop: no panel or feed process is left", detail)
        except DriverError as e:
            r.failed("stop: no panel or feed process is left", str(e))
        try:
            wait_for(lambda: (not self.port_open(), f"127.0.0.1:{self.port} still accepts connections"), "the socket to close", 15.0)
            r.passed("stop: the socket is closed", f"nothing listens on 127.0.0.1:{self.port}")
        except DriverError as e:
            r.failed("stop: the socket is closed", str(e))
        try:
            st = self.status()
            r.check("stop: status says the panel is stopped", st.get("running") is False, f"running={st.get('running')!r}")
        except DriverError as e:
            r.failed("stop: status", str(e))
        try:
            r.passed("stop: the platform shows no surface", wait_for(self.probe.gone, "the surfaces to go", 15.0, interval=0.5))
        except DriverError as e:
            r.failed("stop: the platform shows no surface", str(e))
        r.note("stop: token file", "removed" if not (self.state / panelstate.TOKEN_FILE).exists() else "left behind (not asserted)")
        text = self.logs()
        r.check("no Traceback in the panel's or the feed's log", "Traceback" not in text,
                next((l for l in text.splitlines() if "Traceback" in l), ""))

    def collect(self):
        """The state directory's files (never the token: it is this run's socket key) and the platform's own
        listings, into the artifacts."""
        try:
            shutil.copytree(self.state, self.artifacts / "state", dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(panelstate.TOKEN_FILE, "*.tmp"))
        except (OSError, shutil.Error) as e:
            self.log(f"could not copy the state directory: {e}")
        try:
            self.probe.collect(self.artifacts)
        except Exception as e:     # noqa: BLE001 -- the artifacts are best effort; the verdict is not theirs
            self.log(f"could not collect the platform's listings: {e}")
        (self.artifacts / "report.json").write_text(json.dumps(self.report.results, indent=1))
        table = render_summary(self.report.results, f"End-to-end: {self.probe.name}")
        (self.artifacts / "summary.md").write_text(table, encoding="utf-8")
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:                 # the macOS job's page; the Arch container cannot reach the runner's file, so its job copies summary.md
            with open(summary, "a", encoding="utf-8") as f:
                f.write(table)

    def run(self):
        r = self.report
        try:
            self.prepare()
            self.start()
            self.assert_up()
            self.assert_surfaces()
            self.probe.extras(r)
            self.assert_doctor()
            self.assert_socket()
            self.screenshot("shown")
            self.assert_control()
        except DriverError as e:
            r.failed("run", str(e))
        except Exception as e:     # noqa: BLE001 -- a bug here is a failed run with its message, not a traceback with no report
            import traceback
            r.failed("driver", f"{type(e).__name__}: {e}")
            self.log(traceback.format_exc())
        finally:
            try:
                self.shutdown()
            except Exception as e:     # noqa: BLE001
                r.failed("stop", f"{type(e).__name__}: {e}")
            self.collect()
        if not r.ok:
            self.log("\n--- the logs' last lines ---\n" + "\n".join(self.logs().splitlines()[-60:]))
        self.log(f"\n{r.count(PASS)} passed, {r.count(FAIL)} failed, {r.count(SKIP)} skipped")
        return 0 if r.ok else 1


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--artifacts", required=True, metavar="DIR", help="where the logs, the state directory, the listings and the screenshots go")
    p.add_argument("--probe", choices=sorted(PROBES), default="macos" if sys.platform == "darwin" else "hyprland")
    p.add_argument("--surfaces", choices=("require", "best-effort"), default="require",
                   help="best-effort: a platform whose window list does not show the HUD skips the surface checks (said so, "
                        "never passed) and panel.json and the process back the run")
    p.add_argument("--timeout", type=float, default=TIMEOUT, help="seconds to wait for the HUD to come up (default %(default)s)")
    p.add_argument("--work", metavar="DIR", help="the isolated state and config go here (default: a temporary directory)")
    p.add_argument("--local", action="store_true", help="run outside CI: this ends every HUD of this tree")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    drv = Driver(args)
    return drv.run()


if __name__ == "__main__":
    sys.exit(main())
