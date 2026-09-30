"""Tests for host/sync.py — what `import` and `sync` write down, and how.

Cloning a repo is not tested here, and keymap-drawer's own parser is faked where a test needs its
output: what is tested is everything that turns what came back into the HUD's definitions -- the
layer order and names, the mapping that is a human's to make, the coverage, the physical layout a
drawing of the keymap is drawn on, the files and how they are written, and the delta a sync reports.
"""

import json
import os
import shutil
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


@unittest.skipUnless(HAVE_YAML, "reading a config needs PyYAML or yq")
class Import(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        write(self.d, "board.yaml", BOARD_YAML)
        self.config = write(self.d, "config.yaml", "keymap: board.yaml\ntitle: T\n")
        self.defs = sync.definitions_path(self.config)

    def run_import(self, source=None, keyboard=None):
        return sync.do_import(source, keyboard, self.config, quiet=True, fetch=False)

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


if __name__ == "__main__":
    unittest.main()
