#!/usr/bin/env python3
"""A demo script, played: the JSON the GIFs are made of, turned into what a keyboard sends, when
it would send it.

    host/play.py SCRIPT --keymap KEYMAP.json [--timeline | --stills | --strokes | --check]
                 [--speed X] [--strict]

A script is what docs/make-gif.sh renders (docs/demo-scripts.md has the format): steps that are
frames -- the layers up, the keys down, the strip -- and between them text to type and pauses.
`compile` turns one into a timeline: the (wait_ms, message) pairs host/hudpoke.py sends, positions,
layers and HID reports each at the moment a keyboard would send it. Text is typed through the
keymap by host/ways.py's rules -- each character on a key or a combo of the layer the keymap has it
on, with the key that brings that layer up -- so a demo is typed the way the board types it, never
the way the page would guess it. The same file gives the GIF its frames (`--stills`).

`zmk-layer-hud demo --play FILE` plays it on the demo page, and `zmk-layer-hud poke --play FILE`
into a running feed.

Standard library only: host/ways.py and host/uskeys.py know the keymap and the reports.
"""

import argparse
import asyncio
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uskeys  # noqa: E402
import ways  # noqa: E402

# The pace words_test.js replays at, so what plays is what is tested.
CHORD_MS = 5          # between the keys of a chord
REPORT_MS = 5         # between the reports of one legend, and after a chord's last key
HOLD_MS = 90          # how long a typed key stays down
HELD_LEAD_MS = 120    # a Shift held for a capital goes down this long before the letter
LAYER_LEAD_MS = 100   # a layer comes up this long before the key typed on it
TAP_MS = 40           # how long a sticky layer's key is tapped
SETTLE_MS = 5
STEP_MS = 1000        # how long a frame stays, played
STEP_HOLD_MS = 250    # how long a frame's keys stay down, played
TAIL_MS = 2400        # after the last key: the strip fades (hud/keys.js), then the next loop
STRIP_IDLE_MS = 1800  # a pause this long empties the strip (hud/keys.js IDLE_MS)

# The characters text spells named keys with, and the words keymap-drawer files use for them. A
# drawer's "Delete" is ZMK's DEL, the key that deletes forward; "BSpace" is Backspace.
TEXT_NAMES = {" ": "space", "\n": "return", "\t": "tab"}
DRAWER_WORDS = {
    "space": "space", "spc": "space", "enter": "return", "ret": "return", "return": "return",
    "esc": "escape", "escape": "escape", "tab": "tab", "bspace": "delete", "bspc": "delete",
    "backspace": "delete", "delete": "forwarddelete", "del": "forwarddelete",
    "left": "left", "right": "right", "up": "up", "down": "down", "home": "home", "end": "end",
    "pgup": "pageup", "pageup": "pageup", "pgdn": "pagedown", "pagedown": "pagedown",
    "insert": "insert", "ins": "insert", "caps": "capslock", "capslock": "capslock",
}
GLYPH_FOR_NAME = {}   # a named key -> a legend uskeys can type it from
for _legend, _name in uskeys.NAME_FOR_LEGEND.items():
    if _name not in GLYPH_FOR_NAME and uskeys.keystrokes_for(_legend) is not None:
        GLYPH_FOR_NAME[_name] = _legend
FLAGS = ("cmd", "ctrl", "alt", "shift", "fn")

Compiled = collections.namedtuple("Compiled", "timeline stills strokes problems loop duration_ms")


class PlayError(Exception):
    """A script that cannot be played, said for the user."""


def name_of(legend):
    """The named key a legend stands for, spelled as a glyph or as keymap-drawer's word."""
    if not isinstance(legend, str):
        return None
    return uskeys.NAME_FOR_LEGEND.get(legend) or DRAWER_WORDS.get(legend.lower().replace(" ", ""))


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            script = json.load(f)
    except OSError as e:
        raise PlayError(f"cannot read {path}: {e}")
    except ValueError as e:
        raise PlayError(f"{path} is not JSON: {e}")
    if not isinstance(script, dict) or not isinstance(script.get("steps"), list):
        raise PlayError(f"{path} has no `steps`")
    return script


