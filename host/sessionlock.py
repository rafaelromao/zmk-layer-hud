"""The session lock, on Linux under Hyprland: whether the screen is locked, which is when the
unlock password is typed.

A locker -- hyprlock, Omarchy's shell, swaylock -- is no toolkit's widget tree, so it is not on
the accessibility bus (host/a11y.py), and it is not a terminal (hudfeed.terminal_probe): neither
of the feed's other probes sees it, and the unlock password went to the strip, the counts and
the socket's clients, the strip showing it for a moment after the screen came back. logind's
LockedHint, which a desktop's locker would set, is set by none of these lockers. What there is
is Hyprland's own word: a session lock (ext-session-lock) is a solitary blocker on every monitor,
which `hyprctl -j monitors` reports in `solitaryBlockedBy` as "LOCK" -- what Omarchy's own
omarchy-hyprland-session-locked reads. This asks Hyprland's control socket for that, at every
keystroke and poll (hudfeed.SecureInput), and keeps the answer for KEEP_S: the lock changes
rarely, and a round trip to the compositor per key is not wanted.

A Hyprland that does not say (an older one, with no such key) is asked no further, and the
lockers are looked for by process name instead, which is less: a locker whose name is not known
here, or Omarchy's shell (`qs`, which is also the bar, so never a sign), is not seen. No Hyprland
at all -- the feed runs under a browser page on any Linux -- is no probe (`probe` is None), as
no terminals is for terminal_probe.
"""

import json
import os
import socket
import sys
import threading
import time

LOCKERS = ("hyprlock", "swaylock", "gtklock", "waylock")    # by name, where Hyprland does not say
TIMEOUT_S = 1.0             # for Hyprland's control socket
KEEP_S = 0.1                # how long an answer stands before Hyprland is asked again


def socket_path(environ=os.environ):
    """Hyprland's control socket for this session, or None where there is no Hyprland."""
    signature = environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not signature:
        return None
    runtime = environ.get("XDG_RUNTIME_DIR")
    for base in ((os.path.join(runtime, "hypr") if runtime else None), "/tmp/hypr"):   # the older place
        if base:
            path = os.path.join(base, signature, ".socket.sock")
            if os.path.exists(path):
                return path
    return None


def available(environ=os.environ):
    return sys.platform == "linux" and socket_path(environ) is not None


def monitors(path, timeout_s=TIMEOUT_S):
    """Hyprland's monitors as `hyprctl -j monitors` says them: one request per connection."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout_s)
        s.connect(path)
        s.sendall(b"j/monitors")
        chunks = []
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    got = json.loads(b"".join(chunks).decode("utf-8"))
    return got if isinstance(got, list) else []


def locked(path, timeout_s=TIMEOUT_S):
    """True while a session lock holds the screen, False while Hyprland says none does, None where
    it does not say: no monitors, no `solitaryBlockedBy` (an older Hyprland), or no answer."""
    try:
        said = [m.get("solitaryBlockedBy") for m in monitors(path, timeout_s) if isinstance(m, dict)]
    except (OSError, ValueError):
        return None
    if not said or any(not isinstance(by, list) for by in said):
        return None
    return any("LOCK" in by for by in said)


def locker_running(names=LOCKERS, proc="/proc", uid=None):
    """Whether a process of this user named as a locker is running (/proc/<pid>/comm)."""
    uid = os.getuid() if uid is None else uid
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return False
    for pid in pids:
        d = os.path.join(proc, pid)
        try:
            if os.stat(d).st_uid != uid:
                continue
            with open(os.path.join(d, "comm"), encoding="utf-8", errors="replace") as f:
                if f.read().strip() in names:
                    return True
        except OSError:         # gone meanwhile, or not this user's to read
            continue
    return False


class SessionLock:
    """Whether the session is locked, asked of Hyprland and kept for `keep_s`. `probe()` is what
    SecureInput asks, from the reader threads and the poll at once, hence the lock."""

    def __init__(self, path, log=print, keep_s=KEEP_S, clock=time.monotonic, proc="/proc"):
        self.path, self.log, self.keep_s, self.clock, self.proc = path, log, keep_s, clock, proc
        self.said = None
        self.lock = threading.Lock()
        self._at, self._was = None, False

    def probe(self):
        now = self.clock()
        with self.lock:
            if self._at is not None and now - self._at < self.keep_s:
                return self._was
            got = locked(self.path)
            if got is None:
                if self.said != "by-name":
                    self.said = "by-name"
                    self.log("sessionlock: this Hyprland does not say whether the session is locked (no "
                             "solitaryBlockedBy in `hyprctl -j monitors`): a locker is looked for by name "
                             f"instead ({', '.join(LOCKERS)}), and one not named here is not seen")
                got = locker_running(proc=self.proc)
            self._at, self._was = now, got
            return got


def probe(log=print, environ=os.environ):
    """-> whether the session is locked (SessionLock.probe), or None where there is no Hyprland."""
    path = socket_path(environ) if sys.platform == "linux" else None
    return SessionLock(path, log=log).probe if path else None
