"""Tests for host/sync.py — what `import` and `sync` write down, and how.

Cloning a repo is not tested here, and keymap-drawer's own parser is faked where a test needs its
output: what is tested is everything that turns what came back into the HUD's definitions -- the
layer order and names, the mapping that is a human's to make, the coverage, the physical layout a
drawing of the keymap is drawn on, the files and how they are written, and the delta a sync reports.
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sync  # noqa: E402

try:
    import keymap_drawer  # noqa: F401
    HAVE_DRAWER = True
except ImportError:
    HAVE_DRAWER = False
try:
    import yaml  # noqa: F401
    HAVE_YAML = True
except ImportError:
    HAVE_YAML = bool(shutil.which("yq"))   # keymap.load_yaml's other way to read a config


def write(directory, name, text):
    path = os.path.join(directory, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


KEYMAP = """
/ {
    keymap {
        compatible = "zmk,keymap";
        default_layer { display-name = "DEFAULT"; bindings = <&kp A>; };
        num_layer     { display-name = "NUMBERS"; bindings = <&kp N1>; };
        num_copy      { display-name = "NUMBERS"; bindings = <&kp N1>; };
        nav_layer     { display-name = "NAV";     bindings = <&kp LEFT>; };
    };
};
"""


class LayerOrder(unittest.TestCase):
    def test_layers_come_back_in_keymap_order(self):
        with tempfile.TemporaryDirectory() as d:
            path = write(d, "board.keymap", KEYMAP)
            self.assertEqual(sync.layer_names(path), ["DEFAULT", "NUMBERS", "NUMBERS", "NAV"])

    def test_a_repeated_display_name_keeps_its_own_id(self):
        # Two layers may be called the same thing; collapsing them would shift every id after.
        with tempfile.TemporaryDirectory() as d:
            names = sync.layer_names(write(d, "board.keymap", KEYMAP))
            self.assertEqual(names[1], names[2])
            self.assertEqual(len(names), 4)

    def test_layers_are_followed_through_an_include(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "defs/keymap.dtsi", KEYMAP)
            path = write(d, "board.keymap", '#include "defs/keymap.dtsi"\n')
            self.assertEqual(sync.layer_names(path)[0], "DEFAULT")

    def test_a_keymap_with_no_display_names_says_so(self):
        with tempfile.TemporaryDirectory() as d:
            path = write(d, "board.keymap", "/ { keymap { default_layer { bindings = <&kp A>; }; }; };")
            with self.assertRaises(sync.SyncError):
                sync.layer_names(path)


class FindKeymap(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        for name in ("diamond", "choc_diamond", "wired_diamond"):
            write(self.dir.name, f"boards/{name}/{name}.keymap", KEYMAP)

    def tearDown(self):
        self.dir.cleanup()

    def test_an_exact_name_wins_over_the_ones_containing_it(self):
        self.assertTrue(sync.find_keymap(self.dir.name, "diamond").endswith("/diamond.keymap"))

    def test_a_partial_name_that_matches_several_is_refused(self):
        with self.assertRaises(sync.SyncError) as e:
            sync.find_keymap(self.dir.name, "dia")
        self.assertIn("matches several", str(e.exception))

    def test_several_keyboards_and_no_name_is_refused_with_the_list(self):
        with self.assertRaises(sync.SyncError) as e:
            sync.find_keymap(self.dir.name)
        self.assertIn("--keyboard", str(e.exception))

    def test_one_keyboard_needs_no_name(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "boards/only/only.keymap", KEYMAP)
            self.assertTrue(sync.find_keymap(d).endswith("/only.keymap"))


class ComboTerm(unittest.TestCase):
    def test_a_literal_timeout_is_read(self):
        with tempfile.TemporaryDirectory() as d:
            path = write(d, "board.keymap", "combos { timeout-ms = <45>; };\n")
            self.assertEqual(sync.combo_term(path), 45)

    def test_a_timeout_defined_in_another_file_is_resolved(self):
        # The Diamond writes it through a macro: `timeout-ms = <COMBO_TERM>` in one header,
        # `#define COMBO_TERM 30` in another, both reached through the keymap's includes.
        with tempfile.TemporaryDirectory() as d:
            write(d, "defs/config.dtsi", "#define COMBO_TERM 30\n")
            write(d, "defs/helpers.h", "#define COMBO(N) N { timeout-ms = <COMBO_TERM>; };\n")
            path = write(d, "board.keymap", '#include "defs/config.dtsi"\n#include "defs/helpers.h"\n')
            self.assertEqual(sync.combo_term(path), 30)

    def test_the_commonest_wins_when_combos_disagree(self):
        # ZMK allows a timeout per combo; the HUD groups presses with one number.
        with tempfile.TemporaryDirectory() as d:
            path = write(d, "board.keymap",
                         "a { timeout-ms = <30>; }; b { timeout-ms = <30>; }; c { timeout-ms = <80>; };\n")
            self.assertEqual(sync.combo_term(path), 30)

    def test_a_keymap_that_never_says_gives_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(sync.combo_term(write(d, "board.keymap", KEYMAP)))


class LayerSymbols(unittest.TestCase):
    def test_the_defines_reached_through_the_includes(self):
        # The Diamond's layers are named where its combo term is: a config.dtsi another include away.
        with tempfile.TemporaryDirectory() as d:
            write(d, "defs/config.dtsi", "#define CCEDIL 16\n#define SFT_A2 17\n#define COMBO_TERM 30\n")
            path = write(d, "board.keymap", '#include "defs/config.dtsi"\n' + KEYMAP)
            table = sync.layer_symbols(path)
        self.assertEqual((table["CCEDIL"], table["SFT_A2"], table["COMBO_TERM"]), (16, 17, 30))


@unittest.skipUnless(HAVE_DRAWER, "reading combo nodes needs keymap-drawer's parser (the venv)")
class ComboIdle(unittest.TestCase):
    # The same property name means two things: on a combo, how long the keyboard must have been
    # idle for the combo to fire; on a hold-tap, when a tap is forced. Only the first is the HUD's.
    BOARD = """