# ---------- spelling text ----------

class Speller:
    """Text -> the keymap's ways of typing it, longest legend first (ão before ã, qu before q).

    ways.spell cannot be the front: it gives up at the first character it has no legend for, and
    matches legends as written. Here a character no key types is reported and skipped; a space,
    a newline or ⎋ in the text types the named key, however the drawer spells it ("␣", "Space");
    and on a board that draws its letters as capitals, a lowercase letter is typed on its capital's
    key -- the legend is the keycap, not the character."""

    def __init__(self, km, cases):
        self.km = km
        self.literal, self.named = {}, {}
        for c in cases:
            n = name_of(c["legend"])
            (self.named.setdefault(n, []) if n else self.literal.setdefault(c["legend"], [])).append(c)
        letters = [k.get("tap") for k in km.data["layers"][km.base]
                   if isinstance(k.get("tap"), str) and len(k["tap"]) == 1 and k["tap"].isalpha()]
        self.capitals = bool(letters) and all(t.isupper() for t in letters)
        self.longest = sorted(self.literal, key=len, reverse=True)

    def spell(self, text):
        """-> ([(text, ways, events or None)], [(offset, char, why)])."""
        units, missing, i = [], [], 0
        while i < len(text):
            hit = next((l for l in self.longest if text.startswith(l, i)), None)
            if hit:
                units.append((hit, self.literal[hit], None))
                i += len(hit)
                continue
            ch = text[i]
            name = TEXT_NAMES.get(ch) or uskeys.NAME_FOR_LEGEND.get(ch)
            if name and name in self.named and name in GLYPH_FOR_NAME:
                units.append((ch, self.named[name], uskeys.events_for(GLYPH_FOR_NAME[name])))
            elif self.capitals and ch.islower() and ch.upper() in self.literal and uskeys.events_for(ch):
                units.append((ch, self.literal[ch.upper()], uskeys.events_for(ch)))
            else:
                why = "no key on this keymap types it" if uskeys.keystrokes_for(ch) is not None \
                    else "a US layout cannot type it"
                missing.append((i, ch, why))
            i += 1
        return units, missing


def choose(options, current, combos, base):
    """Of the ways that type a legend, the one a typist would use here: staying on the layer that
    is up, else the base, the base with Shift held, another layer typed on, and last through a
    layer's transparency. A combo only when asked for, or when nothing else types it -- as the
    page places typing sent in (hud.js handleKey)."""
    pool = list(options) if combos else [w for w in options if not w["combo"]] or list(options)

    def rank(w):
        live, held = sorted(w["live"]), bool(w["held"])
        if live and live == current and w["layer"] != base and not held:
            r = 0
        elif not live:
            r = 1 if not held else 2
        else:
            r = 3 if w["layer"] != base else 4
        return (r, (0 if w["combo"] else 1) if combos else (1 if w["combo"] else 0))
    return min(pool, key=rank)


def activator(data, km, way):
    """The key that brings a way's layer up, and how: held while it is typed on, or tapped before
    the key (a sticky layer). None when the drawing names none (the layer is only said)."""
    if not way["live"]:
        return None
    layer = ((data.get("zmk_layers") or {}).get(str(min(way["live"]))) or {}).get("drawer")
    acts = [a for a in data.get("activators") or [] if a.get("layer") == layer and a.get("idx") not in way["keys"]]
    for kind in ("hold", "sticky", "auto-sticky"):
        a = next((a for a in acts if a.get("kind") == kind), None)
        if a is not None:
            return {"pos": km.zmk(a["idx"]), "held": kind == "hold"}
    return None


