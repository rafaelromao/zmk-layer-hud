"""What a US-layout keyboard sends to type a legend — the inverse of hudfeed's decode.

The HUD draws two things for one keypress: the key on the board, which comes from the keymap, and
the character in the strip below, which comes from the report snapshot the keyboard actually sent.
They can disagree. An accented letter is not one keypress but a dead key and a letter; a symbol may
need Option; Return is a named key whose glyph each table spells its own way. The only way to test
the strip is to send it what a keyboard would send and see what comes out.

The character tables are inverted from hudfeed's own at import, so they cannot drift from the
decoder they are meant to exercise. What is written down here is the part hudfeed has no opinion
about: which legend means which named key.

  python3 host/uskeys.py --events < legends    one JSON object per line, for hud/tests/strip_test.js
  python3 host/uskeys.py --corpus KEYMAP.json  the legends of a keymap message, ready for --events
"""

import json
import os
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hudfeed import (ALT_CHARS, CHARS, DEAD_KEY_SPECIAL, DEAD_KEYS, NAMED,  # noqa: E402
                     SignalDecoder)

MOD_SHIFT, MOD_ALT = 0x02, 0x04

# character -> (usage, shift, alt). Lowest usage wins, so "1" is the number row rather than the
# keypad and "\\" is 0x31 rather than 0x64 — the spelling a keyboard reaches for first.
USAGE_FOR = {}
for _usage in sorted(CHARS, reverse=True):
    plain, shifted = CHARS[_usage]
    USAGE_FOR[plain] = (_usage, False, False)
    USAGE_FOR[shifted] = (_usage, True, False)
ALT_USAGE_FOR = {}
for (_usage, _shift), _ch in sorted(ALT_CHARS.items(), reverse=True):
    if _ch:
        ALT_USAGE_FOR[_ch] = (_usage, _shift, True)

# name -> usage, for the named keys. Lowest usage wins (Return is 0x28, not the keypad's 0x58).
USAGE_FOR_NAME = {}
for _usage in sorted(NAMED, reverse=True):
    USAGE_FOR_NAME[NAMED[_usage]] = _usage

# The one place a legend is tied to a named key. keymap-drawer files spell these with glyphs, and
# hud.js and keys.js each carry their own table; this is the union, written down once.
NAME_FOR_LEGEND = {
    "␣": "space", "spc": "space", "space": "space",
    "↵": "return", "⏎": "return", "ret": "return", "enter": "return", "return": "return",
    "⎋": "escape", "esc": "escape", "escape": "escape",
    "⌫": "delete", "bspc": "delete", "backspace": "delete",
    "⌦": "forwarddelete", "del": "forwarddelete",
    "⇥": "tab", "↹": "tab", "tab": "tab",
    "←": "left", "→": "right", "↑": "up", "↓": "down",
    "⇱": "home", "⇲": "end", "⇞": "pageup", "⇟": "pagedown",
    "⎀": "insert", "⇪": "capslock",
}
for _i in range(1, 25):
    NAME_FOR_LEGEND[f"F{_i}"] = f"f{_i}"


def snapshot(usage, mods=0):
    """One `keys` message: the keyboard's report snapshot holding a single key."""
    return {"kind": "keys", "mods": mods, "keys": [usage] if usage else []}


def _mods(shift, alt):
    return (MOD_SHIFT if shift else 0) | (MOD_ALT if alt else 0)


def _tap(usage, shift=False, alt=False):
    """The press and release of one key, modifiers held across both."""
    m = _mods(shift, alt)
    return [snapshot(usage, m), snapshot(0, m)]


def keystrokes_for(legend):
    """The snapshots a US-layout keyboard sends to type `legend`, or None if it cannot.

    Order matters: a plain or shifted key first, then the dead-key pair an accent is really typed
    as (the US-International layout, which is how the keymap's accent macros type on the host),
    then the Option layer.
    """
    name = NAME_FOR_LEGEND.get(legend)
    if name and name in USAGE_FOR_NAME:
        return _tap(USAGE_FOR_NAME[name])
    if len(legend) != 1:
        return None
    if legend in USAGE_FOR:
        usage, shift, alt = USAGE_FOR[legend]
        return _tap(usage, shift, alt)
    pair = _dead_key_pair(legend)
    if pair:
        dead, letter = pair
        return _tap(*USAGE_FOR[dead][:2]) + _tap(*USAGE_FOR[letter][:2])
    if legend in ALT_USAGE_FOR:
        usage, shift, alt = ALT_USAGE_FOR[legend]
        return _tap(usage, shift, alt)
    return None


def _dead_key_pair(ch):
    """(dead key, letter) for an accented character, the way a macro types it: the dead key, then
    the letter, back to back. ç is the US-International special case, not a combining mark."""
    for (dead, letter), composed in DEAD_KEY_SPECIAL.items():
        if composed == ch and dead in USAGE_FOR and letter in USAGE_FOR:
            return dead, letter
    decomposed = unicodedata.normalize("NFD", ch)
    if len(decomposed) != 2:
        return None
    letter, mark = decomposed
    for dead, combining in DEAD_KEYS.items():
        if combining == mark and dead in USAGE_FOR and letter in USAGE_FOR:
            return dead, letter
    return None


def events_for(legend):
    """The keyDown events the decoder emits for `legend`, dead keys composed as the reader would.

    Snapshots go in 5 ms apart, well inside DEAD_KEY_MS, so a dead key and its letter compose into
    the one accented character the legend asks for.
    """
    snapshots = keystrokes_for(legend)
    if snapshots is None:
        return None
    decoder = SignalDecoder()
    out, now = [], 0
    for snap in snapshots:
        out += decoder.feed(snap, now)
        now += 5
    out += decoder.flush(now + 1000)
    return [m for m in out if m.get("type") == "keyDown"]


def legends_of(message):
    """Every legend in a keymap message that a keyboard could type, in a stable order."""
    seen = []
    for keys in list(message.get("layers", {}).values()) + [[c["key"] for c in message.get("combos", [])]]:
        for key in keys:
            if key.get("type") == "trans":
                continue
            tap = key.get("tap") or ""
            if tap and tap not in seen and keystrokes_for(tap) is not None:
                seen.append(tap)
    return seen


def main(argv):
    if "--corpus" in argv:
        path = argv[argv.index("--corpus") + 1]
        with open(path) as f:
            for legend in legends_of(json.load(f)):
                print(legend)
        return 0
    if "--events" in argv:
        for line in sys.stdin:
            legend = line.rstrip("\n")
            if not legend:
                continue
            events = events_for(legend)
            # Another spelling of the same named key is not a wrong chip: the strip draws one
            # glyph per key, and a keymap may spell it ↹ where the strip says ⇥.
            name = NAME_FOR_LEGEND.get(legend)
            accepts = sorted({l for l, n in NAME_FOR_LEGEND.items() if name and n == name} | {legend})
            print(json.dumps({"legend": legend, "events": events, "accepts": accepts}, ensure_ascii=False))
        return 0
    sys.stderr.write(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