#include <behaviors.dtsi>
#include <dt-bindings/zmk/keys.h>
#define IDLE 150
/ {
    behaviors {
        hm: home_row_mod {
            compatible = "zmk,behavior-hold-tap";
            #binding-cells = <2>;
            require-prior-idle-ms = <90>;
            bindings = <&kp>, <&kp>;
        };
    };
    combos {
        compatible = "zmk,combos";
        %s
    };
    keymap {
        compatible = "zmk,keymap";
        base { bindings = <&kp A &kp B &kp C &kp D>; };
    };
};
"""

    def idle(self, combos):
        with tempfile.TemporaryDirectory() as d:
            return sync.combo_idle(write(d, "board.keymap", self.BOARD % combos))

    def test_the_combos_idle_is_read_and_the_hold_taps_is_not(self):
        self.assertEqual(150, self.idle("esc { timeout-ms = <30>; key-positions = <0 1>; bindings = <&kp ESC>;"
                                        " require-prior-idle-ms = <IDLE>; };"))

    def test_combos_that_never_say_give_nothing(self):
        self.assertIsNone(self.idle("esc { timeout-ms = <30>; key-positions = <0 1>; bindings = <&kp ESC>; };"))

    def test_the_commonest_wins(self):
        self.assertEqual(120, self.idle(
            "a { key-positions = <0 1>; bindings = <&kp X>; require-prior-idle-ms = <120>; };"
            "b { key-positions = <1 2>; bindings = <&kp Y>; require-prior-idle-ms = <120>; };"
            "c { key-positions = <2 3>; bindings = <&kp Z>; require-prior-idle-ms = <200>; };"))

    def test_a_keymap_the_parser_cannot_read_gives_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(sync.combo_idle(write(d, "board.keymap", "/ { combos { ")))


class Mapping(unittest.TestCase):
    DRAWN = ["alpha1", "numbers", "nav"]

    def test_a_name_that_matches_a_drawing_is_taken(self):
        layers, undecided = sync.draft_layers(["NUMBERS", "NAV"], self.DRAWN, None)
        self.assertEqual(layers["0"]["drawer"], "numbers")
        self.assertEqual(layers["1"]["drawer"], "nav")
        self.assertEqual(undecided, [])

    def test_a_name_that_matches_nothing_is_left_undecided(self):
        layers, undecided = sync.draft_layers(["DEFAULT"], self.DRAWN, None)
        self.assertIsNone(layers["0"]["drawer"])
        self.assertEqual([n for _, n, _ in undecided], ["DEFAULT"])

    def test_what_was_already_decided_is_kept(self):
        # The mapping is the one part a human makes; a sync must never take it back.
        previous = {"0": {"drawer": "alpha1", "label": "Alpha 1"}}
        layers, undecided = sync.draft_layers(["DEFAULT"], self.DRAWN, previous)
        self.assertEqual(layers["0"]["drawer"], "alpha1")
        self.assertEqual(layers["0"]["label"], "Alpha 1")
        self.assertEqual(undecided, [])

    def test_a_layer_deliberately_left_blank_is_not_asked_about_again(self):
        layers, undecided = sync.draft_layers(["DEFAULT"], self.DRAWN, {"0": {"drawer": None}})
        self.assertIsNone(layers["0"]["drawer"])
        self.assertEqual(undecided, [])

    def test_a_keyboard_defines_its_layers_symbols_and_the_record_carries_them(self):
        layers, _ = sync.draft_layers(["OMARCHY", "NAV"], self.DRAWN, None, symbols=["ALPHA1", "NAV"])
        self.assertEqual(layers["0"]["symbol"], "ALPHA1")
        self.assertEqual(layers["1"]["symbol"], "NAV")

    def test_a_layer_the_keyboard_takes_out_leaves_the_next_known_undecided(self):
        # draft_layers carries exactly what do_import hands it: whether a record's row still
        # belongs to the layer at its id is keeps_decision's call, made before anything lands
        # here (the pair is covered end to end by the Import test a layer taken out). Guarded
        # away, the next layer is drafted from its name alone -- and none of them say it.
        previous = {"0": {"name": "GONE", "drawer": "numbers", "label": "Gone"}}
        layers, _ = sync.draft_layers(["NAVIGATION"], self.DRAWN, previous)
        self.assertEqual("numbers", layers["0"]["drawer"])        # carried as given ...
        layers, undecided = sync.draft_layers(["NAVIGATION"], self.DRAWN, {})
        self.assertIsNone(layers["0"]["drawer"])                  # ... or not carried at all
        self.assertEqual([n for _, n, _ in undecided], ["NAVIGATION"])


class CarriesDecisions(unittest.TestCase):
    """Whether what a record carries at an id is still the decision for the layer there."""

    def test_a_carry_it_named_by_symbol_stays_while_it_means_it(self):
        self.assertTrue(sync.keeps_decision({"symbol": "CCEDIL", "drawer": "x"}, "SHIFTED ALPHA2", "CCEDIL"))
        self.assertFalse(sync.keeps_decision({"symbol": "CCEDIL", "drawer": "x"}, "SHIFTED ALPHA2", "SFT_A2"))
        self.assertFalse(sync.keeps_decision({"symbol": "CCEDIL", "drawer": "x"}, "SHIFTED ALPHA2", None))

    def test_a_carry_that_carried_only_a_name_stays_while_it_is_still_called_so(self):
        self.assertTrue(sync.keeps_decision({"name": "NUMBERS", "drawer": "x"}, "NUMBERS", None))
        # An older record that carries no symbol answers to the name alone: a symbol the keyboard
        # defines from now on does not undo what was decided under that name.
        self.assertTrue(sync.keeps_decision({"name": "NUMBERS", "drawer": "x"}, "NUMBERS", "X"))
        self.assertFalse(sync.keeps_decision({"name": "NUMBERS", "drawer": "x"}, "NAVIGATION", None))

    def test_a_carry_that_said_neither_is_not_doubted(self):
        self.assertTrue(sync.keeps_decision({"drawer": "x", "label": "X"}, "ANYTHING", None))


class Resolve(unittest.TestCase):
    """Where layers.map entries land (resolve_map). """

    NAMES = ["OMARCHY", "MACOS", "NORMAL", "NAVIGATION", "NUMBERS", "NUMBERS"]
    SYMBOLS = {"ALPHA1": 0, "ALT_OS": 1, "NORMAL": 2, "NAV": 3, "NUM": 4, "NUM_CP": 5,
               "COMBO_TERM": 30, "LTP": 40}

    def resolve(self, **entries):
        return sync.resolve_map(entries, self.NAMES, self.SYMBOLS)

    def test_by_symbol_name_and_id(self):
        # "2" is a key whose name is an id: it comes in as a dict entry, not a keyword.
        pins, lands, strays = self.resolve(**{"ALPHA1": {"drawer": "a"}, "MACOS": {"drawer": "b"},
                                              "2": {"drawer": "c"}, "NAV": {"drawer": "n"},
                                              "MISSING": {"drawer": "m"}})
        self.assertEqual({"0": {"drawer": "a"}, "1": {"drawer": "b"}, "2": {"drawer": "c"},
                          "3": {"drawer": "n"}}, pins)
        # The layout: MISSing names nothing, and lands, in order, on the layer(s) still unmapped.
        self.assertEqual(lands, {"ALPHA1": "0", "MACOS": "1", "2": "2", "NAV": "3"})
        self.assertEqual(strays, ["MISSING"])

    def test_a_display_name_two_layers_share_names_each_of_them(self):
        pins, lands, _ = self.resolve(NUMBERS={"drawer": "numbers"})
        self.assertEqual({"4": {"drawer": "numbers"}, "5": {"drawer": "numbers"}}, pins)
        self.assertEqual(lands, {"NUMBERS": "4"})

    def test_a_define_that_is_no_layer_is_not_a_place(self):
        # The keymap defines a combo term, key positions, whatever else: only symbols at ids the
        # keymap has layers at land a map entry somewhere.
        entries = {str(i): {"drawer": f"d{i}"} for i in range(6)}
        entries.update(LTP={"drawer": "x"}, COMBO_TERM={"drawer": "x"}, ALPHA1={"drawer": "a"})
        pins, lands, strays = self.resolve(**entries)
        self.assertEqual("0", lands["ALPHA1"])                    # ALPHA1 is layer 0's symbol
        self.assertEqual("a", pins["0"]["drawer"])                # a symbol outranks an id
        self.assertEqual(strays, ["LTP", "COMBO_TERM"])

    def test_a_leftover_entry_fills_the_first_layer_still_unmapped(self):
        pins, lands, strays = sync.resolve_map(
            {"NAVS": {"drawer": "nav"}, "NUMBERS": {"drawer": "numbers"},
             "FIRST": {"drawer": "first"}}, self.NAMES, {})
        self.assertEqual(lands, {"NAVS": "0", "NUMBERS": "4", "FIRST": "1"})
        self.assertEqual(strays, [])

    def test_an_entry_without_a_drawer_is_placed_but_not_pinned(self):
        pins, lands, strays = self.resolve(ALPHA1={"label": "Home"}, NAV={"drawer": "nav"})
        self.assertEqual(lands, {"ALPHA1": "0", "NAV": "3"})
        self.assertEqual(pins, {"3": {"drawer": "nav"}})
        self.assertEqual(strays, [])

    def test_an_id_outside_the_layers_is_no_place_and_said(self):
        entries = {str(i): {"drawer": f"d{i}"} for i in range(6)}
        entries["99"] = {"drawer": "x"}
        pins, lands, strays = self.resolve(**entries)
        self.assertEqual(strays, ["99"])


class Coverage(unittest.TestCase):
    LAYERS = {"0": {"name": "DEFAULT", "drawer": "alpha1"},
              "1": {"name": "NUMBERS", "drawer": "numbers"},
              "2": {"name": "NUMBERS", "drawer": "numbers"},
              "3": {"name": "ALT OS", "drawer": None}}
    POSITIONS = {"1": 0, "2": 1, "3": 2}      # ZMK position -> drawn key
    DRAWN = ["alpha1", "numbers"]

    def cover(self, combos, drawn_combos=None):
        return sync.combo_coverage({"combos": combos}, self.LAYERS, self.POSITIONS, self.DRAWN,
                                   drawn_combos if drawn_combos is not None else {(0, 1): 1})

    def test_positions_come_back_as_drawn_keys(self):
        got = self.cover([{"p": [1, 2], "l": ["DEFAULT"]}])
        self.assertEqual(got, [{"positions": [0, 1], "layers": ["alpha1"], "binding": None}])

    def test_two_zmk_layers_drawn_as_one_collapse(self):
        # NUMBERS is two layers in the firmware and one drawing; the HUD only draws the one.
        got = self.cover([{"p": [1, 2], "l": ["NUMBERS"]}])
        self.assertEqual(got[0]["layers"], ["numbers"])

    def test_layers_are_listed_in_the_drawer_files_own_order(self):
        got = self.cover([{"p": [1, 2], "l": ["NUMBERS", "DEFAULT"]}])
        self.assertEqual(got[0]["layers"], ["alpha1", "numbers"])

    def test_a_combo_on_keys_this_board_does_not_draw_is_skipped(self):
        self.assertEqual(self.cover([{"p": [1, 9], "l": ["DEFAULT"]}]), [])

    def test_a_combo_only_on_undrawn_layers_is_skipped(self):
        self.assertEqual(self.cover([{"p": [1, 2], "l": ["ALT OS"]}]), [])

    def test_keys_the_drawer_gives_several_combos_are_left_alone(self):
        # The drawer already says which layer each one belongs to, which is more than the firmware
        # can tell us: saying anything here would flatten them together.
        self.assertEqual(self.cover([{"p": [1, 2], "l": ["DEFAULT"]}], {(0, 1): 2}), [])

    def test_a_combo_the_drawer_never_draws_is_skipped(self):
        self.assertEqual(self.cover([{"p": [1, 2], "l": ["DEFAULT"]}], {}), [])


class Delta(unittest.TestCase):
    def after(self, layers=None, combos=None):
        return {"layers": layers or {}, "combos": combos or []}

    def test_nothing_changed_is_no_lines(self):
        a = self.after({"0": {"name": "D", "drawer": "alpha1"}}, [{"positions": [0, 1], "layers": ["alpha1"]}])
        self.assertEqual(sync.delta(a, a), [])

    def test_a_combo_that_gained_and_lost_layers(self):
        before = self.after(combos=[{"positions": [0, 1], "layers": ["alpha1", "vim"]}])
        after = self.after(combos=[{"positions": [0, 1], "layers": ["alpha1", "numbers"]}])
        line = sync.delta(before, after)[0]
        self.assertIn("+numbers", line)
        self.assertIn("-vim", line)

    def test_a_layer_added_and_one_gone(self):
        before = self.after({"0": {"name": "D", "drawer": "alpha1"}})
        after = self.after({"1": {"name": "N", "drawer": "numbers"}})
        lines = sync.delta(before, after)
        self.assertTrue(any(line.startswith("  - layer 0") for line in lines))
        self.assertTrue(any(line.startswith("  + layer 1") for line in lines))

    def test_the_idle_before_a_combo_changing_is_said(self):
        before, after = self.after(), self.after()
        after["combo_idle_ms"] = 150
        self.assertEqual(["  ~ idle before a combo: 0 ms became 150 ms"], sync.delta(before, after))

    def test_a_first_import_has_nothing_to_compare_against(self):
        self.assertEqual(sync.delta(None, self.after()), [])


class Quoting(unittest.TestCase):
    def test_a_plain_word_is_left_alone(self):
        self.assertEqual(sync.yq("alpha1"), "alpha1")
        self.assertEqual(sync.yq("Alpha 1"), "Alpha 1")

    def test_anything_yaml_would_read_as_something_else_is_quoted(self):
        for value in ("off", "no", "true", "null", "ç-extension", "Media / mouse", "Ç EXTENSION"):
            self.assertTrue(sync.yq(value).startswith('"'), value)


class Names(unittest.TestCase):
    def test_every_layer_gets_a_name_of_its_own(self):
        self.assertEqual(["DEFAULT", "NUMBERS", "NUMBERS 2", "NAV", "NUMBERS 3"],
                         sync.unique_names(["DEFAULT", "NUMBERS", "NUMBERS", "NAV", "NUMBERS"]))

    def test_keymap_drawers_config_flag_goes_before_the_subcommand(self):
        with mock.patch.object(sync.shutil, "which", return_value="/venv/bin/keymap"):
            cmd = sync.keymap_parse_cmd("x.keymap", "cfg.yaml", ["A", "B 2"])
        self.assertEqual(["/venv/bin/keymap", "-c", "cfg.yaml", "parse", "-z", "x.keymap", "-l", "A", "B 2"], cmd)


@unittest.skipUnless(HAVE_DRAWER, "reading the layer nodes needs keymap-drawer")
class LayerNodes(unittest.TestCase):
    RESERVED = KEYMAP.replace('num_copy      { display-name = "NUMBERS"; bindings = <&kp N1>; };',
                              'spare { status = "reserved"; bindings = <&kp N1>; };')

    def test_names_as_keymap_drawer_gives_them_in_id_order(self):
        with tempfile.TemporaryDirectory() as d:
            got = sync.layer_list(write(d, "b.keymap", KEYMAP), d)
        self.assertEqual(["DEFAULT", "NUMBERS", "NUMBERS", "NAV"], [l["name"] for l in got])
        self.assertTrue(all(l["drawn"] for l in got))

    def test_a_reserved_layer_has_an_id_only_where_the_firmware_keeps_it(self):
        with tempfile.TemporaryDirectory() as d:
            path = write(d, "b.keymap", self.RESERVED)
            self.assertEqual(["DEFAULT", "NUMBERS", "NAV"], [l["name"] for l in sync.layer_list(path, d)])
            write(d, "config/b.conf", "CONFIG_ZMK_STUDIO=y\n")
            got = sync.layer_list(path, d)
        self.assertEqual(["DEFAULT", "NUMBERS", "spare", "NAV"], [l["name"] for l in got])
        self.assertEqual([True, True, False, True], [l["drawn"] for l in got])


class Layout(unittest.TestCase):
    """The physical layout a drawing of the keymap itself is drawn on."""

    def repo(self, d):
        write(d, "boards/shields/corne/corne.keymap", KEYMAP)
        write(d, "boards/shields/corne_ish/other.dtsi", 'x { compatible = "zmk,physical-layout"; };')
        write(d, "modules/zmk/app/boards/corne.dtsi", 'x { compatible = "zmk,physical-layout"; };')
        return os.path.join(d, "boards/shields/corne/corne.keymap")

    def test_only_the_keyboards_own_files_are_looked_in(self):
        with tempfile.TemporaryDirectory() as d:
            keymap = self.repo(d)
            own, beside = sync.boards_files(d, keymap)
        self.assertEqual([keymap], own)
        self.assertFalse(any("modules" in p or "corne_ish" in p for p in own + beside))

    def test_the_configs_layout_wins_with_its_paths_taken_from_the_config(self):
        with tempfile.TemporaryDirectory() as d:
            spec = sync.layout_for_keymap({"layout": {"dts_layout": "shape.dtsi", "layout_name": "a"}},
                                          os.path.join(d, "config.yaml"), d, self.repo(d), {})
        self.assertEqual({"dts_layout": os.path.join(d, "shape.dtsi"), "layout_name": "a"}, spec)

    def test_the_configs_layout_file_is_found_from_the_config_up_as_a_drawings_is(self):
        # A config kept in the keyboard's repo names its layout from the repo's root, as a drawer
        # YAML does there; both are found from the file that names them up through its parents.
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "config"))
            os.makedirs(os.path.join(d, "docs"))
            write(os.path.join(d, "docs"), "shape.dtsi", "/ {};")
            spec = sync.layout_for_keymap({"layout": {"dts_layout": "docs/shape.dtsi"}},
                                          os.path.join(d, "config", "hud.yaml"), d, self.repo(d), {})
            self.assertEqual(os.path.join(d, "docs", "shape.dtsi"), spec["dts_layout"])

    def test_a_layout_zmk_shares_is_named_for_keymap_drawer_to_fetch(self):
        with tempfile.TemporaryDirectory() as d:
            keymap = self.repo(d)
            write(d, "boards/shields/corne/corne.dtsi", "#include <layouts/foostan/corne.dtsi>\n")
            spec = sync.layout_for_keymap({}, os.path.join(d, "c.yaml"), d, keymap, {"layers": {"A": ["x"]}})
        self.assertEqual({"zmk_shared_layout": "foostan/corne"}, spec)

    def test_with_nothing_to_go_on_it_asks_for_layout(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(sync.SyncError, "layout:"):
                sync.layout_for_keymap({}, os.path.join(d, "c.yaml"), d, self.repo(d), {"layers": {"A": ["x"]}})


class Drawn(unittest.TestCase):
    def test_every_drawn_key_is_counted_not_the_rows(self):
        drawing = {"layer_order": ["A"], "layout": {"keys": [{}] * 36},
                   "combos": [{"positions": [1, 0]}, {"positions": [0, 1]}, {"positions": [2, 3]}]}
        names, positions, counts = sync.drawn(drawing, {})
        self.assertEqual(36, len(positions))       # examples/3x5.yaml's 9 rows once counted as 9 keys
        self.assertEqual({(0, 1): 2, (2, 3): 1}, counts)


BOARD_YAML = """layout: {ortho_layout: {rows: 1, columns: 2}}
layers:
  Base: [a, {t: b, h: Nav}]
  Nav: [x, {type: held}]
