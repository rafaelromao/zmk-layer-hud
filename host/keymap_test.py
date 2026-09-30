"""Tests for host/keymap.py: the pure conversion from parsed dicts to the HUD's keymap message.
keymap-drawer and PyYAML are optional here; the fallback paths are what run without them."""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import keymap as km  # noqa: E402

try:
    import keymap_drawer  # noqa: F401
    HAVE_DRAWER = True
except ImportError:
    HAVE_DRAWER = False

# A 2x2 + 1 thumb per hand keyboard: "22+1> 1<+22" = 10 keys, drawer layer order = ZMK order.
DOC = {
    "layout": {"cols_thumbs_notation": "22+1> 1<+22"},
    "layers": {
        "Base": [["a", "b", "c", "d"], ["e", {"t": "f", "h": "Nav"}, "g", "h"], {"t": "Sym", "s": "sticky"}, "␣"],
        "Nav": [["←", "→", "↑", "↓"], ["▽", "▽", "▽", "▽"], "▽", {"t": "␣", "type": "held"}],
        "Sym": [["!", "@", "#", "$"], ["%", "^", "&", "*"], "▽", "▽"],
    },
    "combos": [{"p": [0, 1], "k": "⎋", "layers": ["Base"]}, {"p": [2, 3], "k": "⇥"}],
}
DTSI = "// Layers\n\n#define BASE 0\n#define NAV 1\n#define SYM 2\n\n// Settings\n#define COMBO_TERM 30\n"

# A 3x5+3 split drawn from its key count alone: examples/3x5.yaml's shape, and most 3x5s'.
DOC_3X5 = {
    "layout": {"cols_thumbs_notation": "33333+3 3+33333"},
    "layers": {"Base": [f"k{i}" for i in range(36)]},
}


class Keys(unittest.TestCase):
    def test_norm_key_forms(self):
        self.assertEqual(km.norm_key("a")["tap"], "a")
        self.assertEqual(km.norm_key(7)["tap"], "7")
        self.assertEqual(km.norm_key(None)["type"], "blank")
        self.assertEqual(km.norm_key("▽")["type"], "trans")
        # A key that *draws* ▽ is not a transparent one. GLYPHS renders
        # mdi:arrow-down-circle-outline as ▽, and taking that for the drawer's transparent marker
        # sent the media layer's scroll-down key to the layer underneath, where it drew an `f`.
        drawn = km.norm_key("$$mdi:arrow-down-circle-outline$$")
        self.assertEqual(drawn["type"], "key")
        self.assertEqual(drawn["glyph"], "mdi:arrow-down-circle-outline")
        k = km.norm_key({"t": "$$mdi:keyboard-return$$", "h": "Nav", "s": "sticky", "type": "held"})
        self.assertEqual((k["tap"], k["hold"], k["shifted"], k["type"], k["glyph"]), ("↵", "Nav", "sticky", "held", "mdi:keyboard-return"))
        self.assertEqual(km.norm_key({"tap": "x", "hold": "y", "shifted": "z"}), {"tap": "x", "hold": "y", "shifted": "z", "type": "key"})

    def test_unknown_glyph_uses_its_name(self):
        self.assertEqual(km.legend("$$mdi:something-new$$")[0], "something-new")

    def test_a_glyph_inside_a_longer_legend_becomes_its_spelling(self):
        # "$$mdi:magnify$$l" means a search icon and the letter l. A legend carries one glyph and
        # the glyph replaces the text, so an embedded one is spelled out instead — anything else
        # would reach the key as the literal characters $$mdi:magnify$$.
        self.assertEqual(km.legend("$$mdi:magnify$$l"), ("🔍l", None))
        self.assertEqual(km.legend("go $$mdi:magnify$$"), ("go 🔍", None))
        # A glyph on its own is still a glyph; its text is only the fallback when the SVG is missing.
        self.assertEqual(km.legend("$$mdi:magnify$$"), ("🔍", "mdi:magnify"))


