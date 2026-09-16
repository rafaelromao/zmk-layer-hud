"""Tests for host/keymap.py: the pure conversion from parsed dicts to the HUD's keymap message.
keymap-drawer and PyYAML are optional here; the fallback paths are what run without them."""

import os
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


class Keys(unittest.TestCase):
    def test_norm_key_forms(self):
        self.assertEqual(km.norm_key("a")["tap"], "a")
        self.assertEqual(km.norm_key(7)["tap"], "7")
        self.assertEqual(km.norm_key(None)["type"], "blank")
        self.assertEqual(km.norm_key("▽")["type"], "trans")
        k = km.norm_key({"t": "$$mdi:keyboard-return$$", "h": "Nav", "s": "sticky", "type": "held"})
        self.assertEqual((k["tap"], k["hold"], k["shifted"], k["type"], k["glyph"]), ("↵", "Nav", "sticky", "held", "mdi:keyboard-return"))
        self.assertEqual(km.norm_key({"tap": "x", "hold": "y", "shifted": "z"}), {"tap": "x", "hold": "y", "shifted": "z", "type": "key"})

    def test_unknown_glyph_uses_its_name(self):
        self.assertEqual(km.legend("$$mdi:something-new$$")[0], "something-new")


class Layout(unittest.TestCase):
    def test_cpt_fallback_geometry(self):
        lay = km.cpt_layout("22+1> 1<+22", key_w=10, key_h=10, split_gap=5)
        self.assertEqual(len(lay["keys"]), 10)
        xs = [k["x"] for k in lay["keys"][:4]]
        self.assertEqual(xs, [5, 15, 30, 40])          # row 0: left hand, gap, right hand
        self.assertEqual(lay["keys"][8]["y"], 25)       # thumbs on row 2
        self.assertEqual((lay["width"], lay["height"]), (45, 30))

    def test_diamond_notation(self):
        lay = km.cpt_layout("1333+2> 2<+3331")
        self.assertEqual(len(lay["keys"]), 24)


class Message(unittest.TestCase):
    def test_default_layer_ids_follow_yaml_order(self):
        msg = km.build_message({}, DOC)
        self.assertEqual([z["name"] for z in msg["zmk_layers"].values()], ["Base", "Nav", "Sym"])
        self.assertEqual(msg["zmk_layers"]["1"]["drawer"], "Nav")
        self.assertEqual(msg["zmk_layers"]["0"]["cls"], "off")
        self.assertEqual(msg["base"], "Base")
        self.assertIsNone(msg["codes"])
        self.assertEqual(msg["signal"], {"base": 0xC0, "commit": 0xDF})

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

    def test_codes(self):
        cfg = {"codes": {1: {"layers": ["Base", "Nav"], "label": "Vim normal", "class": "vim", "vim": True}}}
        msg = km.build_message(cfg, DOC)
        self.assertEqual(msg["codes"]["1"]["layers"], ["Base", "Nav"])
        self.assertTrue(msg["codes"]["1"]["vim"])
        with self.assertRaises(km.KeymapError):
            km.build_message({"codes": {1: {"layers": ["Nope"]}}}, DOC)

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