combos:
  - {p: [0, 1], k: C}
"""

# A board (RENUMBER_KEYMAP) whose map is written in its own symbols, and the same one after it
# takes NUMBERS out: every id after it moves, and a sync must not move the mapping with it.
RENUMBER_KEYMAP = """
#include "defs/config.dtsi"
/ {
    keymap {
        compatible = "zmk,keymap";
        base    { display-name = "ALPHA"; bindings = <&kp A>; };
        numbers { display-name = "NUMBERS"; bindings = <&kp N1>; };
        nav     { display-name = "NAVIGATION"; bindings = <&kp LEFT>; };
    };
};
"""
RENUMBER_KEYMAP_AFTER = """
#include "defs/config.dtsi"
/ {
    keymap {
        compatible = "zmk,keymap";
        base { display-name = "ALPHA"; bindings = <&kp A>; };
        nav  { display-name = "NAVIGATION"; bindings = <&kp LEFT>; };
    };
};
"""
RENUMBER_YAML = """layout: {ortho_layout: {rows: 2, columns: 4}}
layers:
  Base: [a, b, c, d, e, f, g, h]
  Num:  ["1", "2", "3", "4", "5", "6", "7", "8"]
  Nav:  [q, w, e, r, t, z, u, i]
"""


def git(repo, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True, env=env).stdout.strip()


@unittest.skipUnless(HAVE_YAML and shutil.which("git"), "reading a config needs PyYAML or yq, and these need git")
class WorkingCopy(unittest.TestCase):
    """A keyboard's repo read where it is: whichever branch is checked out is the keymap that is read."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.repo = os.path.join(self.d, "repo")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q", "-b", "main")
        write(self.repo, "boards/dia/dia.keymap", RENUMBER_KEYMAP)
        write(self.repo, "boards/dia/defs/config.dtsi", "#define BASE 0\n#define NUM 1\n#define NAV 2\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "main")
        git(self.repo, "switch", "-qc", "taken-out")
        write(self.repo, "boards/dia/dia.keymap", RENUMBER_KEYMAP_AFTER)
        write(self.repo, "boards/dia/defs/config.dtsi", "#define BASE 0\n#define NAV 1\n")
        git(self.repo, "commit", "-qam", "NUM taken out")
        git(self.repo, "switch", "-q", "main")
        write(self.d, "board.yaml", RENUMBER_YAML)
        self.config = write(self.d, "config.yaml", "keymap: board.yaml\ntitle: T\n")
        self.defs = sync.definitions_path(self.config)

    def layers(self):
        with open(self.defs, encoding="utf-8") as f:
            return [l["name"] for _, l in sorted(json.load(f)["zmk"]["layers"].items(), key=lambda kv: int(kv[0]))]

    def run_sync(self, source=None, say=False):
        with mock.patch.object(sync, "parse_keymap", return_value={"combos": []}), \
                contextlib.redirect_stderr(io.StringIO()) as err, contextlib.redirect_stdout(io.StringIO()) as out:
            repo, board = sync.recorded(self.config)
            sync.do_import(source or repo, board or "dia", self.config, quiet=not say, fetch=False,
                           remote=None if source else sync.recorded_remote(self.config))   # as `sync` does: the record is read, not given
        return out.getvalue() + err.getvalue()

    def test_sync_follows_the_branch_that_is_checked_out(self):
        self.run_sync(self.repo)
        self.assertEqual(["ALPHA", "NUMBERS", "NAVIGATION"], self.layers())
        git(self.repo, "switch", "-q", "taken-out")
        self.run_sync()                      # no source named: the one recorded is read again
        self.assertEqual(["ALPHA", "NAVIGATION"], self.layers())

    def recorded_repo(self):
        with open(self.defs, encoding="utf-8") as f:
            return json.load(f)["sources"]["repo"]

    def test_a_working_copy_under_home_is_recorded_as_a_home_relative_path_and_read_again_from_it(self):
        with mock.patch.dict(os.environ, {"HOME": self.d}):
            self.run_sync(self.repo)
            self.assertEqual("~/repo", self.recorded_repo())
            with open(sync.imported_path(self.config), encoding="utf-8") as f:
                self.assertIn("#   source:   ~/repo", f.read())
            git(self.repo, "switch", "-q", "taken-out")
            self.run_sync()
            self.assertEqual(["ALPHA", "NAVIGATION"], self.layers())
            keymap = os.path.join(self.repo, "boards/dia/dia.keymap")
            self.assertIn(keymap, sync.inputs(self.config, "~/repo", "dia"))     # so --watch and doctor see it

    def test_a_working_copy_outside_home_is_recorded_as_it_is(self):
        with mock.patch.dict(os.environ, {"HOME": os.path.join(self.d, "elsewhere")}):
            self.run_sync(self.repo)
        self.assertEqual(self.repo, self.recorded_repo())

    def recorded_sources(self):
        with open(self.defs, encoding="utf-8") as f:
            return json.load(f)["sources"]

    def test_the_remote_of_a_working_copy_is_recorded_beside_it_and_survives_every_sync(self):
        git(self.repo, "remote", "add", "origin", "git@github.com:you/keys.git")
        with mock.patch.dict(os.environ, {"HOME": self.d}):
            self.run_sync(self.repo)
            self.assertEqual("github.com/you/keys", self.recorded_sources()["remote"])
            self.run_sync()
            self.assertEqual("github.com/you/keys", self.recorded_sources()["remote"])
            with open(sync.imported_path(self.config), encoding="utf-8") as f:
                self.assertIn("#   remote:   github.com/you/keys", f.read())
            self.assertEqual(("~/repo", "dia"), sync.recorded(self.config))
            self.assertEqual("github.com/you/keys", sync.recorded_remote(self.config))

    def test_a_working_copy_without_a_remote_records_none(self):
        self.run_sync(self.repo)
        self.assertNotIn("remote", self.recorded_sources())
        self.assertIsNone(sync.recorded_remote(self.config))

    def test_a_remote_is_normalised_to_host_owner_repo(self):
        for url in ("git@github.com:o/r.git", "https://github.com/o/r.git", "https://github.com/o/r",
                    "ssh://git@github.com/o/r.git", "github.com/o/r"):
            self.assertEqual("github.com/o/r", sync.normalise_remote(url), url)
        for url in ("", "/srv/git/r.git", "not a url"):
            self.assertIsNone(sync.normalise_remote(url), url)

    def test_a_missing_working_copy_is_read_from_its_remote_and_the_record_stays(self):
        git(self.repo, "remote", "add", "origin", "git@github.com:you/keys.git")
        with mock.patch.dict(os.environ, {"HOME": self.d}):
            self.run_sync(self.repo)
            with open(self.defs, encoding="utf-8") as f:
                defs = json.load(f)
            defs["sources"]["repo"] = "~/moved-away"             # the working copy is not on this machine
            with open(self.defs, "w", encoding="utf-8") as f:
                json.dump(defs, f)
            seen = []

            def cloned(spec, fresh=False):                        # the clone of the remote is the repo, here
                seen.append(spec)
                return self.repo, "url"
            with mock.patch.object(sync, "resolve_source", side_effect=cloned):
                said = self.run_sync(say=True)
        self.assertEqual(["github.com/you/keys"], seen)
        self.assertIn("from github.com/you/keys (the working copy ~/moved-away is missing)", said)
        self.assertEqual("~/moved-away", self.recorded_sources()["repo"])
        self.assertEqual("github.com/you/keys", self.recorded_sources()["remote"])

    def test_a_missing_working_copy_with_no_remote_is_the_error_it_always_was(self):
        with mock.patch.dict(os.environ, {"HOME": self.d}):
            self.run_sync(self.repo)
            shutil.rmtree(self.repo)
            with self.assertRaisesRegex(sync.SyncError, "neither a directory nor a repository"):
                self.run_sync()

    def test_the_remote_is_kept_by_a_header_only_record_and_a_url_import_has_none(self):
        git(self.repo, "remote", "add", "origin", "git@github.com:you/keys.git")
        self.run_sync(self.repo)
        os.remove(self.defs)                                  # only the readable record is left
        self.assertEqual("github.com/you/keys", sync.recorded_remote(self.config))
        self.assertEqual((self.repo, "dia"), sync.recorded(self.config))
        git(self.repo, "remote", "remove", "origin")
        with mock.patch.object(sync, "resolve_source", return_value=(self.repo, "url")):
            self.run_sync("github.com/you/keys")
        self.assertNotIn("remote", self.recorded_sources())   # a URL is its own remote

    def test_watching_reads_a_missing_working_copy_through_its_remote(self):
        git(self.repo, "remote", "add", "origin", "git@github.com:you/keys.git")
        with mock.patch.dict(os.environ, {"HOME": self.d}):
            self.run_sync(self.repo)
            with open(self.defs, encoding="utf-8") as f:
                defs = json.load(f)
            defs["sources"]["repo"] = "~/moved-away"
            with open(self.defs, "w", encoding="utf-8") as f:
                json.dump(defs, f)
            self.assertNotIn(os.path.join(self.repo, "boards/dia/dia.keymap"), sync.inputs(self.config, "~/moved-away", "dia"))
            with mock.patch.object(sync, "do_import") as imported, mock.patch.object(sync, "inputs", return_value=[]):
                sync.watch(self.config, quiet=True, rounds=0)
        self.assertEqual("github.com/you/keys", imported.call_args.kwargs["remote"])
        self.assertEqual("~/moved-away", imported.call_args.args[0])

    def url_recorded(self, drawing_in_repo=True, origin="git@github.com:you/keys.git", **sources):
        """The state of an install made before the working copy was the default: the definitions
        record the repo by URL, and the config's drawing is a file in the keyboard's working copy."""
        if origin:
            git(self.repo, "remote", "add", "origin", origin)
        drawing = write(self.repo if drawing_in_repo else self.d, "docs/board.yaml", RENUMBER_YAML)
        self.config = write(self.d, "config.yaml", f"keymap: {drawing}\ntitle: T\n")
        self.defs = sync.definitions_path(self.config)
        self.run_sync(self.repo)
        with open(self.defs, encoding="utf-8") as f:
            defs = json.load(f)
        defs["sources"] = dict(defs["sources"], repo="github.com/you/keys", **sources)
        defs["sources"].pop("remote", None)
        with open(self.defs, "w", encoding="utf-8") as f:
            json.dump(defs, f)

    def sync_verb(self, *args):
        out, err = io.StringIO(), io.StringIO()
        real = sync.resolve_source

        def never_the_remote(spec, fresh=False):
            if not os.path.isdir(os.path.expanduser(spec)):       # checked first: reading it would be a clone
                raise AssertionError(f"the remote {spec} was read")
            return real(spec, fresh)
        with mock.patch.object(sync, "parse_keymap", return_value={"combos": []}), \
                mock.patch.object(sync, "resolve_source", side_effect=never_the_remote), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = sync.main(["sync", "--config", self.config, "--no-fetch", *args])
        return code, out.getvalue() + err.getvalue()

    def test_an_old_url_record_is_moved_to_the_working_copy_the_drawing_lives_in(self):
        self.url_recorded()
        with mock.patch.dict(os.environ, {"HOME": self.d}):
            code, said = self.sync_verb()
            self.assertEqual(0, code)
            self.assertIn("the repo was recorded by URL; reading its working copy ~/repo instead", said)
            self.assertEqual("~/repo", self.recorded_repo())
            self.assertEqual("github.com/you/keys", self.recorded_sources()["remote"])
            self.assertNotIn("pinned", self.recorded_sources())
            git(self.repo, "switch", "-q", "taken-out")
            code, said = self.sync_verb()
            self.assertNotIn("recorded by URL", said)           # said once, when it was done
            self.assertEqual(["ALPHA", "NAVIGATION"], self.layers())

    def reading_the_remote(self, clone=None):
        """What `sync` does with a URL record it did not move: read the remote, here a clone made earlier."""
        asked = []

        def cloned(spec, fresh=False):
            asked.append(spec)
            return clone or self.repo, "url"
        return asked, mock.patch.object(sync, "resolve_source", side_effect=cloned)

    def assert_left_on_the_remote(self, **kw):
        self.url_recorded(**kw.pop("recorded", {}))
        for change in kw.pop("then", ()):
            change()
        asked, patched = self.reading_the_remote(kw.pop("clone", None))
        with mock.patch.dict(os.environ, {"HOME": self.d}), patched, \
                mock.patch.object(sync, "parse_keymap", return_value={"combos": []}), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            sync.main(["sync", "--config", self.config, "--no-fetch"])
        self.assertEqual(["github.com/you/keys"], asked)
        self.assertNotIn("recorded by URL", out.getvalue())
        self.assertEqual("github.com/you/keys", self.recorded_repo())

    def test_a_working_copy_of_another_repo_is_not_taken_for_the_one_recorded(self):
        self.assert_left_on_the_remote(recorded={"origin": "git@github.com:someone/else.git"})

    def test_a_drawing_outside_any_working_copy_moves_nothing(self):
        self.assert_left_on_the_remote(recorded={"drawing_in_repo": False})

    def test_a_working_copy_without_the_keyboards_keymap_moves_nothing(self):
        clone = os.path.join(self.d, "clone")
        shutil.copytree(self.repo, clone, ignore=shutil.ignore_patterns(".git"))
        self.assert_left_on_the_remote(then=[lambda: os.remove(os.path.join(self.repo, "boards/dia/dia.keymap"))],
                                       clone=clone)

    def test_a_record_pinned_by_an_import_of_the_url_is_not_moved_and_stays_pinned(self):
        self.assert_left_on_the_remote(recorded={"pinned": True})
        self.assertTrue(self.recorded_sources()["pinned"])         # kept through a plain sync

    def test_importing_a_url_pins_it_and_importing_a_working_copy_does_not(self):
        self.url_recorded()
        asked, patched = self.reading_the_remote()
        with patched, mock.patch.object(sync, "parse_keymap", return_value={"combos": []}), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            sync.main(["import", "github.com/you/keys", "--config", self.config, "--no-fetch"])
        self.assertEqual(["github.com/you/keys"], asked)
        self.assertTrue(self.recorded_sources()["pinned"])
        self.run_sync(self.repo)
        self.assertNotIn("pinned", self.recorded_sources())

    def test_a_pin_is_kept_by_a_drawing_only_sync_and_by_pristine_only_when_the_repo_is(self):
        self.url_recorded(pinned=True)
        quietly = dict(quiet=True, fetch=False)
        sync.do_import(None, None, self.config, **quietly)
        self.assertTrue(self.recorded_sources()["pinned"])
        sync.do_import(None, None, self.config, pristine=True, keep=True, **quietly)
        self.assertTrue(self.recorded_sources()["pinned"])
        sync.do_import(None, None, self.config, pristine=True, keep=False, **quietly)
        self.assertNotIn("pinned", self.recorded_sources())

    def test_watching_moves_the_record_once_and_a_pinned_url_cannot_be_watched(self):
        self.url_recorded()
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"HOME": self.d}), mock.patch.object(sync, "do_import") as imported, \
                mock.patch.object(sync, "inputs", return_value=[]), contextlib.redirect_stderr(out), \
                contextlib.redirect_stdout(out):
            sync.watch(self.config, quiet=False, interval=0, sleep=lambda s: None, rounds=2)
        self.assertEqual(1, out.getvalue().count("recorded by URL"))
        self.assertEqual("~/repo", imported.call_args.args[0])
        with mock.patch.object(sync, "recorded_pinned", return_value=True):
            with self.assertRaisesRegex(sync.SyncError, "cannot be watched"):
                sync.watch(self.config, quiet=True, rounds=0)

    def test_a_home_reached_through_a_link_still_makes_the_record_home_relative(self):
        # macOS: git says /private/var/…, the temporary home is spelled /var/…
        real = os.path.join(self.d, "real-home")
        os.makedirs(os.path.join(real, "repo"))
        link = os.path.join(self.d, "linked-home")
        os.symlink(real, link)
        self.assertEqual("~/repo", sync.portable_path(os.path.join(real, "repo"), link))
        self.assertEqual("~/repo", sync.portable_path(os.path.join(link, "repo"), link))

    def test_what_was_read_is_said_with_its_branch(self):
        said = self.run_sync(self.repo, say=True)
        self.assertIn(f"from {self.repo} (branch main)", said)
        git(self.repo, "switch", "-q", "taken-out")
        self.assertIn(f"from {self.repo} (branch taken-out)", self.run_sync(say=True))
        git(self.repo, "switch", "-q", "--detach")
        said = self.run_sync(say=True)
        self.assertIn(f"from {self.repo}\n", said)
        self.assertNotIn("branch", said.split("reading", 1)[1].splitlines()[0])