class Layout(unittest.TestCase):
    def test_cpt_fallback_geometry(self):
        lay = km.cpt_layout("22+1> 1<+22", key_w=10, key_h=10, split_gap=5)
        self.assertEqual(len(lay["keys"]), 10)
        xs = [k["x"] for k in lay["keys"][:4]]
        # left cols 0,1 with its thumb at 1.5 (max x 1.5 -> right hand offset 2.5 keys + the gap);
        # the right thumb at -0.5 shifts that hand half a key: cols at 3, 4 -> centres 40, 50 (+5 gap)
        self.assertEqual(xs, [5, 15, 40, 50])
        self.assertEqual(lay["keys"][8]["y"], 25)       # thumbs on row 2
        self.assertEqual((lay["width"], lay["height"]), (55, 30))

    def test_diamond_notation_order_is_row_hand_column(self):
        # keymap-drawer sorts by integer row, then part (hand), then column: row 0 has 6 keys
        # (cols 1-3 left, 0-2 right), row 1 has 8, row 2 has 6, then the 4 thumbs.
        lay = km.cpt_layout("1333+2> 2<+3331", key_w=10, key_h=10, split_gap=0)
        self.assertEqual(len(lay["keys"]), 24)
        rows = [int(k["y"] // 10) for k in lay["keys"]]
        self.assertEqual(rows, [0] * 6 + [1] * 8 + [2] * 6 + [3] * 4)
        xs = [k["x"] for k in lay["keys"]]
        # Left hand: cols at 0..3; its "2>" thumbs sit half a key inward (2.5, 3.5) so max x is 3.5
        # and the right hand starts at 4.5. The right hand's "2<" thumbs start at -0.5, which the
        # drawer normalises away by shifting that whole hand half a key: cols at 5..8, thumbs 4.5, 5.5.
        self.assertEqual(xs[:6], [15, 25, 35, 55, 65, 75])            # row 0: left cols 1-3, right cols 0-2
        self.assertEqual(xs[6:14], [5, 15, 25, 35, 55, 65, 75, 85])   # row 1: every column
        self.assertEqual(xs[20:], [30, 40, 50, 60])                   # thumbs hug the inner edges

    def test_shifted_columns(self):
        lay = km.cpt_layout("3v3 33", key_w=10, key_h=10, split_gap=0)
        # a "v" column sits half a row lower than its neighbour; it still sorts into rows 0..2
        col0 = [k for k in lay["keys"] if k["x"] == 5]
        self.assertEqual([k["y"] for k in col0], [10, 20, 30])
        self.assertEqual(lay["keys"][0], col0[0])                      # row 0, part 0, leftmost column


class Ortho(unittest.TestCase):
    def test_split_with_thumbs(self):
        lay = km.ortho_layout({"split": True, "rows": 3, "columns": 5, "thumbs": 3}, key_w=10, key_h=10, split_gap=5)
        self.assertEqual(len(lay["keys"]), 36)
        self.assertEqual([k["x"] for k in lay["keys"][:10]], [5, 15, 25, 35, 45, 60, 70, 80, 90, 100])
        thumbs = lay["keys"][30:]
        self.assertEqual([(k["x"], k["y"]) for k in thumbs], [(25, 35), (35, 35), (45, 35), (60, 35), (70, 35), (80, 35)])
        self.assertEqual((lay["width"], lay["height"]), (105, 40))

    def test_mit_bottom_row(self):
        lay = km.ortho_layout({"rows": 4, "columns": 12, "thumbs": "MIT"}, key_w=10, key_h=10)
        self.assertEqual(len(lay["keys"]), 3 * 12 + 11)
        wide = [k for k in lay["keys"] if k["w"] == 20]
        self.assertEqual([(k["x"], k["y"]) for k in wide], [(60, 35)])

    def test_drop_pinky_shifts_then_drops(self):
        lay = km.ortho_layout({"split": True, "rows": 3, "columns": 5, "drop_pinky": True}, key_w=10, key_h=10, split_gap=0)
        self.assertEqual(lay["keys"][0]["y"], 10)          # row 0 pinky is half a key lower
        self.assertEqual(len(lay["keys"]), 2 * 10 + 8)      # last row loses both pinkies

    def test_ortho_through_build_message(self):
        doc = dict(DOC, layout={"ortho_layout": {"split": True, "rows": 2, "columns": 2, "thumbs": 1}})
        msg = km.build_message({}, doc)
        self.assertEqual(len(msg["layout"]["keys"]), 10)


class Stagger(unittest.TestCase):
    """A 3x5 split that keymap-drawer would draw ortholinear is drawn with a Ferris Sweep's
    column stagger (QMK ferris/sweep: pinky 0.93 of a key below the middle column, ring 0.31,
    index 0.28, inner index 0.42), and nothing else moves."""

    def drop_of(self, layout):
        """Each key's distance below the highest key, in key heights."""
        kh = min(k["h"] for k in layout["keys"])
        top = min(k["y"] for k in layout["keys"])
        return [(k["y"] - top) / kh for k in layout["keys"]]

    def test_a_3x5_split_is_drawn_with_the_ferris_stagger(self):
        drop = self.drop_of(km.build_message({}, DOC_3X5)["layout"])
        ferris = [0.93, 0.31, 0.0, 0.28, 0.42]
        for row in range(3):   # each row of both hands, the right one mirrored
            got = drop[row * 10:row * 10 + 10]
            for got_, want in zip(got, [row + d for d in ferris + ferris[::-1]]):
                self.assertAlmostEqual(got_, want, places=2)

    def test_the_keys_keep_the_keymap_order(self):
        # Only y moves: key i is still the i-th binding, in the column it was drawn in.
        ortho = km.physical_layout(DOC_3X5["layout"], None)
        staggered = km.build_message({}, DOC_3X5)["layout"]
        self.assertEqual([k["x"] for k in staggered["keys"]], [k["x"] for k in ortho["keys"]])
        self.assertGreater(staggered["height"], ortho["height"])

    def test_a_thumb_clears_every_column_above_it(self):
        keys = km.build_message({}, DOC_3X5)["layout"]["keys"]
        for t in keys[30:]:
            above = [k for k in keys[:30] if abs(k["x"] - t["x"]) < (k["w"] + t["w"]) / 2 - 0.1]
            self.assertTrue(above)
            gap = min((t["y"] - t["h"] / 2) - (k["y"] + k["h"] / 2) for k in above) / t["h"]
            self.assertAlmostEqual(gap, km.THUMB_CLEARANCE, places=2)

    def test_the_same_split_through_ortho_layout(self):
        doc = dict(DOC_3X5, layout={"ortho_layout": {"split": True, "rows": 3, "columns": 5, "thumbs": 3}})
        drop = self.drop_of(km.build_message({}, doc)["layout"])
        self.assertAlmostEqual(drop[0] - drop[2], 0.93, places=2)   # pinky below middle

    def test_stagger_false_keeps_it_ortholinear(self):
        layout = km.build_message({"stagger": False}, DOC_3X5)["layout"]
        self.assertEqual(layout, km.physical_layout(DOC_3X5["layout"], None))

    def test_a_layout_that_says_more_is_the_boards_own(self):
        for spec in ("33333+3 3+33333", "33333 33333", "33333+2> 2<+33333"):
            self.assertTrue(km.plain_3x5_split({"cols_thumbs_notation": spec}), spec)
        for spec in ("3v3333+3 3+33333", "33333^+3 3+33333", "333333+3 3+333333", "33333+3", "3333333333"):
            self.assertFalse(km.plain_3x5_split({"cols_thumbs_notation": spec}), spec)
        self.assertFalse(km.plain_3x5_split({"ortho_layout": {"split": True, "rows": 4, "columns": 5}}))
        self.assertFalse(km.plain_3x5_split({"ortho_layout": {"split": True, "rows": 3, "columns": 5, "drop_pinky": True}}))
        self.assertFalse(km.plain_3x5_split({"qmk_keyboard": "ferris/sweep"}))


class Settings(unittest.TestCase):
    def test_hud_and_feed_defaults_are_filled_in(self):
        msg = km.build_message({}, DOC)
        self.assertEqual(msg["hud"], km.HUD_DEFAULTS)
        self.assertEqual(msg["combo_term"], 50)

    def test_hud_overrides_and_validation(self):
        msg = km.build_message({"hud": {"width": 700, "opacity": 40}, "combo_term_ms": 30}, DOC)
        self.assertEqual((msg["hud"]["width"], msg["hud"]["opacity"], msg["combo_term"]), (700, 40, 30))
        with self.assertRaises(km.KeymapError):
            km.build_message({"hud": {"nope": 1}}, DOC)
        with self.assertRaises(km.KeymapError):
            km.build_message({"hud": {"opacity": 120}}, DOC)

    def test_a_setting_that_is_not_a_number_says_so(self):
        # Not a traceback, and not quietly something else.
        for bad in ("fast", "320ms", 2.5, True, None, [320]):
            with self.assertRaises(km.KeymapError) as e:
                km.build_message({"hud": {"press_ms": bad}}, DOC)
            self.assertIn("hud.press_ms must be a whole number", str(e.exception))
        self.assertEqual(320, km.build_message({"hud": {"press_ms": 320.0}}, DOC)["hud"]["press_ms"])

    def test_the_page_falls_back_to_the_same_defaults(self):
        # hud.js keeps its own copy for a keymap message without `hud` (a dump older than the
        # setting, the committed test fixture). A setting added here and not there reads as
        # undefined on the page: a timer of NaN ms, a glow that never shows. `width` is the one
        # the page takes from the message alone (postSize).
        page = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hud", "hud.js")
        with open(page, encoding="utf-8") as f:
            block = re.search(r"const DEFAULTS = \{(.*?)\};", f.read(), re.S).group(1)
        on_page = {k: int(v) for k, v in re.findall(r"(\w+):\s*(\d+)", block)}
        self.assertEqual(on_page, {k: v for k, v in km.HUD_DEFAULTS.items() if k != "width"})

    def test_positions_map(self):
        msg = km.build_message({}, DOC)
        self.assertEqual(msg["positions"]["3"], 3)                       # identity by default
        msg = km.build_message({"positions": [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]}, DOC)
        self.assertEqual(msg["positions"]["9"], 0)
        with self.assertRaises(km.KeymapError):
            km.build_message({"positions": [1, 2]}, DOC)

    def test_relative_paths_resolve_against_the_config_dir(self):
        self.assertEqual(km.expand("../examples/x.yaml", "/a/b/config"), "/a/b/examples/x.yaml")
        self.assertEqual(km.expand("/abs/x.yaml", "/a/b/config"), "/abs/x.yaml")
        self.assertTrue(km.expand("~/x.yaml", "/a").startswith(os.path.expanduser("~")))

    def test_glyph_url_templates(self):
        urls = km.GLYPH_URLS
        self.assertTrue(km.glyph_url("mdi:cog", urls).endswith("/svg/cog.svg"))
        self.assertEqual(km.glyph_url("shift_command", urls), None)     # no source: inline only
        self.assertIn("bold/x-bold.svg", km.glyph_url("phosphor:bold/x", urls))


class Message(unittest.TestCase):
    def test_default_layer_ids_follow_yaml_order(self):
        msg = km.build_message({}, DOC)
        self.assertEqual([z["name"] for z in msg["zmk_layers"].values()], ["Base", "Nav", "Sym"])
        self.assertEqual(msg["zmk_layers"]["1"]["drawer"], "Nav")
        self.assertEqual(msg["zmk_layers"]["0"]["cls"], "off")
        self.assertEqual(msg["base"], "Base")

    def test_dtsi_ids_and_case_insensitive_drawer_match(self):
        msg = km.build_message({"layers": {"dtsi": "x"}}, DOC, dtsi_text=DTSI)
        self.assertEqual(msg["zmk_layers"]["2"], {"id": 2, "name": "SYM", "drawer": "Sym", "label": "Sym", "cls": "momentary"})

    def test_map_overrides(self):
        cfg = {"layers": {"map": {"Nav": {"label": "Navigation", "class": "vim"}, "2": None}}}
        msg = km.build_message(cfg, DOC)
        self.assertEqual(msg["zmk_layers"]["1"]["label"], "Navigation")
        self.assertEqual(msg["zmk_layers"]["1"]["cls"], "vim")
        self.assertIsNone(msg["zmk_layers"]["2"]["drawer"])

    def test_map_to_unknown_drawer_layer_fails(self):
        with self.assertRaises(km.KeymapError):
            km.build_message({"layers": {"map": {"Nav": "Nope"}}}, DOC)

    def test_combos_and_overrides(self):
        msg = km.build_message({"combos": [{"positions": [2, 3], "layers": ["Nav"]}]}, DOC)
        by_pos = {tuple(c["positions"]): c for c in msg["combos"]}
        self.assertEqual(by_pos[(0, 1)]["layers"], ["Base"])
        self.assertEqual(by_pos[(0, 1)]["key"]["tap"], "⎋")
        self.assertEqual(by_pos[(2, 3)]["layers"], ["Nav"])   # overridden (drawer said: all layers)

    def test_activators(self):
        msg = km.build_message({"extras": {"sticky": ["Sym"]}}, DOC)
        kinds = {(a["layer"], a["idx"], a["kind"]) for a in msg["activators"]}
        self.assertIn(("Nav", 5, "hold"), kinds)      # hold legend "Nav" on Base key 5
        self.assertIn(("Sym", 8, "sticky"), kinds)    # s: sticky with tap "Sym"
        self.assertIn(("Nav", 9, "hold"), kinds)      # type: held on Nav itself

    def test_extras_are_validated_and_defaulted(self):
        msg = km.build_message({}, DOC)
        self.assertEqual(msg["extras"]["search"], ["Nav", "Sym"])
        self.assertEqual(msg["extras"]["base"], "Base")
        with self.assertRaises(km.KeymapError):
            km.build_message({"extras": {"alpha2": "Nope"}}, DOC)

    def test_layer_size_mismatch_fails(self):
        bad = {"layout": DOC["layout"], "layers": {"Base": [["a", "b"]]}}
        with self.assertRaises(km.KeymapError):
            km.build_message({}, bad)

    def test_parse_layer_ids(self):
        self.assertEqual(km.parse_layer_ids(DTSI), {"BASE": 0, "NAV": 1, "SYM": 2})
        with self.assertRaises(km.KeymapError):
            km.parse_layer_ids("#define X 1\n")


@unittest.skipUnless(HAVE_DRAWER, "keymap-drawer not installed")
class WithDrawer(unittest.TestCase):
    def test_ortho_layout_via_library(self):
        doc = dict(DOC, layout={"ortho_layout": {"split": True, "rows": 2, "columns": 2, "thumbs": 1}})
        msg = km.build_message({}, doc)
        self.assertEqual(len(msg["layout"]["keys"]), 10)
        self.assertTrue(all(k["w"] > 0 and k["h"] > 0 for k in msg["layout"]["keys"]))


if __name__ == "__main__":
    unittest.main()
