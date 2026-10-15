"""What each key does, for the touch keyboard: a keymap's bindings compiled into actions.

The HUD types when a drawn key is tapped, and what it types is what the key does on the real
keyboard. `sync` keeps the keymap's bindings as text (`&mt LSHIFT A`, `&hml LCTRL N`) with the
behaviours they name; this turns them into actions the page's virtual firmware (hud/engine.js)
runs without knowing anything of devicetree: `{"op": "ht", "hold": {"op": "kp", ...}, "tap": ...}`.
docs/touch.md has the schema.

ZMK's own facts come from host/zmk_tables.py: what a name such as N1 or LS(LGUI) encodes, how the
built-in behaviours are defined (&mt is a hold-preferred hold-tap of two &kp), what an unset
property defaults to. A keymap's own behaviours and its overrides of the built-ins come from the
import. Parameters go where ZMK sends them: a hold-tap's first to its hold binding and its second
to its tap binding, a behaviour of no cells takes none, a macro's &macro_param_1to1 hands the
macro's own parameter to the binding after it.

A key the engine cannot run -- a module's adaptive key, a leader key, a binding this cannot read --
types what its legend says instead (Legends, below), and a drawing with no keymap behind it types
from its legends alone. Either way the action says so (`"via": "legend"`), and `coverage` counts
them, so `sync` can say how much of a keyboard the touch keyboard really is.

Stdlib only: build_message runs wherever the HUD does.
"""

import json
import re
import sys
from collections import Counter

import keynames
import uskeys
from keymap_message import KeymapMessage
import zmk_expr
import zmk_tables as T

ACTIONS_VERSION = 1    # the "actions" field of the keymap message
KEYMAP_VERSION = 1     # zmk.keymap in the definitions, as sync.capture_keymap writes it
SCHEMA = 1             # the keymap message itself: a tablet app refuses one newer than it knows

VALUES = dict(T.KEYCODES, **T.MODIFIERS, **T.POINTING, **T.PAGES)
KEY_PAGE = T.PAGES["HID_USAGE_KEY"]
MAX_DEPTH = 8
DEFAULT_TERM_MS = 200                        # a hold-tap that sets no tapping term
LEGEND_TAP = {"ms": 10, "wait": 10}         # a legend's macro: a dead key and its letter, back to back

# What capture keeps of each behaviour, and how the compiler reads it. Anything else a node says
# (display-name, its label, a module's own properties) is not needed to run it.
PROPS = {
    "zmk,behavior-hold-tap": {
        "bindings": "phandles", "tapping-term-ms": "int", "tapping_term_ms": "int", "quick-tap-ms": "int",
        "quick_tap_ms": "int", "require-prior-idle-ms": "int", "flavor": "str", "retro-tap": "bool",
        "hold-trigger-key-positions": "ints", "hold-trigger-on-release": "bool",
        "hold-while-undecided": "bool", "hold-while-undecided-linger": "bool"},
    "zmk,behavior-sticky-key": {"bindings": "phandles", "release-after-ms": "int", "quick-release": "bool",
                                "lazy": "bool", "ignore-modifiers": "bool"},
    "zmk,behavior-mod-morph": {"bindings": "phandles", "mods": "int", "keep-mods": "int"},
    "zmk,behavior-tap-dance": {"bindings": "phandles", "tapping-term-ms": "int"},
    "zmk,behavior-macro": {"bindings": "phandles", "wait-ms": "int", "tap-ms": "int"},
    "zmk,behavior-macro-one-param": {"bindings": "phandles", "wait-ms": "int", "tap-ms": "int"},
    "zmk,behavior-macro-two-param": {"bindings": "phandles", "wait-ms": "int", "tap-ms": "int"},
    "zmk,behavior-caps-word": {"continue-list": "ints", "mods": "int"},
    "zmk,behavior-key-repeat": {"usage-pages": "ints"},
    "zmk,behavior-momentary-layer": {"locking": "bool"},
    "zmk,behavior-to-layer": {"locking": "bool"},
    "zmk,behavior-toggle-layer": {"toggle-mode": "str", "locking": "bool"},
    "zmk,behavior-key-toggle": {"toggle-mode": "str"},
    "zmk,behavior-input-two-axis": {"x-input-code": "ints", "y-input-code": "ints"},
}
SYSTEM = {"zmk,behavior-bluetooth": "bt", "zmk,behavior-outputs": "out", "zmk,behavior-rgb-underglow": "rgb",
          "zmk,behavior-backlight": "bl", "zmk,behavior-ext-power": "ext_power", "zmk,behavior-reset": "reset",
          "zmk,behavior-soft-off": "soft_off", "zmk,behavior-studio-unlock": "studio"}
