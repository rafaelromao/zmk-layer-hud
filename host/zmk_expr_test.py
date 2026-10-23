"""host/zmk_expr.py: a binding's parameters read the way ZMK's preprocessor reads them."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import zmk_expr as z  # noqa: E402

VALUES = {"A": 0x70004, "TAB": 0x7002B, "MOD_LSFT": 0x02, "MOD_LCTL": 0x01, "MOD_RSFT": 0x20}
FUNCTIONS = {"APPLY_MODS": (["mods", "keycode"], "(mods << 24 | keycode)"),
             "LS": (["keycode"], "APPLY_MODS(MOD_LSFT, keycode)"),
             "LC": (["keycode"], "APPLY_MODS(MOD_LCTL, keycode)"),
             "BIT": (["n"], "(1 << (n))"),
             "MOVE_Y": (["vert"], "((vert) & 0xFFFF)")}


class Evaluate(unittest.TestCase):
    def test_names_numbers_and_operators(self):
        self.assertEqual(z.evaluate("A", VALUES), 0x70004)
        self.assertEqual(z.evaluate("(MOD_LSFT|MOD_RSFT)", VALUES), 0x22)
        self.assertEqual(z.evaluate("0x10 + 2 * 3", VALUES), 22)
        self.assertEqual(z.evaluate("7 / 2", VALUES), 3)

    def test_calls_nest(self):
        self.assertEqual(z.evaluate("LC(LS(TAB))", VALUES, FUNCTIONS), 0x0307002B)
        self.assertEqual(z.evaluate("BIT(3)", VALUES, FUNCTIONS), 8)

    def test_a_negative_move_wraps_like_c(self):
        self.assertEqual(z.evaluate("MOVE_Y(-600)", VALUES, FUNCTIONS), 0xFDA8)

    def test_an_object_macro_may_be_text(self):
        self.assertEqual(z.evaluate("SHIFTED_A", dict(VALUES, SHIFTED_A="LS(A)"), FUNCTIONS), 0x02070004)

    def test_what_it_cannot_read_is_unknown(self):
        for bad in ("NOT_A_KEY", "1 +", "(1", "LS(A, A)", "__import__('os')", "", "'a'"):
            with self.assertRaises(z.Unknown, msg=bad):
                z.evaluate(bad, VALUES, FUNCTIONS)

    def test_a_macro_that_names_itself_stops(self):
        with self.assertRaises(z.Unknown):
            z.evaluate("LOOP", {"LOOP": "LOOP + 1"})


class Split(unittest.TestCase):
    def test_parameters_are_whole_expressions(self):
        self.assertEqual(z.split_binding("&kp LS(A)"), ("kp", ["LS(A)"]))
        self.assertEqual(z.split_binding("&lt 1 SPACE"), ("lt", ["1", "SPACE"]))
        self.assertEqual(z.split_binding("&kp LS( LC( A ) )"), ("kp", ["LS(LC(A))"]))
        self.assertEqual(z.split_binding("&kp LS (A)"), ("kp", ["LS(A)"]))
        self.assertEqual(z.split_binding("&mt (MOD_LSFT | MOD_LCTL) A"), ("mt", ["(MOD_LSFT|MOD_LCTL)", "A"]))
        self.assertEqual(z.split_binding("&trans"), ("trans", []))

    def test_not_a_binding(self):
        with self.assertRaises(z.Unknown):
            z.split_binding("kp A")

    def test_cells_as_keymap_drawer_splits_them(self):
        self.assertEqual(z.cells(["(MOD_LSFT", "|", "MOD_RSFT)"]), ["(MOD_LSFT|MOD_RSFT)"])
        self.assertEqual(z.cells(["UNDERSCORE", "BACKSPACE", "DELETE"]), ["UNDERSCORE", "BACKSPACE", "DELETE"])
        self.assertEqual(z.cells(["1", "2", "LS(A)"]), ["1", "2", "LS(A)"])
        self.assertEqual(z.cells([]), [])


if __name__ == "__main__":
    unittest.main()
