"""Typing for the touch keyboard: what the HUD's page sends, pressed at the operating system.

The page runs a virtual firmware (hud/engine.js) and sends what a keyboard sends: report snapshots,
`{"kind":"hid","v":1,"seq":n,"mods":b,"keys":[usage...]}`, the modifier byte and the keyboard-page
usages held (docs/touch.md). The host's job is the one a USB stack does with a keyboard's reports:
see what changed since the last one and press or release those keys. The operating system then
applies the user's layout, as it does for the physical keyboard, so a US-International dead key is a
dead key and Option-e an accent.

A snapshot, never a press or a release on its own: a lost message is mended by the next one, and an
empty one lets everything go. A page that goes quiet -- a crash, a reload, a tablet that lost the
Wi-Fi -- would leave keys down, so a page holding anything says it again every half second, and a
source silent for STALE_S has its keys let go here. Each source (the panel's page, a paired tablet)
keeps its own snapshot and what is pressed is their union, so one letting go cannot drop a key the
other holds.

Nothing is trusted that the page says beyond what a keyboard could: ten keys at most, the
modifiers folded out of them, the Power key never (DENY), a message no longer than MAX_BYTES, and at
most MAX_RATE of them a second.

The backends press the keys: host/linux/uinput.py (a virtual input device) and
host/macos/cgevent.py (CoreGraphics events). Each takes `send([(page, usage, down), ...])`.

Stdlib only, and Python 3.9: the Linux panel runs under the system interpreter.
"""

import collections
import json
import time

KEY_PAGE, CONSUMER_PAGE = 7, 12
MAX_KEYS = 10
MAX_CONSUMER = 4
MAX_BYTES = 512
MAX_RATE = 100            # messages a second from one source
STALE_S = 1.5             # a source silent this long has its keys let go
DENY = frozenset({0x66})  # Keyboard Power: a key the page has no business pressing
MOD_FIRST, MOD_LAST = 0xE0, 0xE7


class Bad(ValueError):
    """A message that is not a report snapshot this takes. What it was is never logged."""


Report = collections.namedtuple("Report", "mods keys consumer")
EMPTY = Report(0, (), ())


def parse(msg):
    """A page's `hid` message as a Report, checked, or Bad."""
    if isinstance(msg, (bytes, str)):
        if len(msg) > MAX_BYTES:
            raise Bad("too long")
        try:
            msg = json.loads(msg)
        except ValueError:
            raise Bad("not JSON") from None
    if not isinstance(msg, dict) or msg.get("kind") != "hid" or msg.get("v") != 1:
        raise Bad("not a v1 hid message")
    if msg.get("release") is True:
        return EMPTY
    mods = msg.get("mods", 0)
    keys = msg.get("keys", [])
    consumer = msg.get("consumer", [])
    ok = lambda n, top: isinstance(n, int) and not isinstance(n, bool) and 0 <= n <= top  # noqa: E731
    if not ok(mods, 0xFF) or not isinstance(keys, list) or not isinstance(consumer, list):
        raise Bad("malformed")
    if len(keys) > MAX_KEYS or len(consumer) > MAX_CONSUMER:
        raise Bad("too many keys")
    out = []
    for k in keys:
        if not ok(k, 0xFF):
            raise Bad("malformed key")
        if MOD_FIRST <= k <= MOD_LAST:
            mods |= 1 << (k - MOD_FIRST)
        elif k and k not in DENY and k not in out:
            out.append(k)
    cons = []
    for c in consumer:
        if not ok(c, 0xFFF):
            raise Bad("malformed consumer key")
        if c and c not in cons:
            cons.append(c)
    return Report(mods, tuple(out), tuple(cons))


def changes(before, after):
    """What turns one report into the other: keys up, then modifiers up, then modifiers down, then
    keys down, as [(page, usage, down)] -- so a shifted key never types unshifted on its way."""
    out = []
    for k in before.keys:
        if k not in after.keys:
            out.append((KEY_PAGE, k, False))
    for c in before.consumer:
        if c not in after.consumer:
            out.append((CONSUMER_PAGE, c, False))
    for bit in range(8):
        if before.mods & (1 << bit) and not after.mods & (1 << bit):
            out.append((KEY_PAGE, MOD_FIRST + bit, False))
    for bit in range(8):
        if after.mods & (1 << bit) and not before.mods & (1 << bit):
            out.append((KEY_PAGE, MOD_FIRST + bit, True))
    for k in after.keys:
        if k not in before.keys:
            out.append((KEY_PAGE, k, True))
    for c in after.consumer:
        if c not in before.consumer:
            out.append((CONSUMER_PAGE, c, True))
    return out


def union(reports):
    mods, keys, consumer = 0, [], []
    for r in reports:
        mods |= r.mods
        keys += [k for k in r.keys if k not in keys]
        consumer += [c for c in r.consumer if c not in consumer]
    return Report(mods, tuple(keys[:MAX_KEYS]), tuple(consumer[:MAX_CONSUMER]))