def shift_key(data, km):
    """The base layer's Shift, held for a capital on a board whose letters are capitals already."""
    for idx, k in enumerate(data["layers"][km.base]):
        if isinstance(k.get("hold"), str) and k["hold"].strip().lower() in ("⇧", "shift", "lshift", "rshift"):
            return idx
    return None


# ---------- the timeline ----------

def key_event(ev, combos, down=True):
    msg = {"kind": "key", "type": "keyDown" if down else "keyUp", "name": ev.get("name") or ev.get("chars") or "",
           "chars": ev.get("chars") or "", "code": ev.get("code", 0),
           "flags": {f: bool((ev.get("flags") or {}).get(f)) for f in FLAGS}, "repeat": False, "combos": combos}
    if ev.get("composed"):
        msg["composed"] = ev["composed"]
    return msg


def chip_event(k):
    """A frame's strip chip (a string, or part of a key event) as the key event that types it."""
    if isinstance(k, str):
        events = uskeys.events_for(k)
        return events if events else [{"name": k, "chars": k}]
    return [{"name": k.get("name") or k.get("chars") or "", "chars": k.get("chars") or "", "flags": k.get("flags") or {}}]


def combo_on(data, strike, ids):
    """Whether keys struck together are a combo on these layers, as the page decides it: some
    layer up declares one on exactly those keys (hud.js pressAt). A modifier held with a key is a
    chord of another kind, and draws no pill."""
    positions = data.get("positions") or {}
    idxs = sorted(positions.get(str(p), p) if positions else p for p in strike)
    stack = []
    for zid in sorted(ids, reverse=True):
        z = (data.get("zmk_layers") or {}).get(str(zid))
        if z and z.get("drawer") and z["drawer"] not in stack:
            stack.append(z["drawer"])
    stack.append(data.get("base"))
    return len(idxs) > 1 and any(sorted(c["positions"]) == idxs and any(l in c["layers"] for l in stack)
                                 for c in data.get("combos") or [])


def text_of(evs):
    """What reports type as text: a ⌘ or ⌃ chord types nothing."""
    return "".join(e.get("chars") or "" for e in evs
                   if not ((e.get("flags") or {}).get("cmd") or (e.get("flags") or {}).get("ctrl")))


def chip_id(k):
    if isinstance(k, str):
        return (k, None, ())
    return (k.get("chars") or "", k.get("name"), tuple(sorted(f for f, v in (k.get("flags") or {}).items() if v)))


class Board:
    """The keyboard, played: what is down and which layers are up, and every message it sends."""

    def __init__(self):
        self.events = []      # (t, seq, msg)
        self.down = set()
        self.layers = []
        self.flags = {f: False for f in FLAGS}

    def send(self, t, msg):
        self.events.append((int(round(t)), len(self.events), msg))

    def press(self, t, pos):
        if pos not in self.down:
            self.down.add(pos)
            self.send(t, {"kind": "press", "pos": pos})

    def release(self, t, pos):
        if pos in self.down:
            self.down.discard(pos)
            self.send(t, {"kind": "release", "pos": pos})

    def set_layers(self, t, ids):
        ids = sorted(set(ids))
        if ids != self.layers:
            self.layers = ids
            self.send(t, {"kind": "layers", "ids": ids})

    def shift(self, t, on):
        if self.flags["shift"] != on:
            self.flags = dict(self.flags, shift=on)
            self.send(t, {"kind": "key", "type": "flagsChanged", "name": "", "chars": "", "code": 0,
                          "flags": dict(self.flags), "repeat": False, "combos": False})

    def timeline(self):
        out, last = [], 0
        for t, _, msg in sorted(self.events, key=lambda e: (e[0], e[1])):
            out.append((max(0, t - last), msg))
            last = max(last, t)
        return out, last


