"""Tests for host/sync.py — the parts that decide what `import` writes down.

Reading a repo and running keymap-drawer over it are not tested here (they need both); what is
tested is everything that turns what came back into config: the layer order, the mapping that is a
human's to make, the coverage, and the delta a sync reports.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sync  # noqa: E402


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

    def test_a_first_import_has_nothing_to_compare_against(self):
        self.assertEqual(sync.delta(None, self.after()), [])


class Quoting(unittest.TestCase):
    def test_a_plain_word_is_left_alone(self):
        self.assertEqual(sync.yq("alpha1"), "alpha1")
        self.assertEqual(sync.yq("Alpha 1"), "Alpha 1")

    def test_anything_yaml_would_read_as_something_else_is_quoted(self):
        for value in ("off", "no", "true", "null", "ç-extension", "Media / mouse", "Ç EXTENSION"):
            self.assertTrue(sync.yq(value).startswith('"'), value)


if __name__ == "__main__":
    unittest.main()