class Typist:
    """The keys the operating system has down, kept equal to the union of every source's last
    snapshot. `apply` takes a source's message; `tick` (call it every few hundred ms) lets a silent
    source go; `release` lets one go, or all of them -- a reload, a hide, a quit."""

    def __init__(self, backend, clock=time.monotonic, stale_s=STALE_S, max_rate=MAX_RATE):
        self.backend, self.clock, self.stale_s, self.max_rate = backend, clock, stale_s, max_rate
        self.sources = {}          # name -> {"report", "seen", "seq", "times"}
        self.pressed = EMPTY       # what the backend has down

    def apply(self, source, msg):
        """True when the message was taken. A bad one is refused (Bad); one over the rate limit, or
        older than one already taken from that source, is dropped (False)."""
        report = parse(msg)
        now = self.clock()
        s = self.sources.setdefault(source, {"report": EMPTY, "seen": now, "seq": None, "times": collections.deque()})
        while s["times"] and now - s["times"][0] > 1.0:
            s["times"].popleft()
        if len(s["times"]) >= self.max_rate:
            return False
        s["times"].append(now)
        seq = msg.get("seq") if isinstance(msg, dict) else None
        if isinstance(seq, int) and not isinstance(seq, bool):
            if s["seq"] is not None and seq <= s["seq"]:
                return False
            s["seq"] = seq
        s["report"], s["seen"] = report, now
        self._sync()
        return True

    def release(self, source=None):
        """Let a source's keys go (it reloaded, it left), or every source's."""
        if source is None:
            self.sources.clear()
        else:
            self.sources.pop(source, None)
        self._sync()

    def tick(self):
        """Let go of what a silent source held. Returns the sources let go."""
        now = self.clock()
        silent = [name for name, s in self.sources.items()
                  if now - s["seen"] > self.stale_s and s["report"] != EMPTY]
        for name in silent:
            self.sources[name]["report"] = EMPTY
        if silent:
            self._sync()
        if hasattr(self.backend, "tick"):
            self.backend.tick()
        return silent

    def holding(self):
        return self.pressed != EMPTY

    def _sync(self):
        want = union(s["report"] for s in self.sources.values())
        todo = changes(self.pressed, want)
        self.pressed = want
        if todo:
            self.backend.send(todo)


class Unavailable(Exception):
    """A backend that cannot be had: `state` says why, for the page's banner -- "denied" (the
    operating system's leave is missing), "missing" (nothing to type with: no /dev/uinput), "error"."""

    def __init__(self, state, why=""):
        super().__init__(why or state)
        self.state = state


class Typing:
    """A panel's touch typing, the same on both: the backend it needs (make_backend, which raises
    Unavailable), a Typist over it, the watchdog's ticks, and what the page is told -- whether it
    may type ({"kind":"touch"}, with the sink it types into) and why not ({"kind":"typing"}).
    Asked for and refused, it tries again every retry_s, so granting the permission is enough.

    tell(msg) hands a message to the panel's page; later(seconds, fn) runs fn on the panel's loop."""

    FIX = "zmk-layer-hud typing setup"

    def __init__(self, make_backend, tell, later, pages=(KEY_PAGE, CONSUMER_PAGE), retry_s=5.0, tick_s=0.25,
                 log=None):
        self.make_backend, self.tell, self.later = make_backend, tell, later
        self.pages, self.retry_s, self.tick_s, self.log = list(pages), retry_s, tick_s, log or (lambda *a: None)
        self.wanted, self.typist, self.backend, self.state = False, None, None, "off"
        self.generation = 0          # a timer from before a stop or a start finds itself stale

    def start(self):
        if self.wanted:
            return
        self.wanted = True
        self.generation += 1
        self._try(self.generation)

    def stop(self):
        if not self.wanted:
            return
        self.wanted = False
        self.generation += 1
        self.release()
        if self.backend is not None and hasattr(self.backend, "close"):
            self.backend.close()
        self.typist = self.backend = None
        self._say("off")

    def hid(self, msg):
        if self.typist is None:
            return False
        try:
            return self.typist.apply("panel", msg)
        except Bad:
            return False

    def release(self):
        if self.typist is not None:
            self.typist.release()

    def _try(self, gen):
        if gen != self.generation or not self.wanted or self.typist is not None:
            return
        try:
            self.backend = self.make_backend()
        except Unavailable as e:
            if e.state != self.state:
                self.log(f"typing: cannot type ({e}); `{self.FIX}` sets it up")
            self._say(e.state)
            self.later(self.retry_s, lambda: self._try(gen))
            return
        self.typist = Typist(self.backend)
        self._say("ok")
        self.later(self.tick_s, lambda: self._tick(gen))

    def _tick(self, gen):
        if gen != self.generation or self.typist is None:
            return
        self.typist.tick()
        self.later(self.tick_s, lambda: self._tick(gen))

    def _say(self, state):
        self.state = state
        self.tell({"kind": "typing", "state": state, "fix": self.FIX})
        if state == "ok":
            self.tell({"kind": "touch", "on": True, "sink": {"output": "hid", "repeat": "os", "pages": self.pages}})
        else:
            self.tell({"kind": "touch", "on": False})
