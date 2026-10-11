"""host/actions.py: what each key does when the touch keyboard taps it.

The compiler is held to ZMK's own reading of a binding -- where a hold-tap sends its parameters,
what a built-in is when the keymap overrides it, how a macro hands its parameter on -- and the
legend fallback to the drawing conventions the rest of the HUD reads (Romak 34 is the sample with
the most of them). The values come from host/zmk_tables.py, never from numbers written here.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import actions  # noqa: E402
import keymap  # noqa: E402
import zmk_tables as T  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hud", "tests", "fixtures")


def key(name, mods=0):
    v = T.KEYCODES[name]
    return {"page": (v >> 16) & 0xFF, "id": v & 0xFFFF, "mods": ((v >> 24) & 0xFF) | mods}


def bit(name):
    return T.MODIFIERS[name]


def compile_(text, behaviors=None, overrides=None):
    return actions.Compiler(actions.Behaviors(behaviors, overrides)).binding(text)


HOLD_TAP = {"compatible": "zmk,behavior-hold-tap", "cells": 2,
            "props": {"bindings": ["&kp", "&kp"], "flavor": "balanced", "tapping-term-ms": ["220"],
                      "quick-tap-ms": ["150"], "hold-trigger-key-positions": ["0", "1"]}}


class Keys(unittest.TestCase):
    def test_a_key_and_its_implicit_modifiers(self):
        self.assertEqual({"op": "kp", "key": key("A")}, compile_("&kp A"))
        self.assertEqual({"op": "kp", "key": key("N1", bit("MOD_LSFT"))}, compile_("&kp LS(N1)"))
        self.assertEqual(compile_("&kp EXCL"), compile_("&kp LS(N1)"))

    def test_a_modifier_with_modifiers_is_all_of_them_held(self):
        self.assertEqual({"op": "kp", "key": key("LGUI", bit("MOD_LSFT"))}, compile_("&kp LS(LGUI)"))

    def test_consumer_keys_keep_their_page(self):
        self.assertEqual(T.PAGES["HID_USAGE_CONSUMER"], compile_("&kp C_VOL_UP")["key"]["page"])

    def test_key_toggle(self):
        self.assertEqual({"op": "kt", "key": key("LSHIFT"), "mode": "flip"}, compile_("&kt LSHIFT"))


class Layers(unittest.TestCase):
    def test_layer_behaviours(self):
        self.assertEqual({"op": "mo", "layer": 2, "locking": False}, compile_("&mo 2"))
        self.assertEqual({"op": "to", "layer": 0, "locking": True}, compile_("&to 0"))
        self.assertEqual({"op": "tog", "layer": 3, "mode": "flip", "locking": True}, compile_("&tog 3"))

    def test_sticky_layer_and_key_take_zmks_defaults(self):
        self.assertEqual({"op": "sl", "layer": 1, "release_after": 1000, "quick_release": True, "lazy": False},
                         compile_("&sl 1"))
        self.assertEqual({"op": "sk", "key": key("LSHIFT"), "ignore_mods": True, "release_after": 1000,
                          "quick_release": False, "lazy": False}, compile_("&sk LSHIFT"))

    def test_an_override_changes_a_built_in(self):
        got = compile_("&sk LSHIFT", overrides={"sk": {"release-after-ms": ["1500"], "quick-release": True}})
        self.assertEqual((1500, True), (got["release_after"], got["quick_release"]))


class HoldTaps(unittest.TestCase):
    def test_mod_tap_sends_its_first_parameter_to_the_hold_and_its_second_to_the_tap(self):
        got = compile_("&mt LSHIFT A")
        self.assertEqual({"op": "kp", "key": key("LSHIFT")}, got["hold"])
        self.assertEqual({"op": "kp", "key": key("A")}, got["tap"])
        self.assertEqual(("hold-preferred", 200, -1, -1), (got["flavor"], got["term"], got["quick_tap"], got["prior_idle"]))

    def test_layer_tap_holds_a_layer(self):
        got = compile_("&lt 2 SPACE")
        self.assertEqual(({"op": "mo", "layer": 2, "locking": False}, "tap-preferred"), (got["hold"], got["flavor"]))

    def test_a_keymaps_own_hold_tap(self):
        got = compile_("&hml LCTRL N", {"hml": HOLD_TAP})
        self.assertEqual(("balanced", 220, 150, [0, 1]), (got["flavor"], got["term"], got["quick_tap"],
                                                          got["trigger_positions"]))

    def test_overridden_mod_tap(self):
        got = compile_("&mt LCTL N", overrides={"mt": {"flavor": "tap-preferred", "tapping-term-ms": ["250"]}})
        self.assertEqual(("tap-preferred", 250), (got["flavor"], got["term"]))

    def test_a_hold_tap_of_a_behaviour_that_takes_no_parameter(self):
        macro = {"compatible": "zmk,behavior-macro", "cells": 0, "props": {"bindings": ["&kp X"]}}
        ht = {"compatible": "zmk,behavior-hold-tap", "cells": 2, "props": {"bindings": ["&kp", "&xm"]}}
        got = compile_("&htx LGUI 0", {"xm": macro, "htx": ht})
        self.assertEqual(("kp", "macro"), (got["hold"]["op"], got["tap"]["op"]))


class Morphs(unittest.TestCase):
    def test_grave_escape_is_a_mod_morph(self):
        got = compile_("&gresc")
        mods = bit("MOD_LGUI") | bit("MOD_LSFT") | bit("MOD_RGUI") | bit("MOD_RSFT")
        self.assertEqual(("morph", mods, 0), (got["op"], got["mods"], got["keep"]))
        self.assertEqual((key("ESC"), key("GRAVE")), (got["plain"]["key"], got["morphed"]["key"]))

    def test_tap_dance(self):
        td = {"compatible": "zmk,behavior-tap-dance", "cells": 0, "props": {"bindings": ["&kp A", "&kp B"]}}
        got = compile_("&td0", {"td0": td})
        self.assertEqual((200, 2), (got["term"], len(got["taps"])))


class Macros(unittest.TestCase):
    def test_modes_times_pauses_and_a_handed_on_parameter(self):
        m = {"compatible": "zmk,behavior-macro-one-param", "cells": 1,
             "props": {"wait-ms": ["5"], "bindings": [
                 "&macro_press", "&kp LSHIFT", "&macro_tap", "&macro_param_1to1", "&kp MACRO_PLACEHOLDER",
                 "&macro_tap_time 50", "&macro_pause_for_release", "&macro_release", "&kp LSHIFT"]}}
        got = compile_("&shifted A", {"shifted": m})
        self.assertEqual([
            {"press": {"op": "kp", "key": key("LSHIFT")}, "wait": 5},
            {"tap": {"op": "kp", "key": key("A")}, "wait": 5, "ms": 30},
            {"pause": True},
            {"release": {"op": "kp", "key": key("LSHIFT")}, "wait": 5},
        ], [s for s in got["steps"] if "pause" in s or s.get("tap") or s.get("press") or s.get("release")])


class Others(unittest.TestCase):
    def test_repeat_caps_word_and_mouse(self):
        self.assertEqual({"op": "repeat", "pages": [T.PAGES["HID_USAGE_KEY"]]}, compile_("&key_repeat"))
        cw = compile_("&caps_word")
        self.assertEqual((bit("MOD_LSFT"), [key("UNDERSCORE"), key("BACKSPACE"), key("DELETE")]),
                         (cw["mods"], cw["continue"]))
        self.assertEqual({"op": "mouse", "button": T.POINTING["LCLK"]}, compile_("&mkp LCLK"))
        self.assertEqual({"op": "mouse", "move": [0, -600]}, compile_("&mmv MOVE_UP"))
        self.assertEqual({"op": "mouse", "scroll": [0, 10]}, compile_("&msc SCRL_UP"))

    def test_what_the_screen_cannot_do_is_a_system_action(self):
        self.assertEqual({"op": "system", "kind": "bt"}, compile_("&bt BT_SEL 0"))
        self.assertEqual({"op": "system", "kind": "bootloader"}, compile_("&bootloader"))

    def test_what_it_cannot_run_says_why(self):
        self.assertEqual({"op": "unsupported", "why": "&nope"}, compile_("&nope A"))
        adaptive = {"compatible": "zmk,behavior-adaptive-key", "cells": 0, "props": {}}
        self.assertEqual("zmk,behavior-adaptive-key", compile_("&ak", {"ak": adaptive})["why"])
        self.assertTrue(compile_("&kp NOT_A_KEY")["why"].startswith("unreadable"))
        self.assertTrue(compile_("&kp")["why"].startswith("unreadable"))

    def test_a_macro_that_names_itself_stops(self):
        loop = {"compatible": "zmk,behavior-macro", "cells": 0, "props": {"bindings": ["&loop"]}}
        got = compile_("&loop", {"loop": loop})
        while got["op"] == "macro":
            got = got["steps"][0]["tap"]
        self.assertEqual("unsupported", got["op"])


class LegendKeys(unittest.TestCase):
    """The drawing conventions, one key at a time."""

    def legends(self, layers, sticky=()):
        zmk_layers = {str(i): {"drawer": name} for i, name in enumerate(layers)}
        return actions.Legends(layers, zmk_layers, next(iter(layers)), {"sticky": list(sticky)})

    def act(self, raw, layer="base", **kw):
        layers = {"base": [keymap.norm_key(raw)], "nav": [], "alpha2": [], "ext": []}
        return self.legends(layers, **kw).action(layers["base"][0] if layer == "base" else keymap.norm_key(raw), layer)

    def test_characters_and_named_keys(self):
        self.assertEqual(key("A"), self.act("a")["key"])
        self.assertEqual(key("N1", bit("MOD_LSFT")), self.act("!")["key"])
        self.assertEqual(key("BACKSPACE"), self.act("⌫")["key"])
        self.assertEqual(key("BACKSPACE"), self.act("BSpace")["key"])
        self.assertEqual(key("DELETE"), self.act("Del")["key"])
        self.assertEqual(key("F13"), self.act("F13")["key"])
        self.assertEqual("legend", self.act("a")["via"])

    def test_modifiers(self):
        self.assertEqual(key("LSHIFT"), self.act("⇧")["key"])
        self.assertEqual(key("LSHIFT", bit("MOD_LGUI")), self.act("Shift Super")["key"])
        self.assertEqual(key("LSHIFT", bit("MOD_LGUI")), self.act("⇧⌘")["key"])
        self.assertEqual(key("TAB", bit("MOD_LSFT")), self.act("⇧ ↹")["key"])
        self.assertEqual("sk", self.act({"t": "⇧", "s": "sticky"})["op"])

    def test_layers(self):
        self.assertEqual({"op": "mo", "layer": 1, "locking": False, "via": "legend"}, self.act("nav"))
        self.assertEqual("sl", self.act({"t": "nav", "s": "sticky"})["op"])
        self.assertEqual("sl", self.act({"t": "nav", "h": "sticky"})["op"])
        held = self.act({"h": "nav"})
        self.assertEqual(("mo", 1), (held["op"], held["layer"]))

    def test_a_hold_legend_makes_a_balanced_hold_tap(self):
        got = self.act({"t": "␣", "h": "nav"})
        self.assertEqual(("ht", "balanced"), (got["op"], got["flavor"]))
        self.assertEqual(({"op": "mo", "layer": 1, "locking": False}, key("SPACE")), (got["hold"], got["tap"]["key"]))
        self.assertEqual(key("LCTRL"), self.act({"t": "n", "h": "⌃"})["hold"]["key"])

    def test_a_key_that_leaves_its_layer_on(self):
        got = self.act({"t": "c", "h": "ext", "s": "auto-sticky"})
        self.assertEqual("macro", got["op"])
        self.assertEqual({"op": "sl", "layer": 3, "release_after": 1000, "quick_release": True, "lazy": False},
                         got["steps"][-1]["tap"])

    def test_words_on_a_typing_layer_are_typed(self):
        got = self.act("qu")
        self.assertEqual([key("Q"), key("U")], [s["tap"]["key"] for s in got["steps"]])
        nav = {"base": [keymap.norm_key("a")], "nav": [keymap.norm_key("qu")]}
        self.assertEqual("none", self.legends(nav).action(nav["nav"][0], "nav")["op"])

    def test_an_accent_is_its_dead_key_then_its_letter(self):
        self.assertEqual([key("SQT"), key("A")], [s["tap"]["key"] for s in self.act("á")["steps"]])

    def test_media_and_mouse_words_are_zmks(self):
        self.assertEqual(key("C_VOL_UP"), self.act("Vol +")["key"])
        self.assertEqual(key("C_PP"), self.act("Play")["key"])
        self.assertEqual({"op": "mouse", "button": T.POINTING["LCLK"], "via": "legend"}, self.act("L Click"))
        self.assertEqual([0, -10], self.act("Wheel ↓")["scroll"])

    def test_a_label_does_nothing(self):
        # Off a typing layer: on one, a word of letters is typed (qu, ão), as ways.typed reads it.
        self.assertEqual({"op": "none", "why": "label", "via": "legend"}, self.act("Swapper", layer="nav"))

    def test_a_tap_time_with_no_time_is_unreadable(self):
        m = {"compatible": "zmk,behavior-macro", "cells": 0, "props": {"bindings": ["&macro_tap_time", "&kp A"]}}
        self.assertTrue(compile_("&m", {"m": m})["why"].startswith("unreadable"))

    def test_transparent_and_blank(self):
        self.assertEqual({"op": "trans"}, self.act("▽"))
        self.assertEqual({"op": "none"}, self.act(None))
        self.assertEqual({"op": "trans"}, self.act({"type": "held"}))


class Romak(unittest.TestCase):
    """The whole of a drawing with no keymap behind it: Romak 34's message, as the page gets it."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(FIXTURES, "romak-34.json"), encoding="utf-8") as f:
            m = json.load(f)
        cls.m = m
        cls.a = actions.build(m["layers"], m["zmk_layers"], m["positions"], m["base"], m["extras"], None,
                              m["combos"], m["combo_term"], "sticky", m.get("altgr", False))

    def at(self, layer, idx):
        zid = next(i for i, z in self.m["zmk_layers"].items() if z["drawer"] == layer)
        pos = next(int(p) for p, i in self.m["positions"].items() if i == idx)
        return self.a["table"][self.a["layers"][zid][pos]]

    def test_every_key_types_from_its_legend(self):
        self.assertEqual("legends", self.a["source"])
        self.assertEqual(34, self.a["positions"])
        self.assertEqual(len(self.m["zmk_layers"]), len(self.a["layers"]))

    def test_the_thumbs(self):
        repeat_nav = self.at("alpha1", 30)
        self.assertEqual(("ht", "repeat", "mo"), (repeat_nav["op"], repeat_nav["tap"]["op"], repeat_nav["hold"]["op"]))
        shift_media = self.at("alpha1", 33)
        self.assertEqual(("sk", key("LSHIFT")), (shift_media["tap"]["op"], shift_media["tap"]["key"]))

    def test_the_c_cedilla_leaves_its_extension_on(self):
        c = self.at("alpha2", 22)
        self.assertEqual(("macro", "sl"), (c["op"], c["steps"][-1]["tap"]["op"]))

    def test_the_combos_type_their_legends(self):
        semicolon = next(c for c in self.a["combos"] if c["drawn"] == 0)
        self.assertEqual(key("SEMI"), self.a["table"][semicolon["action"]]["key"])

    def test_each_action_is_in_the_table_once(self):
        dumped = [json.dumps(t, sort_keys=True) for t in self.a["table"]]
        self.assertEqual(len(dumped), len(set(dumped)))