def compile(script, keymap, speed=1.0, combos=None):
    """The script, played: see the module's docstring. `speed` quickens the pace -- the gaps, the
    frames, the pauses -- and never the chords or the reports, nor below the combo term."""
    data = keymap
    km = ways.Keymap(data)
    hud = data.get("hud") or {}
    floor = (data.get("combo_term") or 50) + hud.get("combo_slack_ms", 20) + 20
    speed = speed if speed and speed > 0 else 1.0
    pace = lambda ms: max(floor, ms / speed)            # noqa: E731  (between keystrokes)
    span = lambda ms: max(20.0, ms / speed)             # noqa: E731  (a hold, a frame, a pause)
    use_combos = bool(script.get("combos")) if combos is None else bool(combos)
    speller = Speller(km, ways.cases(km))
    shift_idx = shift_key(data, km)
    held_timeout = hud.get("held_timeout_ms", 5000)

    board = Board()
    problems, stills, strokes = [], [], []
    t = 0.0
    if script.get("device"):
        board.send(0, {"kind": "device", "name": script["device"]})
    board.send(0, {"kind": "layers", "ids": []})
    prev_chips = []                       # the strip as the frame before left it (the strip rule)

    for n, step in enumerate(script["steps"]):
        if not isinstance(step, dict):
            raise PlayError(f"step {n + 1} is not an object")

        if "wait" in step:
            ms = span(float(step["wait"]))
            if ms >= STRIP_IDLE_MS:
                prev_chips = []
            t += ms
            continue

        if "type" in step:
            text = step["type"]
            if not isinstance(text, str):
                raise PlayError(f"step {n + 1}: `type` is not text")
            entry_combos = bool(step.get("combos", use_combos))
            wpm = step.get("wpm") or script.get("wpm")
            gap = pace(12000.0 / float(wpm) if wpm else ways.KEY_GAP_MS)   # a word is five characters
            background = sorted(int(i) for i in step.get("layers", board.layers))
            board.set_layers(t, background)
            units, missing = speller.spell(text)
            for off, ch, why in missing:
                problems.append({"level": "skip", "step": n + 1, "at": off, "char": ch, "message": why})
            holding = None                     # (pos, layer ids) of a layer key held down
            chips, frames = [], []
            for utext, options, events in units:
                way = choose(options, board.layers, entry_combos, km.base)
                target = sorted(set(background) | set(way["live"]))
                strike = [km.zmk(i) for i in way["keys"]]
                held = [km.zmk(i) for i in way["held"]]
                if speller.capitals and not held and utext.isupper() and shift_idx is not None and shift_idx not in way["keys"]:
                    held = [km.zmk(shift_idx)]   # a capital is Shift and its key, whatever the keycap says
                evs = events if events is not None else way["events"]
                b = t
                sticky = False
                if target != board.layers:
                    if holding is not None:
                        board.release(b, holding)
                        holding = None
                    act = activator(data, km, way) if way["live"] else None
                    if act is not None:
                        board.press(b, act["pos"])
                        if act["held"]:
                            holding = act["pos"]
                        else:
                            sticky = True
                            board.release(b + TAP_MS, act["pos"])
                    board.set_layers(b + SETTLE_MS, target)
                    b += max(LAYER_LEAD_MS, floor)
                if held:
                    for p in held:
                        board.press(b, p)
                    board.shift(b, True)
                    b += max(HELD_LEAD_MS, floor)
                for i, p in enumerate(strike):
                    board.press(b + i * CHORD_MS, p)
                last = b + (len(strike) - 1) * CHORD_MS
                for i, ev in enumerate(evs):
                    board.send(last + REPORT_MS * (i + 1), key_event(ev, entry_combos))
                reported = last + REPORT_MS * len(evs)
                up = max(b + span(HOLD_MS), reported + SETTLE_MS)
                strokes.append({"at_ms": int(round(b)), "check_ms": int(round(reported + 1)), "step": n + 1,
                                "text": utext, "layers": board.layers,
                                "down": sorted(board.down), "strike": strike, "combo": bool(way["combo"])})
                frames.append({"layers": list(board.layers),
                               "hold": sorted(([holding] if holding is not None else []) + held),
                               "press": strike, "keys": chips + [{"chars": e.get("chars") or "", "name": e.get("name"),
                                                                  "flags": e.get("flags") or {}} for e in evs]})
                chips = frames[-1]["keys"]
                for i, p in enumerate(strike):
                    board.release(up + i * CHORD_MS, p)
                for i, ev in enumerate(evs):
                    board.send(up + i * CHORD_MS, key_event(ev, entry_combos, down=False))
                end = up + (len(strike) - 1) * CHORD_MS
                if held:
                    for p in held:
                        board.release(end + SETTLE_MS, p)
                    board.shift(end + SETTLE_MS, False)
                    end += SETTLE_MS
                if sticky:
                    board.set_layers(end + SETTLE_MS, background)   # a one-shot layer serves one key
                t = max(b + gap, end + SETTLE_MS)
            if holding is not None:
                board.release(t, holding)
            board.set_layers(t + SETTLE_MS, background)
            t += SETTLE_MS
            how = step.get("stills", "each")
            stills += frames if how == "each" else frames[-1:] if how == "last" and frames else []
            prev_chips = [chip_id(k) for k in chips]
            continue

        # A frame: the layers up, the keys down, the strip. Played, it is one keystroke or chord.
        target = sorted(int(i) for i in step.get("layers") or [])
        press = [int(p) for p in step.get("press") or []]
        nxt = next((s for s in script["steps"][n + 1:] if isinstance(s, dict) and "type" not in s and "wait" not in s), None)
        keep = set(int(p) for p in step.get("hold") or []) or _kept(data, km, press, target, nxt)
        for p in sorted(board.down - set(press)):
            board.release(t, p)
        board.set_layers(t, target)
        carried = [p for p in press if p in board.down]
        strike = [p for p in press if p not in board.down]
        b = t + SETTLE_MS
        for i, p in enumerate(strike):
            board.press(b + i * CHORD_MS, p)
        last = b + max(0, len(strike) - 1) * CHORD_MS
        cur = [chip_id(k) for k in step.get("keys") or []]
        keys = step.get("keys") or []
        if "typed" in step:
            typed_chips = step["typed"] or []
        elif cur[:len(prev_chips)] == prev_chips and prev_chips:
            typed_chips = keys[len(prev_chips):]
        else:
            typed_chips = keys
        prev_chips = cur
        evs = [e for k in typed_chips for e in chip_event(k)]
        for i, ev in enumerate(evs):
            board.send(last + REPORT_MS * (i + 1), key_event(ev, use_combos))
        reported = last + REPORT_MS * len(evs)
        if strike or evs:
            strokes.append({"at_ms": int(round(b)), "check_ms": int(round(reported + 1)), "step": n + 1,
                            "text": text_of(evs), "layers": list(target),
                            "down": sorted(board.down), "strike": strike, "combo": combo_on(data, strike, target)})
        up = max(t + span(float(step.get("hold_ms") or script.get("hold_ms") or STEP_HOLD_MS)), reported + SETTLE_MS)
        for ev in evs:
            board.send(up, key_event(ev, use_combos, down=False))
        for i, pos in enumerate(q for q in strike + carried if q not in keep):
            board.release(up + i * CHORD_MS, pos)
        ms = span(float(step.get("ms") or script.get("step_ms") or STEP_MS))
        if keep and ms > held_timeout:
            problems.append({"level": "warn", "step": n + 1, "at": None, "char": None,
                             "message": f"a key held past hud.held_timeout_ms ({held_timeout} ms) is let go by the page"})
        t += ms
        stills.append({k: v for k, v in step.items() if k in ("layers", "press", "hold", "keys", "note", "device")})

    for p in sorted(board.down):
        board.release(t, p)
    board.shift(t, False)
    board.set_layers(t + SETTLE_MS, [])
    timeline, end = board.timeline()
    still_script = {k: script[k] for k in ("device", "opacity") if k in script}
    still_script["steps"] = stills
    return Compiled(timeline, still_script, strokes, problems, bool(script.get("loop")), int(end + TAIL_MS))


