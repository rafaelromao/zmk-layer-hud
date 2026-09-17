"""Round-trip tests for host/uskeys.py: what a keyboard sends to type a legend, decoded back.

These are the encoding cases the typed-keys strip gets wrong when they drift — an accented letter
typed as a dead key and a letter, a character on the Option layer, a named key. The JS half
(hud/tests/strip_test.js) takes the same events on to the strip itself.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uskeys  # noqa: E402
from hudfeed import ALT_CHARS, CHARS, NAMED  # noqa: E402


def typed(legend):
    """The characters and names the decoder reports for `legend`."""
    events = uskeys.events_for(legend)
    return None if events is None else [(m["chars"], m["name"]) for m in events]


class Characters(unittest.TestCase):
    def test_every_plain_and_shifted_character_round_trips(self):
        for usage, (plain, shifted) in CHARS.items():
            for ch in (plain, shifted):
                if ch == " ":
                    continue  # space is a named key; covered below
                self.assertEqual(typed(ch), [(ch, ch)], f"{ch!r} (usage {usage:#04x})")

    def test_every_option_layer_character_round_trips(self):
        for ch in {c for c in ALT_CHARS.values() if c}:
            if ch in uskeys.USAGE_FOR:
                continue  # reachable without Option; that spelling wins
            self.assertEqual(typed(ch), [(ch, ch)], repr(ch))


class Accents(unittest.TestCase):
    def test_accented_letters_are_a_dead_key_and_a_letter(self):
        # Two keypresses go out, one composed character comes back.
        for ch in "áàâãéêíóôõúÁÀÂÃÉÊÍÓÔÕÚ":
            self.assertEqual(len(uskeys.keystrokes_for(ch)), 4, f"{ch!r} should be two taps")
            self.assertEqual(typed(ch), [(ch, ch)], repr(ch))

    def test_cedilla_is_the_us_international_special_case(self):
        # ' + c is ç, not ć: the combining mark would give the wrong letter.
        self.assertEqual(uskeys._dead_key_pair("ç"), ("'", "c"))
        self.assertEqual(typed("ç"), [("ç", "ç")])
        self.assertEqual(typed("Ç"), [("Ç", "Ç")])

    def test_a_dead_key_alone_is_still_the_key_it_was(self):
        self.assertEqual(typed("'"), [("'", "'")])
        self.assertEqual(typed("~"), [("~", "~")])


class Named(unittest.TestCase):
    def test_every_named_key_the_decoder_knows_has_a_legend(self):
        # A named key the decoder can report but no legend spells is a key the strip would show
        # as a bare name; the tables have to stay in step.
        spelled = set(uskeys.NAME_FOR_LEGEND.values())
        missing = {n for n in NAMED.values() if n not in spelled}
        self.assertEqual(missing, {"printscreen", "scrolllock", "pause", "menu"},
                         "a named key gained or lost a legend spelling")

    def test_legends_reach_their_named_key(self):
        for legend, name in [("␣", "space"), ("↵", "return"), ("⏎", "return"), ("⎋", "escape"),
                             ("⌫", "delete"), ("⇥", "tab"), ("↹", "tab"), ("→", "right"),
                             ("⎀", "insert"), ("F5", "f5"), ("F13", "f13")]:
            self.assertEqual(typed(legend)[0][1], name, legend)

    def test_return_is_the_main_one_not_the_keypad(self):
        self.assertEqual(uskeys.USAGE_FOR_NAME["return"], 0x28)


class Corpus(unittest.TestCase):
    def test_a_legend_that_no_keyboard_can_type_is_skipped(self):
        self.assertIsNone(uskeys.keystrokes_for("vim mode"))
        self.assertIsNone(uskeys.keystrokes_for("🐳"))


if __name__ == "__main__":
    unittest.main()
