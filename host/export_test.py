"""Tests for host/export.py: which step of heat each key gets, and the SVG keymap-drawer draws with
them. The steps need nothing but the standard library; the drawing needs keymap-drawer (the venv)."""

import math
import os
import re
import sys
import unittest
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

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
# The definitions import would write for it, and the message the HUD builds from them.
DEFS = km.draw(DOC, fetch=False)
MSG = km.build_message({}, DEFS["drawing"])
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
        msg = km.build_message({"positions": [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]}, DEFS["drawing"])
        self.assertEqual(6, export.levels({"presses": {"Base": {"9": 5}}}, msg, "session")["Base"][0])


class Themes(unittest.TestCase):
    """The steps in the HUD's two themes: YlOrRd on keymap-drawer's light keys, and on the dark keys
    one indigo, the way LayoutMaster draws its board."""

    def test_the_steps_are_the_huds_own(self):
        with open(os.path.join(os.path.dirname(HERE), "hud", "hud.css"), encoding="utf-8") as f:
            css = f.read()
        light = re.findall(r"^\.key\.hs(\d)::before \{ background: (#[0-9a-f]{6})", css, re.M)
        dark = re.findall(r"^body\.dark \.key\.hs(\d)::before \{ background: (#[0-9a-f]{6})", css, re.M)
        self.assertEqual(export.RAMP, tuple(c for _, c in sorted(light)))
        self.assertEqual(export.DARK_RAMP, tuple(c for _, c in sorted(dark)))

    def test_the_dark_steps_go_light_blue_to_dark_blue_and_their_legends_read(self):
        # What the ramp was chosen by: one indigo, light blue cold and dark blue hot, each step darker
        # than the one before and plainly apart from it (OKLab, 0.05); the hottest still stands out
        # on the key (WCAG 1.4:1, and above all its blue against the key's grey); and every step's legends
        # read on it (3:1, the legends being large): dark on the colder steps, light on the hotter.
        def lin(h):
            return [(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
                    for c in (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))]

        def contrast(a, b):
            ys = sorted((0.2126 * r + 0.7152 * g + 0.0722 * bl for r, g, bl in (lin(a), lin(b))), reverse=True)
            return (ys[0] + 0.05) / (ys[1] + 0.05)

        def oklab(h):
            r, g, b = lin(h)
            l, m, s = ((0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3),
                       (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3),
                       (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3))
            return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
                    1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
                    0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)

        ramp, key = export.DARK_RAMP, export.DARK_BOARD["key"]
        self.assertGreaterEqual(contrast(ramp[-1], key), 1.4)
        labs = [oklab(c) for c in ramp]
        for (l1, a1, b1), (l2, a2, b2) in zip(labs, labs[1:]):
            self.assertLess(l2, l1)                                             # darker, the hotter
            self.assertGreaterEqual(math.dist((l1, a1, b1), (l2, a2, b2)), 0.05)
        self.assertGreater(math.hypot(*labs[-1][1:]), 4 * math.hypot(*oklab(key)[1:]))   # blue on grey
        hues = [math.degrees(math.atan2(b, a)) for _, a, b in labs]
        self.assertLess(max(hues) - min(hues), 5)                               # one indigo
        for step, c in enumerate(ramp, 1):
            ink = export.DARK_COLD_LEGEND if step <= export.DARK_COLD else export.DARK_BOARD["text"]
            self.assertGreaterEqual(contrast(c, ink), 3.0, (step, c, ink))
        self.assertIn(f"g.hs1 text, g.hs2 text, g.hs3 text {{ fill: {export.DARK_COLD_LEGEND}; }}",
                      export.stylesheet(dark=True))

    def test_light_keys_are_keymap_drawers_under_the_steps(self):
        style = export.stylesheet()
        self.assertIn(f"rect.key.hs6 {{ fill: {export.RAMP[-1]}; }}", style)
        self.assertNotIn("rect.key {", style)                    # the key's own fill stays keymap-drawer's

    def test_dark_keys_are_the_huds_dark_board(self):
        style = export.stylesheet(dark=True)
        self.assertIn(f"rect.key {{ fill: {export.DARK_BOARD['key']}; }}", style)
        self.assertIn(f"rect.key.hs6 {{ fill: {export.DARK_RAMP[-1]}; }}", style)
        self.assertIn(f"background-color: {export.DARK_BOARD['background']}", style)
        self.assertNotIn(export.RAMP[-1], style)


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
        svg = export.svg(SESSION, MSG, DEFS, mode="session", footer="week1 < week2")
        root, keys = self.classes_of(svg)
        self.assertIn("hs6", keys["keypos-0"][0])                    # Base, the first layer drawn
        self.assertFalse(any(n.startswith("hs") for n in keys["keypos-3"][0]))
        style = "".join(s.text or "" for s in root.iter("{http://www.w3.org/2000/svg}style"))
        self.assertIn(f"rect.key.hs6 {{ fill: {export.RAMP[-1]}; }}", style)
        self.assertIn("week1 &lt; week2", svg)
        dark = export.svg(SESSION, MSG, DEFS, mode="session", dark=True)
        self.assertIn(f"rect.key.hs6 {{ fill: {export.DARK_RAMP[-1]}; }}", dark)
        labels = [t.text for t in root.iter("{http://www.w3.org/2000/svg}text") if t.get("class") == "label"]
        self.assertEqual(["Base:", "Nav:"], labels)                  # the layers with heat, in order

    def test_the_layers_asked_for(self):
        root, _ = self.classes_of(export.svg(SESSION, MSG, DEFS, layers=["Sym"]))
        labels = [t.text for t in root.iter("{http://www.w3.org/2000/svg}text") if t.get("class") == "label"]
        self.assertEqual(["Sym:"], labels)
        with self.assertRaises(ValueError):
            export.svg(SESSION, MSG, DEFS, layers=["Fn"])
        with self.assertRaises(ValueError):
            export.svg({"presses": {}}, MSG, DEFS)                    # nothing to draw

    def test_it_is_drawn_from_the_definitions_alone(self):
        # No file of the user's and no network: the layout is the drawn keys, handed over as a QMK
        # layout, and the glyphs are the ones the definitions carry.
        import urllib.request
        from unittest import mock
        defs = km.draw(DOC, fetch=False)
        defs["drawing"]["glyphs"] = {"mdi:x": "<svg xmlns='http://www.w3.org/2000/svg'/>"}
        with mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("fetched")):
            svg = export.svg(SESSION, MSG, defs)
        root, keys = self.classes_of(svg)
        self.assertEqual(10, len({k for k in keys}))                  # every key of the board, drawn


if __name__ == "__main__":
    unittest.main()