class Build(unittest.TestCase):
    RAW = {"v": actions.KEYMAP_VERSION, "positions": 3,
           "layers": {"0": ["&kp A", "&ak", "&mo 1"], "1": ["&trans", "&kp N1", "&ak"]},
           "behaviors": {"ak": {"compatible": "zmk,behavior-adaptive-key", "cells": 0, "props": {}}},
           "overrides": {}, "combos": [{"positions": [0, 1], "binding": "&kp ESC", "layers": None}],
           "conditional": [{"if": [1, 2], "then": 3}]}
    DRAWING = {"base": [keymap.norm_key("a"), keymap.norm_key("b"), keymap.norm_key("num")],
               "num": [keymap.norm_key("▽"), keymap.norm_key("1"), keymap.norm_key("Magic")]}
    ZMK = {"0": {"drawer": "base"}, "1": {"drawer": "num"}}
    POSITIONS = {"0": 0, "1": 1, "2": 2}

    def build(self, raw=RAW):
        return actions.build(self.DRAWING, self.ZMK, self.POSITIONS, "base", {}, raw,
                             [{"positions": [0, 1], "key": keymap.norm_key("⎋"), "layers": ["base"]}], 30)

    def slot(self, a, layer, pos):
        return a["table"][a["layers"][layer][pos]]

    def test_a_binding_it_cannot_run_types_its_legend_and_says_so(self):
        a = self.build()
        got = self.slot(a, "0", 1)
        self.assertEqual(("kp", key("B"), "legend", "&ak", "zmk,behavior-adaptive-key"),
                         (got["op"], got["key"], got["via"], got["raw"], got["why"]))
        self.assertEqual("mixed", a["source"])

    def test_and_with_no_legend_to_type_it_does_nothing(self):
        got = self.slot(self.build(), "1", 2)
        self.assertEqual(("unsupported", "&ak"), (got["op"], got["raw"]))

    def test_coverage_and_its_summary(self):
        a = self.build()
        self.assertEqual({"keymap": 4, "legend": 1, "unsupported": 1,
                          "why": {"zmk,behavior-adaptive-key": 2}}, a["coverage"])
        self.assertIn("runs 4 bindings, 1 more from their legends, 1 do nothing", actions.summary(a))

    def test_combos_conditional_layers_and_the_drawn_combo(self):
        a = self.build()
        self.assertEqual([{"positions": [0, 1], "layers": None, "action": a["combos"][0]["action"], "term": 30,
                           "idle": 0, "slow_release": False, "drawn": 0}], a["combos"])
        self.assertEqual(key("ESC"), a["table"][a["combos"][0]["action"]]["key"])
        self.assertEqual([{"if": [1, 2], "then": 3}], a["conditional"])

    def test_without_a_keymap_the_legends_do_it_all(self):
        a = self.build(None)
        self.assertEqual("legends", a["source"])
        self.assertEqual(key("N1"), self.slot(a, "1", 1)["key"])
        self.assertEqual(("none", "label"), (self.slot(a, "1", 2)["op"], self.slot(a, "1", 2)["why"]))
        self.assertEqual([0], a["combos"][0]["layers"])

    def test_a_keymap_of_another_version_is_left_to_the_legends(self):
        self.assertEqual("legends", self.build(dict(self.RAW, v=actions.KEYMAP_VERSION + 1))["source"])


class Diamond(unittest.TestCase):
    """The sample keyboard imported from its repo: a real keymap, run binding by binding."""

    def test_it_runs_from_its_bindings(self):
        with open(os.path.join(os.path.dirname(FIXTURES), "..", "..", "config", "diamond.definitions.json"),
                  encoding="utf-8") as f:
            defs = json.load(f)
        if "keymap" not in (defs.get("zmk") or {}):
            self.skipTest("the Diamond's definitions have no bindings yet (make samples)")
        cfg = keymap.merge_imported({}, defs["zmk"])
        m = keymap.build_message(cfg, defs["drawing"], source="diamond")
        a = m["actions"]
        self.assertEqual("mixed", a["source"])
        self.assertGreater(a["coverage"]["keymap"], 10 * (a["coverage"]["legend"] + a["coverage"]["unsupported"]))
        mt = a["table"][a["layers"]["0"][11]]   # &mt LCTL N, with the keymap's override of &mt
        self.assertEqual(("ht", "tap-preferred", 250, key("LCTRL"), key("N")),
                         (mt["op"], mt["flavor"], mt["term"], mt["hold"]["key"], mt["tap"]["key"]))


if __name__ == "__main__":
    unittest.main()