def _kept(data, km, press, target, nxt):
    """The keys of a frame that stay down into the next: a key holding a layer that is up in both,
    pressed in both -- the thumb under NAV while the chord beside it changes (docs/demo-3x5.json)."""
    if not nxt:
        return set()
    after = set(int(i) for i in nxt.get("layers") or [])
    nxt_press = set(int(p) for p in nxt.get("press") or [])
    ids_of = {}
    for zid, z in (data.get("zmk_layers") or {}).items():
        if z and z.get("drawer"):
            ids_of.setdefault(z["drawer"], set()).add(int(zid))
    keep = set()
    for a in data.get("activators") or []:
        if a.get("kind") != "hold":
            continue
        pos = km.zmk(a["idx"])
        ids = ids_of.get(a.get("layer"), set())
        if pos in press and pos in nxt_press and ids & set(target) and ids & after:
            keep.add(pos)
    return keep


def describe(problem, path):
    where = f"step {problem['step']}"
    if problem.get("at") is not None:
        where += f", character {problem['at'] + 1} {problem['char']!r}"
    return f"{path}: {where}: {problem['message']}"


async def play(timeline, send, loop=False, duration_ms=None, now=None, sleep=None):
    """Send the timeline in real time, against the clock it started on: a message late for any
    reason does not push every later one back. Returns how many messages went."""
    loop_ = asyncio.get_running_loop()
    now = now or loop_.time
    sleep = sleep or asyncio.sleep
    sent = 0
    while True:
        t0, at = now(), 0.0
        for wait_ms, msg in timeline:
            at += wait_ms / 1000.0
            delay = at - (now() - t0)
            if delay > 0:
                await sleep(delay)
            await send(msg)
            sent += 1
        if duration_ms:
            delay = duration_ms / 1000.0 - (now() - t0)
            if delay > 0:
                await sleep(delay)
        if not loop:
            return sent


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("script", help="a demo script (docs/demo-scripts.md)")
    p.add_argument("--keymap", required=True, help="the keymap message to type it on (zmk-layer-hud keymap --dump)")
    out = p.add_mutually_exclusive_group()
    out.add_argument("--timeline", action="store_true", help="print [wait_ms, message] per line (the default)")
    out.add_argument("--stills", action="store_true", help="print the script the GIF renders, a frame per keystroke")
    out.add_argument("--strokes", action="store_true", help="print every keystroke and when it goes down")
    out.add_argument("--check", action="store_true", help="say what the script plays and what it cannot type")
    p.add_argument("--speed", type=float, default=1.0, help="play this many times as fast (default 1)")
    p.add_argument("--strict", action="store_true", help="fail on a character the keymap cannot type")
    args = p.parse_args(argv)
    try:
        script = load(args.script)
        with open(args.keymap, encoding="utf-8") as f:
            keymap = json.load(f)
        c = compile(script, keymap, speed=args.speed)
    except (PlayError, OSError, ValueError) as e:
        print(f"play: {e}", file=sys.stderr)
        return 2
    for pr in c.problems:
        print(describe(pr, args.script), file=sys.stderr)
    skipped = any(pr["level"] == "skip" for pr in c.problems)
    if args.stills:
        json.dump(c.stills, sys.stdout, ensure_ascii=False, indent=1)
        sys.stdout.write("\n")
    elif args.strokes:
        for s in c.strokes:
            print(json.dumps(s, ensure_ascii=False))
    elif args.check:
        print(f"{args.script}: {len(c.strokes)} keystrokes, {len(c.timeline)} messages over "
              f"{c.duration_ms / 1000:.1f} s, {len(c.stills['steps'])} frames")
        return 1 if skipped else 0
    else:
        for wait_ms, msg in c.timeline:
            print(json.dumps([wait_ms, msg], ensure_ascii=False))
    return 1 if skipped and args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