@unittest.skipUnless(HAVE_YAML, "reading a config needs PyYAML or yq")
class ShippedDiamond(unittest.TestCase):
    """A fresh install links the shipped Diamond files, so what they record is what `sync` reads first."""

    def test_the_shipped_diamond_syncs_from_the_local_checkout_and_falls_back_to_the_remote(self):
        config = os.path.join(sync.ROOT, "config", "diamond.yaml")
        self.assertEqual(("~/projects/keyboards", "diamond"), sync.recorded(config))
        self.assertEqual("github.com/rafaelromao/keyboards", sync.recorded_remote(config))
        with open(sync.imported_path(config), encoding="utf-8") as f:
            head = f.read()
        self.assertIn("#   source:   ~/projects/keyboards", head)
        self.assertIn("#   remote:   github.com/rafaelromao/keyboards", head)


@unittest.skipUnless(HAVE_YAML, "reading a config needs PyYAML or yq")
class ResolveSource(unittest.TestCase):
    def test_a_word_is_neither_a_directory_nor_a_repository(self):
        with mock.patch.object(sync, "run") as run:
            for spec in ("help", "../nowhere/keymap.yaml", "github.com"):
                with self.assertRaisesRegex(sync.SyncError, "neither a directory nor a repository"):
                    sync.resolve_source(spec)
        run.assert_not_called()

    def test_a_pasted_github_path_is_cloned_over_https(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(sync, "CACHE", d), \
                mock.patch.object(sync, "run") as run:
            sync.resolve_source("github.com/you/zmk-config")
        self.assertEqual("https://github.com/you/zmk-config", run.call_args[0][0][-2])
        self.assertEqual("--", run.call_args[0][0][-3])      # a URL from a file is never an option to git


class Import(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        write(self.d, "board.yaml", BOARD_YAML)
        self.config = write(self.d, "config.yaml", "keymap: board.yaml\ntitle: T\n")
        self.defs = sync.definitions_path(self.config)

    def run_import(self, source=None, keyboard=None):
        return sync.do_import(source, keyboard, self.config, quiet=True, fetch=False)

    def test_a_config_named_through_a_variable_is_the_one_meant(self):
        # As the HUD reads it (keymap.find_config): ~ and $VARS, then absolute. A $VAR left as it
        # was named a directory called "$ZMKHUD_T" and wrote a new config there.
        with mock.patch.dict(os.environ, {"ZMKHUD_T": self.d}):
            sync.do_import(None, None, "$ZMKHUD_T/config.yaml", quiet=True, fetch=False)
        self.assertTrue(os.path.isfile(self.defs))
        self.assertFalse(os.path.exists(os.path.join(os.getcwd(), "$ZMKHUD_T")))
        self.assertEqual(self.config, sync.keymap_mod.config_path(os.path.join(self.d, ".", "config.yaml")))

    def test_a_drawing_alone_from_the_configs_keymap_drawer_file(self):
        self.run_import()
        with open(self.defs, encoding="utf-8") as f:
            defs = json.load(f)
        self.assertEqual(sync.keymap_mod.DEFINITIONS_VERSION, defs["version"])
        self.assertEqual("board.yaml", defs["source"])
        self.assertEqual({"drawing": "board.yaml"}, defs["sources"])
        self.assertEqual(["Base", "Nav"], defs["drawing"]["layer_order"])
        self.assertEqual(2, len(defs["keymap_drawer"]["layout"]))
        self.assertNotIn("zmk", defs)
        self.assertFalse(os.path.exists(sync.imported_path(self.config)))   # nothing from a repo to record

    def test_an_unchanged_sync_writes_nothing(self):
        self.run_import()
        before = os.stat(self.defs)
        self.run_import()
        after = os.stat(self.defs)
        self.assertEqual((before.st_ino, before.st_mtime_ns), (after.st_ino, after.st_mtime_ns))

    def test_definitions_that_would_not_load_are_not_written(self):
        write(self.d, "config.yaml", "keymap: board.yaml\nlayers: {map: {Nav: Nope}}\n")
        with self.assertRaisesRegex(sync.SyncError, "nothing was written"):
            self.run_import()
        self.assertFalse(os.path.exists(self.defs))

    def test_a_write_goes_through_a_symlink_to_the_file_it_points_at(self):
        kept = os.path.join(self.d, "kept-in-a-repo.definitions.json")
        with open(kept, "w", encoding="utf-8") as f:
            f.write("{}")
        os.symlink(kept, self.defs)
        self.run_import()
        self.assertTrue(os.path.islink(self.defs))
        with open(kept, encoding="utf-8") as f:
            self.assertIn('"layer_order"', f.read())

    def test_a_drawing_alone_keeps_what_a_repo_import_said(self):
        self.run_import()
        with open(self.defs, encoding="utf-8") as f:
            defs = json.load(f)
        defs["zmk"] = {"layers": {"0": {"name": "BASE", "drawer": "Base", "label": "Base"}}, "combo_term_ms": 40}
        defs["sources"].update(repo="github.com/you/zmk-config", keyboard="b")
        with open(self.defs, "w", encoding="utf-8") as f:
            json.dump(defs, f)
        write(self.d, "board.yaml", BOARD_YAML.replace("[a,", "[z,"))
        self.run_import()
        with open(self.defs, encoding="utf-8") as f:
            again = json.load(f)
        self.assertEqual(40, again["zmk"]["combo_term_ms"])
        self.assertEqual("github.com/you/zmk-config", again["sources"]["repo"])
        self.assertEqual("z", again["drawing"]["layers"]["Base"][0]["tap"])

    def test_the_configs_own_keymap_drawer_file_is_taken_as_the_drawing(self):
        self.run_import(os.path.join(self.d, "board.yaml"))
        with open(self.defs, encoding="utf-8") as f:
            self.assertEqual({"drawing": "board.yaml"}, json.load(f)["sources"])

    def test_another_keymap_drawer_file_is_not_cloned_but_explained(self):
        other = write(self.d, "other.yaml", BOARD_YAML)
        with mock.patch.object(sync, "run") as run:
            with self.assertRaisesRegex(sync.SyncError, "name it as `keymap: "):
                self.run_import(other)
        run.assert_not_called()
        self.assertFalse(os.path.exists(self.defs))

    def as_if_a_repo_was_imported(self):
        self.run_import()
        with open(self.defs, encoding="utf-8") as f:
            defs = json.load(f)
        defs["zmk"] = {"layers": {"0": {"name": "BASE", "drawer": "Base", "label": "Base"}}, "combo_term_ms": 40}
        defs["sources"].update(repo="github.com/you/zmk-config", keyboard="b")
        with open(self.defs, "w", encoding="utf-8") as f:
            json.dump(defs, f)
        return os.stat(self.defs).st_mtime_ns

    def pristine(self, **kw):
        sync.do_import(None, None, self.config, quiet=True, fetch=False, pristine=True, **kw)
        with open(self.defs, encoding="utf-8") as f:
            return json.load(f)

    def test_pristine_with_no_one_to_ask_lists_what_it_would_drop_and_writes_nothing(self):
        before = self.as_if_a_repo_was_imported()
        with self.assertRaisesRegex(sync.SyncError, r"github\.com/you/zmk-config(.|\n)*--keep-custom"):
            self.pristine()
        self.assertEqual(before, os.stat(self.defs).st_mtime_ns)

    def test_pristine_drop_forgets_what_an_earlier_repo_import_said(self):
        self.as_if_a_repo_was_imported()
        again = self.pristine(keep=False)
        self.assertEqual({"drawing": "board.yaml"}, again["sources"])
        self.assertNotIn("zmk", again)

    def test_pristine_keep_keeps_just_that_and_redraws_the_rest(self):
        self.as_if_a_repo_was_imported()
        write(self.d, "board.yaml", BOARD_YAML.replace("[a,", "[z,"))
        again = self.pristine(keep=True)
        self.assertEqual(40, again["zmk"]["combo_term_ms"])
        self.assertEqual("github.com/you/zmk-config", again["sources"]["repo"])
        self.assertEqual("z", again["drawing"]["layers"]["Base"][0]["tap"])

    def test_pristine_asks_when_it_can(self):
        self.as_if_a_repo_was_imported()
        asked = []
        with contextlib.redirect_stderr(io.StringIO()) as err:
            again = self.pristine(ask=lambda q: asked.append(q) or q.startswith("Keep 2"))
        self.assertEqual(["Keep 1, layer numbers and names? [Y/n] ", "Keep 2, combo timeout (40 ms)? [Y/n] ",
                          "Keep 3, the repo sync reads? [Y/n] "], asked)          # each on its own
        listed = err.getvalue()
        self.assertIn("These came from your last import of github.com/you/zmk-config", listed)
        self.assertIn("  2. Combo timeout: 40 ms\n     Without it: the default, 50 ms.", listed)
        self.assertEqual({"combo_term_ms": 40}, again["zmk"])                    # 2 kept, 1 dropped
        self.assertNotIn("repo", again["sources"])                               # 3 dropped

    def test_pristine_keeps_the_items_it_is_told(self):
        self.as_if_a_repo_was_imported()
        again = self.pristine(keep={1, 3})
        self.assertEqual(["layers"], list(again["zmk"]))                         # not the combo timeout
        self.assertEqual("github.com/you/zmk-config", again["sources"]["repo"])

    def test_pristine_refuses_an_item_it_did_not_list(self):
        before = self.as_if_a_repo_was_imported()
        with self.assertRaisesRegex(sync.SyncError, "names 4, and the list goes up to 3"):
            self.pristine(keep={4})
        self.assertEqual(before, os.stat(self.defs).st_mtime_ns)

    def test_pristine_lists_only_what_would_change_something(self):
        self.as_if_a_repo_was_imported()
        write(self.d, "config.yaml", "keymap: board.yaml\ntitle: T\ncombo_term_ms: 30\n")   # the config's own wins
        asked = []
        with contextlib.redirect_stderr(io.StringIO()):
            self.pristine(ask=lambda q: asked.append(q) or True)
        self.assertFalse(any("combo timeout" in q for q in asked), asked)

    def with_labels(self):
        self.as_if_a_repo_was_imported()
        write(self.d, "config.yaml", "keymap: board.yaml\ntitle: T\nlayers:\n  map:\n"
                                     "    BASE: { drawer: Base, label: My base }   # mine\n"
                                     "    NAV:  { drawer: Nav, label: Arrows, class: vim }\n")

    def test_pristine_asks_first_about_the_configs_own_labels(self):
        self.with_labels()
        asked = []
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.pristine(ask=lambda q: asked.append(q) or True)
        self.assertEqual("Keep 1, the layer labels in config.yaml? [Y/n] ", asked[0])
        self.assertIn('Layer labels your config.yaml sets (2 layers, e.g. NAV = "Arrows")', err.getvalue())
        with open(self.config, encoding="utf-8") as f:
            self.assertIn("label: Arrows", f.read())                     # kept: not touched

    def test_pristine_drops_them_from_config_yaml_and_keeps_the_rest_of_it(self):
        self.with_labels()
        self.pristine(keep={2, 3, 4})                                       # everything but the labels
        with open(self.config, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("    BASE: { drawer: Base }   # mine\n", text)
        self.assertIn("    NAV:  { drawer: Nav, class: vim }", text)
        backups = [n for n in os.listdir(self.d) if n.startswith("config.yaml.bak-")]
        self.assertEqual(1, len(backups))
        with open(os.path.join(self.d, backups[0]), encoding="utf-8") as f:
            self.assertIn("label: Arrows", f.read())

    def test_pristine_with_nothing_carried_asks_nothing(self):
        self.run_import()
        self.pristine(ask=lambda q: self.fail("asked"))

    def test_pristine_clones_a_repo_again(self):
        with tempfile.TemporaryDirectory() as cache, mock.patch.object(sync, "CACHE", cache), \
                mock.patch.object(sync, "run") as run:
            os.makedirs(os.path.join(cache, "zmk-config", ".git"))
            sync.resolve_source("github.com/you/zmk-config", fresh=True)
            self.assertEqual("clone", run.call_args[0][0][1])
            self.assertFalse(os.path.exists(os.path.join(cache, "zmk-config", ".git")))

    def test_a_config_that_is_not_there_is_started_from_the_example(self):
        config = os.path.join(self.d, "new", "config.yaml")
        with self.assertRaisesRegex(sync.SyncError, "needs a ZMK repo"):
            sync.do_import(None, None, config, quiet=True, fetch=False)   # the example names no keymap
        self.assertTrue(os.path.isfile(config))

    def test_the_keymap_itself_drawn_layer_by_layer(self):
        write(self.d, "config.yaml", "layout: {ortho_layout: {rows: 1, columns: 2}}\n")
        repo = os.path.join(self.d, "repo")
        write(repo, "config/b.keymap", KEYMAP)

        def parsed(path, drawer_config=None, names=None):
            # keymap-drawer's parse, as the names it was given make it: a layer each, in order.
            return {"layers": {n: [n.lower(), {"t": "x", "h": "NAV"} if i == 0 else "y"]
                               for i, n in enumerate(names or [])}, "combos": [{"p": [0, 1], "k": "C", "l": ["NAV"]}]}
        with mock.patch.object(sync, "parse_keymap", side_effect=parsed):
            self.run_import(repo, "b")
        with open(self.defs, encoding="utf-8") as f:
            defs = json.load(f)
        self.assertEqual(["DEFAULT", "NUMBERS", "NUMBERS 2", "NAV"], defs["drawing"]["layer_order"])
        self.assertEqual(["DEFAULT", "NUMBERS", "NUMBERS 2", "NAV"],
                         [defs["zmk"]["layers"][str(i)]["drawer"] for i in range(4)])
        self.assertEqual(["NAV"], defs["drawing"]["combos"][0]["layers"])
        self.assertEqual(sync.os.path.abspath(repo), defs["sources"]["repo"])
        self.assertTrue(os.path.isfile(sync.imported_path(self.config)))

    def test_a_layer_taken_out_moves_no_mapping_across_and_says_what_stopped_applying(self):
        # The Diamond's bug: the map is written in the keyboard's own symbols, a layer in the
        # middle is taken out, every id after it moves one down -- resolving by position put an
        # entry on the layer above its one. The record carries the symbols beside each layer, so
        # the carries are told against them and the map lands on the layers it still names; the
        # entry for a layer that is gone is said, never quietly dropped onto a neighbour.
        repo = os.path.join(self.d, "repo")
        write(repo, "boards/dia/defs/config.dtsi", "#define BASE 0\n#define NUM 1\n#define NAV 2\n")
        write(repo, "boards/dia/dia.keymap", RENUMBER_KEYMAP)
        write(self.d, "board.yaml", RENUMBER_YAML)
        write(self.d, "config.yaml", "keymap: board.yaml\ntitle: T\n"
                                     "layers:\n  map:\n"
                                     "    BASE: { drawer: Base }\n"
                                     "    NUM:  { drawer: Num }\n"
                                     "    NAV:  { drawer: Nav }\n")
        with mock.patch.object(sync, "parse_keymap", return_value={"combos": []}):
            self.run_import(repo, "dia")
            with open(self.defs, encoding="utf-8") as f:
                layers = json.load(f)["zmk"]["layers"]
            self.assertEqual(["ALPHA", "NUMBERS", "NAVIGATION"], [layers[str(i)]["name"] for i in range(3)])
            self.assertEqual(["Base", "Num", "Nav"], [layers[str(i)]["drawer"] for i in range(3)])
            self.assertEqual(["BASE", "NUM", "NAV"], [layers[str(i)].get("symbol") for i in range(3)])

        write(repo, "boards/dia/defs/config.dtsi", "#define BASE 0\n#define NAV 1\n")
        write(repo, "boards/dia/dia.keymap", RENUMBER_KEYMAP_AFTER)
        with mock.patch.object(sync, "parse_keymap", return_value={"combos": []}), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            sync.do_import(repo, "dia", self.config, quiet=False, fetch=False)
        said = err.getvalue()
        self.assertIn("no longer name a layer", said)
        self.assertIn("not applied: NUM (the keyboard's layers have changed?)", said)
        with open(self.defs, encoding="utf-8") as f:
            layers = json.load(f)["zmk"]["layers"]
        self.assertEqual(["ALPHA", "NAVIGATION"], [layers[str(i)]["name"] for i in range(2)])
        self.assertEqual(["Base", "Nav"], [layers[str(i)]["drawer"] for i in range(2)])
        self.assertEqual(["BASE", "NAV"], [layers[str(i)].get("symbol") for i in range(2)])
        with open(sync.imported_path(self.config), encoding="utf-8") as f:
            self.assertEqual(2, f.read().count("symbol: "))

    def test_a_plain_sync_leaves_the_labels_a_human_wrote_alone(self):
        # The labels in the map are part of what the human wrote: a sync carries them into the
        # record and never takes them out of config.yaml (only --pristine, asked, does).
        repo = os.path.join(self.d, "repo")
        write(repo, "boards/dia/defs/config.dtsi", "#define BASE 0\n#define NAV 1\n")
        write(repo, "boards/dia/dia.keymap", RENUMBER_KEYMAP_AFTER)
        write(self.d, "board.yaml", RENUMBER_YAML)
        write(self.d, "config.yaml", "keymap: board.yaml\ntitle: T\n"
                                     "layers:\n  map:\n"
                                     "    BASE: { drawer: Base, label: Base camp }\n"
                                     "    NAV:  { drawer: Nav }\n")
        with mock.patch.object(sync, "parse_keymap", return_value={"combos": []}):
            self.run_import(repo, "dia")
            self.run_import(repo, "dia")                       # and a sync over a sync: no drift
        with open(self.config, encoding="utf-8") as f:
            self.assertIn("label: Base camp", f.read())
        with open(sync.imported_path(self.config), encoding="utf-8") as f:
            rec = f.read()
        self.assertIn("label: Base camp", rec)                 # ... and carried where it lands
        with open(self.defs, encoding="utf-8") as f:
            self.assertEqual("Base camp", json.load(f)["zmk"]["layers"]["0"]["label"])

    def test_a_carried_decision_follows_its_symbol_to_a_new_id(self):
        repo = os.path.join(self.d, "repo")
        write(repo, "boards/dia/defs/config.dtsi", "#define BASE 0\n#define NUM 1\n#define NAV 2\n")
        write(repo, "boards/dia/dia.keymap", RENUMBER_KEYMAP)
        write(self.d, "board.yaml", RENUMBER_YAML)
        with mock.patch.object(sync, "parse_keymap", return_value={"combos": []}):
            self.run_import(repo, "dia")
            # A decision carried by an earlier import, with no config pin to recreate it.
            rec = sync.read_imported(sync.imported_path(self.config))
            rec.update(source=repo, keyboard="dia")
            rec["layers"]["2"].update(drawer="Num", label="My navigation")
            sync.write_if_changed(sync.imported_path(self.config), sync.render(rec, []))
            write(repo, "boards/dia/defs/config.dtsi", "#define BASE 0\n#define NAV 1\n")
            write(repo, "boards/dia/dia.keymap", RENUMBER_KEYMAP_AFTER)
            self.run_import(repo, "dia")
        with open(self.defs, encoding="utf-8") as f:
            row = json.load(f)["zmk"]["layers"]["1"]
        self.assertEqual(("Num", "My navigation", "NAV"),
                         (row["drawer"], row["label"], row["symbol"]))

    def test_pristine_offers_to_keep_a_repo_mapping(self):
        repo = os.path.join(self.d, "repo")
        write(repo, "b.keymap", KEYMAP)
        with mock.patch.object(sync, "parse_keymap", return_value={"combos": []}):
            self.run_import(repo, "b")
            rec = sync.read_imported(sync.imported_path(self.config))
            rec.update(source=repo, keyboard="b")
            rec["layers"]["0"].update(drawer="Nav", label="Handmade")
            sync.write_if_changed(sync.imported_path(self.config), sync.render(rec, []))
            asked = []
            with contextlib.redirect_stderr(io.StringIO()):
                sync.do_import(repo, "b", self.config, pristine=True, fetch=False,
                               ask=lambda q: asked.append(q) or True)
        self.assertTrue(any("layer 0" in q for q in asked), asked)
        with open(self.defs, encoding="utf-8") as f:
            self.assertEqual("Handmade", json.load(f)["zmk"]["layers"]["0"]["label"])

    def test_pristine_drops_labels_from_all_layers_sharing_a_name(self):
        repo = os.path.join(self.d, "repo")
        write(repo, "b.keymap", KEYMAP)
        write(self.d, "config.yaml", "keymap: board.yaml\nlayers:\n  map:\n"
                                     "    NUMBERS: {drawer: Nav, label: Human}\n")
        with mock.patch.object(sync, "parse_keymap", return_value={"combos": []}):
            self.run_import(repo, "b")
            sync.do_import(repo, "b", self.config, quiet=True, pristine=True, keep=False, fetch=False)
        with open(self.defs, encoding="utf-8") as f:
            layers = json.load(f)["zmk"]["layers"]
        self.assertEqual(["Numbers", "Numbers"], [layers[i]["label"] for i in ("1", "2")])



@unittest.skipUnless(HAVE_YAML, "reading a config needs PyYAML or yq")
class Watch(unittest.TestCase):
    """`sync --watch`: a sync each time a file it reads is edited, and none in between."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        write(self.d, "board.yaml", BOARD_YAML)
        self.config = write(self.d, "config.yaml", "keymap: board.yaml\ntitle: T\n")
        self.defs = sync.definitions_path(self.config)

    def watch(self, *rounds):
        """The watch for as many rounds as it is given, each one's edit made while it waits;
        returns how many syncs ran and what they said went wrong."""
        steps = iter(rounds)
        err = io.StringIO()
        with mock.patch.object(sync, "do_import", wraps=sync.do_import) as syncs, contextlib.redirect_stderr(err):
            sync.watch(self.config, quiet=True, fetch=False, sleep=lambda _: next(steps)(), rounds=len(rounds))
        return syncs.call_count, err.getvalue()

    def first_key(self):
        with open(self.defs, encoding="utf-8") as f:
            return json.load(f)["drawing"]["layers"]["Base"][0]["tap"]

    def test_an_edit_is_synced_and_a_quiet_round_is_not(self):
        edit = lambda: write(self.d, "board.yaml", BOARD_YAML.replace("Base: [a,", "Base: [zz,"))  # noqa: E731
        count, _ = self.watch(lambda: None, edit, lambda: None)
        self.assertEqual(2, count)                       # the first sync, and the edit's
        self.assertEqual("zz", self.first_key())

    def test_a_sync_that_fails_is_said_and_the_watch_goes_on(self):
        broken = lambda: write(self.d, "board.yaml", "layers: [\n")  # noqa: E731
        whole = lambda: write(self.d, "board.yaml", BOARD_YAML.replace("Base: [a,", "Base: [q,"))  # noqa: E731
        count, said = self.watch(broken, whole)
        self.assertEqual(3, count)
        self.assertIn("zmk-layer-hud sync:", said)
        self.assertEqual("q", self.first_key())

    def test_what_is_watched(self):
        write(self.d, "config.yaml", "keymap: board.yaml\ndrawer_config: ~/nowhere/drawer.yaml\n")
        repo = os.path.join(self.d, "repo")
        keymap = write(repo, "config/b.keymap", '#include "defs.dtsi"\n' + KEYMAP)
        write(repo, "config/defs.dtsi", "#define X 1\n")
        files = sync.inputs(self.config, repo, "b")
        self.assertIn(os.path.join(self.d, "board.yaml"), files)
        self.assertIn(os.path.expanduser("~/nowhere/drawer.yaml"), files)   # watched for when it appears
        self.assertIn(keymap, files)
        self.assertIn(os.path.join(repo, "config", "defs.dtsi"), files)
        self.assertNotIn(self.defs, files)                                  # what it writes, never

    def test_a_drawings_layout_file_is_watched_and_an_unused_layout_is_not(self):
        # The Diamond's drawing names its layout from its repo's root; saving that JSON changes the
        # board as much as saving the drawing does. The config's own layout: is read only when the
        # drawing is made from the ZMK keymap, so with keymap: set it is not the board's.
        os.makedirs(os.path.join(self.d, "docs"))
        write(self.d, "docs/layout.json", "{}")
        write(self.d, "drawings/board.yaml", "layout: {qmk_info_json: docs/layout.json}\nlayers: {}\n")
        write(self.d, "config.yaml", "keymap: drawings/board.yaml\nlayout: {dts_layout: unused.dtsi}\n")
        files = sync.inputs(self.config)
        self.assertIn(os.path.join(self.d, "docs", "layout.json"), files)
        self.assertNotIn(os.path.join(self.d, "unused.dtsi"), files)

    def test_without_a_drawing_the_configs_layout_is_watched(self):
        write(self.d, "config.yaml", "layout: {dts_layout: shape.dtsi}\n")
        self.assertIn(os.path.join(self.d, "shape.dtsi"), sync.inputs(self.config))

    def test_the_keyboards_files_are_watched_only_when_its_layout_is_searched_for(self):
        # A sync looks for the physical layout in the keyboard's own files only when the drawing is
        # made from the ZMK keymap and the config says no layout: of its own.
        repo = os.path.join(self.d, "repo")
        write(repo, "config/b.keymap", KEYMAP)
        overlay = write(repo, "config/b.overlay", "/ { };\n")
        self.assertNotIn(overlay, sync.inputs(self.config, repo, "b"))
        write(self.d, "config.yaml", "layout: {dts_layout: shape.dtsi}\n")
        self.assertNotIn(overlay, sync.inputs(self.config, repo, "b"))
        write(self.d, "config.yaml", "title: T\n")
        self.assertIn(overlay, sync.inputs(self.config, repo, "b"))

    def test_a_repo_by_url_is_not_watched(self):
        with mock.patch.object(sync, "recorded", return_value=("https://github.com/you/zmk-config", None)):
            with self.assertRaisesRegex(sync.SyncError, "cannot be watched"):
                sync.watch(self.config, quiet=True, fetch=False, sleep=lambda _: None, rounds=1)


if __name__ == "__main__":
    unittest.main()