RUNS = set(PROPS) | set(SYSTEM) | {"zmk,behavior-key-press", "zmk,behavior-transparent", "zmk,behavior-none",
                                   "zmk,behavior-mouse-key-press"}


def runs(compatible):
    """Whether the engine runs a behaviour of this compatible (else its keys type their legends)."""
    return compatible in RUNS or compatible.startswith("zmk,macro-")


def capture_props(node, compatible):
    """A devicetree node's properties that the compiler reads, as keymap-drawer's DTNode gives them:
    a phandle array as binding strings, an array as its cells, a string, or True. sync.py calls this
    at import; the built-ins in zmk_tables.py come in the same shapes."""
    out = {}
    for prop, kind in PROPS.get(compatible, {"bindings": "phandles"}).items():
        pattern = re.escape(prop) + "$"
        if kind == "phandles":
            value = node.get_phandle_array(pattern)
        elif kind == "str":
            value = node.get_string(pattern)
        elif kind == "bool":
            value = True if node.get_array(pattern) is not None else None
        else:
            value = node.get_array(pattern)
        if value is not None:
            out[prop] = value
    return out


def number(expr):
    return zmk_expr.evaluate(expr, VALUES, T.FUNCTIONS)


def key_of(expr):
    """A key parameter -- `A`, `LS(N1)`, `C_VOL_UP` -- as {page, id, mods}: ZMK's encoding taken
    apart. A page of 0 is the keyboard page, as keycode_state_changed.h reads it."""
    v = number(expr)
    return {"page": (v >> 16) & 0xFF or KEY_PAGE, "id": v & 0xFFFF, "mods": (v >> 24) & 0xFF}


def _signed16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


class Behaviors:
    """Every behaviour a binding may name: ZMK's built-ins, as the keymap overrides them, and the
    keymap's own (which win, as a later node with the same label would)."""

    def __init__(self, captured=None, overrides=None):
        self.table = {label: {"compatible": b["compatible"], "cells": b["cells"], "props": dict(b["props"])}
                      for label, b in T.BEHAVIORS.items()}
        for label, props in (overrides or {}).items():
            if label in self.table and isinstance(props, dict):
                self.table[label]["props"].update(props)
        for label, b in (captured or {}).items():
            if isinstance(b, dict) and isinstance(b.get("compatible"), str):
                self.table[label] = {"compatible": b["compatible"], "cells": int(b.get("cells") or 0),
                                     "props": dict(b.get("props") or {})}

    def get(self, label):
        return self.table.get(label)

    @staticmethod
    def prop(b, name, kind, default=None):
        raw = b["props"].get(name)
        if raw is None:
            return T.DEFAULTS.get(b["compatible"], {}).get(name, default)
        if kind in ("phandles", "str"):
            return raw
        if kind == "bool":
            return bool(raw)
        values = [number(c) for c in zmk_expr.cells(raw if isinstance(raw, list) else [str(raw)])]
        if kind == "ints":
            return values
        return values[0] if values else default


