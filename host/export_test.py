"""Tests for host/export.py: which step of heat each key gets, and the SVG keymap-drawer draws with
them. The steps need nothing but the standard library; the drawing needs keymap-drawer (the venv)."""

import os
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import export  # noqa: E402
import keymap as km  # noqa: E402

try:
    import keymap_drawer  # noqa: F401
    HAVE_DRAWER = True
except ImportError:
    HAVE_DRAWER = False

# keymap_test's board: 2x2 + a thumb a hand, 10 keys; on Nav the second row is transparent.
DOC = {
    "layout": {"cols_thumbs_notation": "22+1> 1<+22"},
    "layers": {
        "Base": [["a", "b", "c", "d"], ["e", {"t": "f", "h": "Nav"}, "g", "h"], {"t": "Sym", "s": "sticky"}, "␣"],
        "Nav": [["←", "→", "↑", "↓"], ["▽", "▽", "▽", "▽"], "▽", {"t": "␣", "type": "held"}],
        "Sym": [["!", "@", "#", "$"], ["%", "^", "&", "*"], "▽", "▽"],
    },
    "combos": [{"p": [0, 1], "k": "⎋", "layers": ["Base"]}],
}
MSG = km.build_message({}, DOC, fetch_glyphs=False)
SESSION = {
    "presses": {"Base": {"0": 500, "1": 40, "2": 3}, "Nav": {"0": 20, "4": 9}, "Sym": {}},
    "timed": {"Base": {"0": 100, "1": 10, "2": 2}},
    "ms": {"Base": {"0": 15000, "1": 3000, "2": 900}},
}


class Levels(unittest.TestCase):
    def test_the_session_is_each_layers_own_keys(self):
        steps = export.levels(SESSION, MSG, "session")
        self.assertEqual(6, steps["Base"][0])                        # the most pressed is the top step
        self.assertTrue(steps["Base"][0] > steps["Base"][1] > steps["Base"][2] >= 1)
        self.assertEqual({0: 6}, steps["Nav"])                       # key 4 is transparent on Nav
        self.assertNotIn("Sym", steps)                               # nothing counted there

    def test_physical_adds_every_layer_up_on_the_base(self):
        steps = export.levels(SESSION, MSG, "physical")
        self.assertEqual(["Base"], list(steps))
        self.assertEqual(6, steps["Base"][0])
        self.assertIn(4, steps["Base"])                              # its presses on Nav count

    def test_speed_is_the_slowest_hottest(self):
        steps = export.levels(SESSION, MSG, "speed")["Base"]
        self.assertEqual({0: 1, 1: 6}, steps)                        # 150 ms and 300 ms; key 2 timed twice only

    def test_a_mode_it_cannot_draw(self):
        with self.assertRaises(ValueError):
            export.levels(SESSION, MSG, "live")

    def test_positions_are_the_firmwares(self):
        msg = km.build_message({"positions": [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]}, DOC, fetch_glyphs=False)
        self.assertEqual(6, export.levels({"presses": {"Base": {"9": 5}}}, msg, "session")["Base"][0])


@unittest.skipUnless(HAVE_DRAWER, "drawing needs keymap-drawer (the venv)")
class Drawing(unittest.TestCase):
    def classes_of(self, svg):
        root = ET.fromstring(svg)
        out = {}
        for g in root.iter("{http://www.w3.org/2000/svg}g"):
            names = (g.get("class") or "").split()
            pos = next((n for n in names if n.startswith("keypos-")), None)
            if pos:
                out.setdefault(pos, []).append(set(names))
        return root, out

    def test_each_key_takes_its_step_and_the_steps_their_colours(self):
        svg = export.svg(SESSION, MSG, DOC, mode="session", footer="week1 < week2")
        root, keys = self.classes_of(svg)
        self.assertIn("hs6", keys["keypos-0"][0])                    # Base, the first layer drawn
        self.assertFalse(any(n.startswith("hs") for n in keys["keypos-3"][0]))
        style = "".join(s.text or "" for s in root.iter("{http://www.w3.org/2000/svg}style"))
        self.assertIn(f"rect.key.hs6 {{ fill: {export.RAMP[-1]}; }}", style)
        self.assertIn("week1 &lt; week2", svg)
        labels = [t.text for t in root.iter("{http://www.w3.org/2000/svg}text") if t.get("class") == "label"]
        self.assertEqual(["Base:", "Nav:"], labels)                  # the layers with heat, in order

    def test_the_layers_asked_for(self):
        root, _ = self.classes_of(export.svg(SESSION, MSG, DOC, layers=["Sym"]))
        labels = [t.text for t in root.iter("{http://www.w3.org/2000/svg}text") if t.get("class") == "label"]
        self.assertEqual(["Sym:"], labels)
        with self.assertRaises(ValueError):
            export.svg(SESSION, MSG, DOC, layers=["Fn"])
        with self.assertRaises(ValueError):
            export.svg({"presses": {}}, MSG, DOC)                    # nothing to draw


if __name__ == "__main__":
    unittest.main()
