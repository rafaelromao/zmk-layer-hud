"""host/keymap_message.py: a keymap message read as the page reads it (hud/view.js), checked on a small
hand-made message and on the Diamond's."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from keymap_message import KeymapMessage  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hud", "tests", "fixtures")

KEY = lambda t: {"tap": t}       # noqa: E731
TRANS = {"type": "trans"}
DATA = {
    "base": "base", "layer_order": ["base", "nav", "sym"],
    "layers": {"base": [KEY("a"), KEY("b"), KEY("c")], "nav": [KEY("←"), TRANS, {"tap": "␣", "type": "held"}],
               "sym": [TRANS, TRANS, KEY("!")]},
    "zmk_layers": {"0": {"drawer": "base"}, "1": {"drawer": "nav"}, "2": {"drawer": "nav"}, "3": {"drawer": None},
                   "4": {"drawer": "sym"}},
    "positions": {"10": 0, "11": 1, "12": 2},
    "combos": [{"positions": [0, 1], "layers": ["base"], "key": KEY("x")},
               {"positions": [1, 2], "layers": ["nav", "sym"], "key": KEY("y")}],
    "activators": [{"layer": "nav", "idx": 2, "kind": "hold"}, {"layer": "nav", "idx": 2, "kind": "sticky"}],
}


class Read(unittest.TestCase):
    def setUp(self):
        self.m = KeymapMessage(DATA)

    def test_layers(self):
        self.assertEqual("base", self.m.base)
        self.assertEqual(("nav", "nav", None, "sym"), tuple(self.m.drawer_of(i) for i in (1, 2, 3, 4)))
        self.assertEqual([1, 2], self.m.ids_of("nav"))
        self.assertEqual([], self.m.ids_of("nowhere"))

    def test_an_id_the_table_leaves_out_is_the_drawers_layer_of_that_number(self):
        m = KeymapMessage(dict(DATA, zmk_layers={"0": {"drawer": "base"}}))
        self.assertEqual("sym", m.drawer_of(2))
        self.assertIsNone(m.drawer_of(9))

    def test_positions(self):
        self.assertEqual((0, 2, None), (self.m.idx_at(10), self.m.idx_at(12), self.m.idx_at(13)))
        self.assertEqual((10, None), (self.m.pos_of(0), self.m.pos_of(5)))
        bare = KeymapMessage(dict(DATA, positions={}))
        self.assertEqual((13, 5), (bare.idx_at(13), bare.pos_of(5)))

    def test_the_stack(self):
        self.assertEqual(["nav", "base"], self.m.stack([2, 1, 3]))
        self.assertEqual(["sym", "nav", "base"], self.m.stack([4, 1]))
        self.assertEqual(["base"], self.m.stack([]))

    def test_a_key_resolves_through_transparency(self):
        self.assertEqual(("base", KEY("b")), self.m.resolve(1, ["nav", "base"]))
        self.assertEqual("nav", self.m.resolve(2, ["nav", "base"])[0])        # held: its layer's own
        self.assertEqual("nav", self.m.resolve(0, ["sym", "nav", "base"])[0])
        self.assertEqual((None, None), self.m.resolve(7, ["base"]))

    def test_combos_and_activators(self):
        self.assertEqual(["y"], [c["key"]["tap"] for c in self.m.combos_on("nav")])
        self.assertEqual([2], self.m.activators("nav"))

    def test_the_base_without_one_said(self):
        self.assertEqual("base", KeymapMessage(dict(DATA, base=None)).base)            # ZMK layer 0's
        self.assertEqual("base", KeymapMessage(dict(DATA, base=None, zmk_layers={})).base)   # the first

    def test_every_reader_takes_the_same_base(self):
        # A message with no base said: ZMK layer 0's drawer layer, as build_message picks it, for
        # the oracle and the export alike -- not the first drawn layer, not a KeyError.
        import export
        import ways
        data = dict(DATA, base=None, layer_order=["sym", "base", "nav"], layout={"keys": [{}] * 3})
        self.assertEqual("base", ways.Keymap(data).base)
        session = {"presses": {"base": {"10": 4}}}
        self.assertEqual(["base"], list(export.levels(session, data, "physical")))

    def test_the_diamond(self):
        with open(os.path.join(FIXTURES, "diamond.json"), encoding="utf-8") as f:
            data = json.load(f)
        m = KeymapMessage(data)
        self.assertEqual(data["base"], m.base)
        for pos, idx in data["positions"].items():
            self.assertEqual(idx, m.idx_at(int(pos)))
            self.assertEqual(int(pos), m.pos_of(idx))


if __name__ == "__main__":
    unittest.main()