class Compiler:
    """Bindings -> actions. Never raises: what cannot be compiled is an `unsupported` action that
    says why, and the caller decides what the key does instead."""

    def __init__(self, behaviors):
        self.b = behaviors

    def binding(self, text, depth=0):
        if depth > MAX_DEPTH:
            return unsupported("nested too deep")
        try:
            return self._binding(text, depth)
        except zmk_expr.Unknown as e:
            return unsupported(f"unreadable: {e}")

    def _inner(self, phandle, param, depth):
        """A hold-tap's or a sticky key's own binding (`&kp`), given the parameter it passes on --
        or none, to a behaviour that takes none."""
        label = phandle.strip().lstrip("&").split()[0]
        b = self.b.get(label)
        if b is not None and b["cells"] == 0 or param is None:
            return self.binding(phandle, depth + 1)
        return self.binding(f"{phandle} {param}", depth + 1)

    def _binding(self, text, depth):
        label, params = zmk_expr.split_binding(text)
        b = self.b.get(label)
        if b is None:
            return unsupported(f"&{label}")
        c = b["compatible"]
        prop = lambda name, kind, default=None: Behaviors.prop(b, name, kind, default)  # noqa: E731

        def param(i):
            if i >= len(params):
                raise zmk_expr.Unknown(f"&{label} needs {i + 1} parameter(s)")
            return params[i]

        if c == "zmk,behavior-key-press":
            return {"op": "kp", "key": key_of(param(0))}
        if c == "zmk,behavior-key-toggle":
            return {"op": "kt", "key": key_of(param(0)), "mode": prop("toggle-mode", "str", "flip")}
        if c == "zmk,behavior-momentary-layer":
            return {"op": "mo", "layer": number(param(0)), "locking": prop("locking", "bool", False)}
        if c == "zmk,behavior-to-layer":
            return {"op": "to", "layer": number(param(0)), "locking": prop("locking", "bool", False)}
        if c == "zmk,behavior-toggle-layer":
            return {"op": "tog", "layer": number(param(0)), "mode": prop("toggle-mode", "str", "flip"),
                    "locking": prop("locking", "bool", False)}
        if c == "zmk,behavior-transparent":
            return {"op": "trans"}
        if c == "zmk,behavior-none":
            return {"op": "none"}
        if c == "zmk,behavior-hold-tap":
            phandles = prop("bindings", "phandles") or []
            if len(phandles) < 2:
                return unsupported(f"&{label}: a hold-tap needs two bindings")
            term = prop("tapping-term-ms", "int") or prop("tapping_term_ms", "int") or DEFAULT_TERM_MS
            quick = prop("quick-tap-ms", "int")
            if quick in (None, -1):
                quick = prop("quick_tap_ms", "int", -1)
            trigger = prop("hold-trigger-key-positions", "ints") or []
            return {"op": "ht",
                    "hold": self._inner(phandles[0], params[0] if params else None, depth),
                    "tap": self._inner(phandles[1], params[1] if len(params) > 1 else None, depth),
                    "flavor": prop("flavor", "str", "hold-preferred"), "term": term, "quick_tap": quick,
                    "prior_idle": prop("require-prior-idle-ms", "int", -1),
                    "retro_tap": prop("retro-tap", "bool", False),
                    "trigger_positions": trigger or None,
                    "trigger_on_release": prop("hold-trigger-on-release", "bool", False),
                    "hold_while_undecided": prop("hold-while-undecided", "bool", False),
                    "hold_while_undecided_linger": prop("hold-while-undecided-linger", "bool", False)}
        if c == "zmk,behavior-sticky-key":
            phandles = prop("bindings", "phandles") or []
            if not phandles:
                return unsupported(f"&{label}: a sticky key needs a binding")
            inner = self._inner(phandles[0], params[0] if params else None, depth)
            common = {"release_after": prop("release-after-ms", "int", 1000),
                      "quick_release": prop("quick-release", "bool", False), "lazy": prop("lazy", "bool", False)}
            if inner["op"] == "kp":
                return dict({"op": "sk", "key": inner["key"], "ignore_mods": prop("ignore-modifiers", "bool", False)},
                            **common)
            if inner["op"] == "mo":
                return dict({"op": "sl", "layer": inner["layer"]}, **common)
            return unsupported(f"&{label}: sticky {inner['op']}")
        if c == "zmk,behavior-mod-morph":
            phandles = prop("bindings", "phandles") or []
            if len(phandles) < 2:
                return unsupported(f"&{label}: a mod-morph needs two bindings")
            return {"op": "morph", "mods": prop("mods", "int", 0), "keep": prop("keep-mods", "int", 0),
                    "plain": self.binding(phandles[0], depth + 1), "morphed": self.binding(phandles[1], depth + 1)}
        if c == "zmk,behavior-tap-dance":
            return {"op": "td", "term": prop("tapping-term-ms", "int", 200),
                    "taps": [self.binding(p, depth + 1) for p in prop("bindings", "phandles") or []]}
        if c.startswith("zmk,behavior-macro"):
            return self._macro(b, params, depth)
        if c == "zmk,behavior-key-repeat":
            return {"op": "repeat", "pages": prop("usage-pages", "ints") or [KEY_PAGE]}
        if c == "zmk,behavior-caps-word":
            continues = b["props"].get("continue-list")
            return {"op": "caps_word", "mods": prop("mods", "int", T.MODIFIERS["MOD_LSFT"]),
                    "continue": [key_of(x) for x in zmk_expr.cells(continues or [])]}
        if c == "zmk,behavior-mouse-key-press":
            return {"op": "mouse", "button": number(param(0))}
        if c == "zmk,behavior-input-two-axis":
            v = number(param(0))
            axis = "scroll" if "WHEEL" in " ".join(b["props"].get("y-input-code") or []) else "move"
            return {"op": "mouse", axis: [_signed16(v >> 16), _signed16(v)]}
        if c in SYSTEM:
            kind = "bootloader" if c == "zmk,behavior-reset" and b["props"].get("bootloader") else SYSTEM[c]
            return {"op": "system", "kind": kind}
        return unsupported(c)

    def _macro(self, b, params, depth):
        """A macro's bindings as steps: each one tapped, pressed or released as the mode before it
        says, with the waits it sets; a pause waits for the key's release."""
        tap_ms = Behaviors.prop(b, "tap-ms", "int", 30)
        wait_ms = Behaviors.prop(b, "wait-ms", "int", 15)
        mode, steps, subs = "tap", [], []
        for item in Behaviors.prop(b, "bindings", "phandles") or []:
            label, ps = zmk_expr.split_binding(item)
            inner = self.b.get(label)
            comp = inner["compatible"] if inner else ""
            if comp.startswith("zmk,macro-control-mode-"):
                mode = comp.rsplit("-", 1)[1]
                continue
            if comp in ("zmk,macro-control-tap-time", "zmk,macro-control-wait-time"):
                if not ps:
                    raise zmk_expr.Unknown(f"&{label} needs a time")
                if comp.endswith("tap-time"):
                    tap_ms = number(ps[0])
                else:
                    wait_ms = number(ps[0])
                continue
            if comp == "zmk,macro-pause-for-release":
                steps.append({"pause": True})
                continue
            m = re.match(r"zmk,macro-param-(\d)to(\d)$", comp)
            if m:
                subs.append((int(m.group(1)), int(m.group(2))))
                continue
            if subs:
                ps = list(ps)
                for src, dst in subs:
                    while len(ps) < dst:
                        ps.append("0")
                    ps[dst - 1] = params[src - 1] if src - 1 < len(params) else "0"
                subs = []
            action = self.binding("&" + label + ("" if not ps else " " + " ".join(ps)), depth + 1)
            step = {mode: action, "wait": wait_ms}
            if mode == "tap":
                step["ms"] = tap_ms
            steps.append(step)
        return {"op": "macro", "steps": steps}


