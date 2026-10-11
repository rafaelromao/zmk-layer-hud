"""host/zmk_tables.py: the facts the touch keyboard leans on, held as relations between ZMK's own
names rather than numbers written here -- and, given a ZMK checkout ($ZMK_DIR), the module equal
to what host/zmk_tables_gen.py makes of it."""

import io
import os
import sys
import unittest
from contextlib import redirect_stderr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import zmk_expr  # noqa: E402
import zmk_tables as T  # noqa: E402
import zmk_tables_gen  # noqa: E402


def value(expr):
    return zmk_expr.evaluate(expr, dict(T.KEYCODES, **T.MODIFIERS, **T.POINTING, **T.PAGES), T.FUNCTIONS)


class Keycodes(unittest.TestCase):
    def test_a_shifted_symbol_is_its_key_with_the_left_shift(self):
        self.assertEqual(T.KEYCODES["EXCL"], T.KEYCODES["N1"] | T.MODIFIERS["MOD_LSFT"] << 24)
        self.assertEqual(value("LS(N1)"), T.KEYCODES["EXCL"])

    def test_aliases_share_a_value(self):
        for a, b in (("C_PP", "C_PLAY_PAUSE"), ("LSHFT", "LEFT_SHIFT"), ("BSPC", "BACKSPACE"),
                     ("RET", "ENTER"), ("N1", "NUMBER_1")):
            self.assertEqual(T.KEYCODES[a], T.KEYCODES[b], (a, b))

    def test_every_keycode_is_on_a_page_the_hud_knows(self):
        pages = {T.PAGES["HID_USAGE_GD"], T.PAGES["HID_USAGE_KEY"], T.PAGES["HID_USAGE_CONSUMER"]}
        for name, code in T.KEYCODES.items():
            self.assertIn((code >> 16) & 0xFF, pages, name)

    def test_letters_are_the_keyboard_page_from_4(self):
        for i, letter in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
            self.assertEqual(T.KEYCODES[letter], T.PAGES["HID_USAGE_KEY"] << 16 | 0x04 + i)

    def test_modifier_functions_nest(self):
        self.assertEqual(value("LC(LS(TAB))"), T.KEYCODES["TAB"] | (T.MODIFIERS["MOD_LCTL"] | T.MODIFIERS["MOD_LSFT"]) << 24)
        self.assertEqual(value("(MOD_LSFT|MOD_RSFT)"), 0x22)

    def test_pointing_buttons_are_bits(self):
        self.assertEqual(T.POINTING["LCLK"], value("BIT(0)"))
        self.assertEqual(T.POINTING["RCLK"], value("BIT(1)"))


class Behaviors(unittest.TestCase):
    def test_the_hold_taps_ship_with_their_flavours(self):
        mt, lt = T.BEHAVIORS["mt"], T.BEHAVIORS["lt"]
        self.assertEqual((mt["compatible"], mt["cells"]), ("zmk,behavior-hold-tap", 2))
        self.assertEqual(mt["props"]["flavor"], "hold-preferred")
        self.assertEqual(lt["props"]["flavor"], "tap-preferred")
        self.assertEqual(mt["props"]["bindings"], ["&kp", "&kp"])
        self.assertEqual(lt["props"]["bindings"], ["&mo", "&kp"])
        self.assertEqual(int(mt["props"]["tapping-term-ms"][0]), 200)

    def test_sticky_keys_and_layers(self):
        sk, sl = T.BEHAVIORS["sk"]["props"], T.BEHAVIORS["sl"]["props"]
        self.assertTrue(sk.get("ignore-modifiers"))
        self.assertTrue(sl.get("quick-release"))
        self.assertNotIn("quick-release", sk)
        self.assertEqual(sl["bindings"], ["&mo"])

    def test_one_param_behaviours(self):
        for label in ("kp", "mo", "tog", "to", "sk", "sl", "kt", "mkp"):
            self.assertEqual(T.BEHAVIORS[label]["cells"], 1, label)
        for label in ("trans", "none", "key_repeat", "caps_word"):
            self.assertEqual(T.BEHAVIORS[label]["cells"], 0, label)

    def test_the_macro_controls_are_there_for_reading_macros(self):
        for label in ("macro_tap", "macro_press", "macro_release", "macro_wait_time", "macro_tap_time",
                      "macro_pause_for_release", "macro_param_1to1"):
            self.assertIn(label, T.BEHAVIORS)

    def test_defaults_zmk_falls_back_to(self):
        self.assertEqual(T.DEFAULTS["zmk,behavior-macro"], {"tap-ms": 30, "wait-ms": 15})
        self.assertEqual(T.DEFAULTS["zmk,behavior-caps-word"]["mods"], T.MODIFIERS["MOD_LSFT"])
        self.assertEqual(T.DEFAULTS["zmk,behavior-hold-tap"]["quick-tap-ms"], -1)
        self.assertEqual(T.DEFAULTS["zmk,behavior-tap-dance"]["tapping-term-ms"], 200)
        self.assertEqual(T.DEFAULTS["zmk,behavior-toggle-layer"]["toggle-mode"], "flip")


@unittest.skipUnless(os.environ.get("ZMK_DIR"), "no ZMK checkout ($ZMK_DIR)")
class Generated(unittest.TestCase):
    def test_the_module_is_what_the_generator_makes(self):
        with redirect_stderr(io.StringIO()) as err:
            code = zmk_tables_gen.main(["--check", "--zmk", os.environ["ZMK_DIR"]])
        self.assertEqual(code, 0, err.getvalue())


if __name__ == "__main__":
    unittest.main()
