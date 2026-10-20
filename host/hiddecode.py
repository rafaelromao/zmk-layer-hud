"""What the keyboard's reports mean: its signal messages -> the pages' layers / key / flagsChanged
messages, by the key vocabulary (host/keynames.py). Pure and standard library only: the feed
(host/hudfeed.py) runs it on what its readers bring in, host/uskeys.py on what a keyboard would
send, and the page has its port, hud/hidtext.js, on the same tables (hud/keynames.js).
"""

import unicodedata

from keynames import ALT_CHARS, ALTGR_CHARS, CHARS, CONTROL_CHARS, DEAD_KEY_SPECIAL, DEAD_KEYS, MOD_BITS, NAMED, RALT


def flags_of(mods):
    f = {"cmd": False, "ctrl": False, "alt": False, "shift": False, "fn": False}
    for bit, name in MOD_BITS.items():
        if mods & bit:
            f[name] = True
    return f


def key_message(usage, down, flags, altgr=False):
    """A key press/release -> the HUD's key event. `altgr`: the right Alt is down and is AltGr."""
    if altgr and (usage, flags["shift"]) in ALTGR_CHARS:
        chars = ALTGR_CHARS[(usage, flags["shift"])]
        # AltGr picks a level of the key, as Shift does: what it typed is a character, not a chord.
        return {"kind": "key", "type": "keyDown" if down else "keyUp", "name": chars, "chars": chars,
                "code": usage, "flags": dict(flags, alt=False), "repeat": False}
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

    def __init__(self, compose=True, dead_key_ms=DEAD_KEY_MS, altgr=False):
        self.compose = compose
        self.altgr = altgr   # the right Alt is AltGr (ALTGR_CHARS), not Option
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
        altgr = self.altgr and bool(mods & RALT)
        for k in self.held:
            if k not in held:
                out.extend(self._up(key_message(k, False, flags, altgr)))
        for k in held:
            if k not in self.held:
                out.extend(self._down(key_message(k, True, flags, altgr), now_ms))
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