def unsupported(why):
    return {"op": "unsupported", "why": why}


# ---------- legends ----------

# Legends that name a modifier, in drawings and in keymap-drawer's parse of a keymap.
MOD_WORDS = {"⇧": "LSHIFT", "shift": "LSHIFT", "shft": "LSHIFT", "⌃": "LCTRL", "ctrl": "LCTRL", "ctl": "LCTRL",
             "control": "LCTRL", "⌥": "LALT", "alt": "LALT", "opt": "LALT", "option": "LALT", "⌘": "LGUI",
             "cmd": "LGUI", "command": "LGUI", "super": "LGUI", "gui": "LGUI", "win": "LGUI", "meta": "LGUI",
             "altgr": "RALT", "ralt": "RALT"}
MOD_GROUPS = {"hyper": ("LCTRL", "LSHIFT", "LALT", "LGUI"), "meh": ("LCTRL", "LSHIFT", "LALT")}
# Media and mouse words -> ZMK's names for them, so their values come from zmk_tables, not from here.
ZMK_WORDS = {
    "vol+": "C_VOL_UP", "volup": "C_VOL_UP", "vol−": "C_VOL_DN", "vol-": "C_VOL_DN", "voldn": "C_VOL_DN",
    "voldown": "C_VOL_DN", "mute": "C_MUTE", "play": "C_PP", "pause": "C_PP", "play/pause": "C_PP",
    "prev": "C_PREV", "previous": "C_PREV", "next": "C_NEXT", "stop": "C_STOP", "eject": "C_EJECT",
    "bri+": "C_BRI_UP", "bright+": "C_BRI_UP", "bri−": "C_BRI_DN", "bri-": "C_BRI_DN", "bright−": "C_BRI_DN",
    "bright-": "C_BRI_DN", "printscreen": "PSCRN", "prtsc": "PSCRN", "pscr": "PSCRN",
    "lclick": "LCLK", "rclick": "RCLK", "mclick": "MCLK",
    "mouse←": "MOVE_LEFT", "mouse→": "MOVE_RIGHT", "mouse↑": "MOVE_UP", "mouse↓": "MOVE_DOWN",
    "wheel←": "SCRL_LEFT", "wheel→": "SCRL_RIGHT", "wheel↑": "SCRL_UP", "wheel↓": "SCRL_DOWN",
}
MOD_PREFIX = re.compile(r"^([⇧⌃⌥⌘]+)\s*(.+)$")


def _word(legend):
    return re.sub(r"\s+", "", legend.lower())


def _mod_usage(name):
    return T.KEYCODES[name] & 0xFFFF


def _mod_bit(name):
    return 1 << (_mod_usage(name) - 0xE0)


def modifier_key(legend):
    """A legend that names modifiers only -- ⇧, Ctrl, Shift Super, ⇧⌘, Hyper -- as the key ZMK's
    `&kp LS(LGUI)` holds: the first modifier's usage, the rest as its modifiers. Else None."""
    if not legend:
        return None
    group = MOD_GROUPS.get(_word(legend))
    if group:
        names = list(group)
    else:
        parts = re.split(r"[\s+]+", legend.strip())
        if len(parts) == 1 and all(ch in "⇧⌃⌥⌘" for ch in legend.strip()) and len(legend.strip()) > 1:
            parts = list(legend.strip())
        names = [MOD_WORDS.get(p.lower()) for p in parts if p]
        if not names or None in names:
            return None
    mods = 0
    for n in names[1:]:
        mods |= _mod_bit(n)
    return {"page": KEY_PAGE, "id": _mod_usage(names[0]), "mods": mods}


class Legends:
    """What a key types by its legend, for a drawing with no keymap behind it and for bindings the
    engine cannot run. The rules are the drawing conventions the rest of the HUD reads already
    (activators, ways.typed, play's Speller); see docs/touch.md."""

    def __init__(self, layers, zmk_layers, base, extras, sticky_label="sticky", altgr=False,
                 toggle_label="toggle"):
        self.layers = layers
        self.base = base
        self.sticky = set((extras or {}).get("sticky") or [])
        self.typing = {base} | self.sticky
        self.sticky_label, self.toggle_label = sticky_label, toggle_label
        self.altgr = altgr
        # Each drawer layer by its lowest ZMK layer: the one a layer key of the drawing brings up.
        read = KeymapMessage({"zmk_layers": zmk_layers or {}})
        drawers = {z["drawer"] for z in (zmk_layers or {}).values() if z and z.get("drawer")}
        self.ids_of = {layer: read.ids_of(layer)[0] for layer in drawers}
        letters = [k.get("tap") for k in layers.get(base) or [] if len(k.get("tap") or "") == 1 and k["tap"].isalpha()]
        self.capitals = bool(letters) and all(t.isupper() for t in letters)

    def action(self, key, layer, typing=None):
        """The action for drawer key `key` on drawer layer `layer`, never None."""
        typing = layer in self.typing if typing is None else typing
        kind = key.get("type") or "key"
        # The key holding the layer falls through to what holds it; a "held alternate" one types its
        # own legend on the layer (the Diamond's nav thumb is ⎀ there).
        if kind == "trans" or kind == "held" or (kind.startswith("held") and not key.get("tap")):
            return {"op": "trans"}
        # A ghost key is drawn faint, not absent: the Diamond's caps-word layer draws its capitals so.
        if kind == "blank":
            return {"op": "none"}
        tap, hold, shifted = key.get("tap") or "", key.get("hold") or "", key.get("shifted") or ""
        tapped = self.tap(tap, hold, shifted, typing)
        holds = self.hold(hold) if hold not in ("", self.sticky_label, self.toggle_label) else None
        if hold in self.ids_of and tapped and tapped["op"] not in ("none", "mo", "sl", "tog") and (
                shifted == "auto-sticky" or (layer in self.sticky and layer != self.base)):
            # A key that types and leaves its hold's layer on for the next key (Romak's ç).
            steps = tapped["steps"] if tapped["op"] == "macro" else [dict({"tap": tapped}, **LEGEND_TAP)]
            sticky = {"op": "sl", "layer": self.ids_of[hold], "release_after": 1000, "quick_release": True,
                      "lazy": False}
            return self._legend({"op": "macro", "steps": steps + [dict({"tap": sticky}, **LEGEND_TAP)]})
        if holds and tapped and tapped["op"] != "none":
            return self._legend({"op": "ht", "hold": holds, "tap": tapped, "flavor": "balanced",
                                 "term": DEFAULT_TERM_MS, "quick_tap": -1, "prior_idle": -1, "retro_tap": False,
                                 "trigger_positions": None, "trigger_on_release": False,
                                 "hold_while_undecided": False})
        if holds and (tapped is None or tapped["op"] == "none"):
            return self._legend(holds)
        if tapped is None:
            return self._legend({"op": "none", "why": "label"})
        return self._legend(tapped)

    @staticmethod
    def _legend(action):
        return dict(action, via="legend")

    def tap(self, tap, hold, shifted, typing):
        if not tap:
            return None
        if tap in self.ids_of:
            layer = self.ids_of[tap]
            if shifted == "sticky" or hold == self.sticky_label:
                return {"op": "sl", "layer": layer, "release_after": 1000, "quick_release": True, "lazy": False}
            if hold == self.toggle_label:
                return {"op": "tog", "layer": layer, "mode": "flip", "locking": False}
            return {"op": "mo", "layer": layer, "locking": False}
        # The repeat mark on a key whose legend is no character (↻); a "." marked so on a vim layer
        # is Vim's repeat, and types a dot.
        if shifted == "repeat" and not self.keys(tap, typing):
            return {"op": "repeat", "pages": [KEY_PAGE]}
        mod = modifier_key(tap)
        if mod:
            if shifted == "sticky":
                return {"op": "sk", "key": mod, "release_after": 1000, "quick_release": False, "lazy": False,
                        "ignore_mods": True}
            return {"op": "kp", "key": mod}
        m = MOD_PREFIX.match(tap)
        if m and not uskeys.keystrokes_for(tap, self.altgr):
            prefix, rest = m.groups()
            keys = self.keys(rest.strip(), typing=False)
            if keys and len(keys) == 1:
                extra = 0
                for ch in prefix:
                    extra |= _mod_bit(MOD_WORDS[ch])
                return {"op": "kp", "key": dict(keys[0], mods=keys[0]["mods"] | extra)}
        if "|" in tap and all(p.strip() for p in tap.split("|")):
            return self.tap(tap.split("|")[0].strip(), "", "", typing)   # an adaptive key's usual legend
        zmk = ZMK_WORDS.get(_word(tap))
        if zmk:
            return self.zmk_word(zmk)
        keys = self.keys(tap, typing)
        return self.typed(keys) if keys else None

    def hold(self, hold):
        if hold in self.ids_of:
            return {"op": "mo", "layer": self.ids_of[hold], "locking": False}
        mod = modifier_key(hold)
        if mod:
            return {"op": "kp", "key": mod}
        keys = self.keys(hold, typing=False)
        if keys:
            return self.typed(keys)
        zmk = ZMK_WORDS.get(_word(hold))
        return self.zmk_word(zmk) if zmk else None

    @staticmethod
    def typed(keys):
        if len(keys) == 1:
            return {"op": "kp", "key": keys[0]}
        return {"op": "macro", "steps": [dict({"tap": {"op": "kp", "key": k}}, **LEGEND_TAP) for k in keys]}

    @staticmethod
    def zmk_word(name):
        if name in T.POINTING:
            v = T.POINTING[name]
            if name.endswith("CLK"):
                return {"op": "mouse", "button": v}
            axis = "scroll" if name.startswith("SCRL") else "move"
            return {"op": "mouse", axis: [_signed16(v >> 16), _signed16(v)]}
        return {"op": "kp", "key": key_of(name)}

    def keys(self, legend, typing):
        """The keys a US layout types `legend` with ({page, id, mods} each), or None: a character,
        a named key in any spelling the drawings use, or -- on a layer made for typing -- a word."""
        if self.capitals and len(legend) == 1 and legend.isalpha() and legend.isupper():
            legend = legend.lower()
        name = keynames.name_of(legend)
        if name and name in uskeys.USAGE_FOR_NAME:
            return [{"page": KEY_PAGE, "id": uskeys.USAGE_FOR_NAME[name], "mods": 0}]
        snaps = uskeys.keystrokes_for(legend, self.altgr)
        if snaps is None and typing and len(legend) > 1 and legend.isalpha():
            out = []
            for ch in legend:
                one = self.keys(ch, False)
                if not one:
                    return None
                out += one
            return out
        if snaps is None:
            return None
        return [{"page": KEY_PAGE, "id": s["keys"][0], "mods": s["mods"]} for s in snaps if s["keys"]]


# ---------- the message's "actions" ----------

class Table:
    """Each distinct action once; a layer's slots are indexes into it."""

    def __init__(self):
        self.items, self.index = [], {}

    def add(self, action):
        k = json.dumps(action, sort_keys=True, ensure_ascii=False)
        if k not in self.index:
            self.index[k] = len(self.items)
            self.items.append(action)
        return self.index[k]


def build(drawing_layers, zmk_layers, pos_to_idx, base, extras, raw=None, combos=(), combo_term=50,
          sticky_label="sticky", altgr=False):
    """The keymap message's `actions`: what every ZMK position does on every ZMK layer, compiled
    from the keymap's bindings when the import kept them (`raw`, definitions zmk.keymap), from the
    legends where it did not or where a binding cannot be run.

    drawing_layers  drawer layer -> [drawer key]          zmk_layers  id -> {drawer, ...}
    pos_to_idx      "ZMK position" -> drawer index        combos      the message's drawn combos
    """
    legends = Legends(drawing_layers, zmk_layers, base, extras, sticky_label, altgr)
    table, why = Table(), Counter()
    count = Counter()
    keymap = raw if isinstance(raw, dict) and raw.get("v") == KEYMAP_VERSION and raw.get("layers") else None
    if keymap:
        compiler = Compiler(Behaviors(keymap.get("behaviors"), keymap.get("overrides")))
        ids = sorted(int(i) for i in keymap["layers"])
        positions = int(keymap.get("positions") or max(len(b) for b in keymap["layers"].values()))
    else:
        compiler = None
        ids = sorted(int(i) for i in zmk_layers)
        positions = max([int(p) for p in pos_to_idx] + [-1]) + 1

    def legend_of(zid, pos):
        drawer = (zmk_layers.get(str(zid)) or {}).get("drawer")
        idx = pos_to_idx.get(str(pos))
        keys = drawing_layers.get(drawer) if drawer else None
        if keys is None or idx is None or idx >= len(keys):
            return None
        return legends.action(keys[idx], drawer)

    layers = {}
    for zid in ids:
        slots = []
        bindings = keymap["layers"].get(str(zid)) or [] if keymap else []
        for pos in range(positions):
            if keymap:
                text = bindings[pos] if pos < len(bindings) else "&none"
                action = compiler.binding(text)
                if action["op"] == "unsupported":
                    fallback = legend_of(zid, pos)
                    why[action["why"]] += 1
                    if fallback is not None and fallback["op"] not in ("none", "unsupported"):
                        action = dict(fallback, raw=text, why=action["why"])
                        count["legend"] += 1
                    else:
                        action = dict(action, raw=text)
                        count["unsupported"] += 1
                else:
                    action = dict(action, raw=text)
                    count["keymap"] += 1
            else:
                # A layer drawn as nothing (the Diamond's ALT OS) is see-through, as the board draws it.
                drawn = (zmk_layers.get(str(zid)) or {}).get("drawer")
                action = legend_of(zid, pos) or ({"op": "none"} if drawn else {"op": "trans"})
                count["legend"] += 1
            slots.append(table.add(action))
        layers[str(zid)] = slots

    combo_out = _combos(keymap, compiler, legends, combos, zmk_layers, pos_to_idx, combo_term, table, count, why)
    source = "legends" if not keymap else "mixed" if count["legend"] else "keymap"
    conditional = [{"if": [int(i) for i in c["if"]], "then": int(c["then"])}
                   for c in (keymap or {}).get("conditional") or [] if c.get("if") and c.get("then") is not None]
    return {"v": ACTIONS_VERSION, "source": source, "positions": positions, "default": ids[0] if ids else 0,
            "table": table.items, "layers": layers, "combos": combo_out, "conditional": conditional,
            "coverage": {"keymap": count["keymap"], "legend": count["legend"], "unsupported": count["unsupported"],
                         "why": dict(why.most_common())}}


def _combos(keymap, compiler, legends, drawn, zmk_layers, pos_to_idx, combo_term, table, count, why):
    """The combos as the engine fires them: ZMK positions, ZMK layer ids (None: every layer), the
    action, and `drawn`, the index of the drawn combo whose legend a chip shows."""
    idx_to_pos = {int(idx): int(pos) for pos, idx in pos_to_idx.items()}
    ids_of = KeymapMessage({"zmk_layers": zmk_layers or {}}).ids_of
    drawn_at = {}
    for i, c in enumerate(drawn or []):
        ps = [idx_to_pos.get(int(p)) for p in c.get("positions") or []]
        if None not in ps:
            drawn_at.setdefault(tuple(sorted(ps)), []).append(i)
    out = []
    if keymap:
        for c in keymap.get("combos") or []:
            if not c.get("binding") or not c.get("positions"):
                continue
            action = compiler.binding(c["binding"])
            positions = sorted(int(p) for p in c["positions"])
            hits = drawn_at.get(tuple(positions)) or []
            if action["op"] == "unsupported":
                why[action["why"]] += 1
                fallback = legends.action(drawn[hits[0]]["key"], None, typing=False) if hits else None
                if fallback is not None and fallback["op"] not in ("none", "unsupported"):
                    action = dict(fallback, why=action["why"])
            out.append({"positions": positions, "layers": [int(l) for l in c["layers"]] if c.get("layers") else None,
                        "action": table.add(dict(action, raw=c["binding"])),
                        "term": int(c.get("timeout-ms") or combo_term), "idle": int(c.get("require-prior-idle-ms") or 0),
                        "slow_release": bool(c.get("slow-release")), "drawn": hits[0] if hits else None})
        return out
    # A combo works through a layer drawn as nothing, as its keys do.
    undrawn = [int(zid) for zid, z in (zmk_layers or {}).items() if not (z or {}).get("drawer")]
    for i, c in enumerate(drawn or []):
        positions = [idx_to_pos.get(int(p)) for p in c.get("positions") or []]
        if None in positions:
            continue
        layer_ids = sorted({zid for name in c.get("layers") or [] for zid in ids_of(name)})
        if layer_ids:
            layer_ids = sorted(set(layer_ids) | set(undrawn))
        out.append({"positions": sorted(positions), "layers": layer_ids or None,
                    "action": table.add(legends.action(c["key"], None, typing=False)), "term": int(combo_term),
                    "idle": 0, "slow_release": False, "drawn": i})
    return out


def from_message(m):
    """The actions of a keymap message that has none -- a fixture made before them -- built from its
    legends, as build_message would for a drawing with no keymap behind it."""
    return build(m["layers"], m["zmk_layers"], m["positions"], KeymapMessage(m).base, m.get("extras") or {}, None,
                 m.get("combos") or [], m.get("combo_term", 50), "sticky", bool(m.get("altgr")))


def main(argv):
    if len(argv) == 2 and argv[0] == "--message":
        with open(argv[1], encoding="utf-8") as f:
            json.dump(from_message(json.load(f)), sys.stdout, ensure_ascii=False)
        print()
        return 0
    sys.stderr.write("usage: python3 host/actions.py --message KEYMAP.json   the actions its legends give\n")
    return 2


def summary(actions):
    """One line for `sync` and `keymap.py`: how much of the keyboard the touch keyboard runs."""
    if not actions:
        return "the touch keyboard: no actions"
    c = actions["coverage"]
    if actions["source"] == "legends":
        return f"the touch keyboard types from the legends ({c['legend']} keys: no keymap bindings were imported)"
    line = f"the touch keyboard runs {c['keymap']} bindings"
    if c["legend"]:
        line += f", {c['legend']} more from their legends"
    if c["unsupported"]:
        line += f", {c['unsupported']} do nothing"
    if c["why"]:
        top = ", ".join(f"{w} ({n})" for w, n in list(c["why"].items())[:4])
        line += f"; not run: {top}"
    return line


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
