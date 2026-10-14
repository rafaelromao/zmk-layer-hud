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
from hudfeed import ALT_CHARS, ALTGR_CHARS, CHARS, NAMED, RALT, SignalDecoder  # noqa: E402


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

    def test_on_a_host_whose_right_alt_is_altgr_cedilla_is_altgr_comma(self):
        # Omarchy (feed.altgr): the keymap sends RA(COMMA) for ç, and that is what it decodes to.
        for ch in ALTGR_CHARS.values():
            snaps = uskeys.keystrokes_for(ch, altgr=True)
            self.assertTrue(snaps[0]["mods"] & RALT, ch)
            self.assertEqual([ch], [m["chars"] for m in uskeys.events_for(ch, altgr=True)])
        self.assertEqual(0x36, uskeys.keystrokes_for("ç", altgr=True)[0]["keys"][0])

    def test_without_it_the_right_alt_is_option(self):
        # A Mac: the same report is Option+, -- what a keymap pinned to the other OS shows there.
        decoder = SignalDecoder()
        out = decoder.feed({"kind": "keys", "mods": RALT, "keys": [0x36]}, 0)
        self.assertEqual(["≤"], [m["chars"] for m in out if m["type"] == "keyDown"])

    def test_a_dead_key_alone_is_still_the_key_it_was(self):
        self.assertEqual(typed("'"), [("'", "'")])
        self.assertEqual(typed("~"), [("~", "~")])


class Named(unittest.TestCase):
    def test_every_named_key_the_decoder_knows_has_a_legend(self):
        # A named key the decoder can report but no legend spells is a key the strip would show
        # as a bare name; the tables have to stay in step.
        spelled = set(uskeys.SPELLINGS.values())
        missing = {n for n in NAMED.values() if n not in spelled}
        self.assertEqual(missing, {"printscreen", "scrolllock", "pause", "menu"},
                         "a named key gained or lost a legend spelling")

    def test_legends_reach_their_named_key(self):
        for legend, name in [("␣", "space"), ("↵", "return"), ("⏎", "return"), ("⎋", "escape"),
                             ("⌫", "delete"), ("⇥", "tab"), ("↹", "tab"), ("→", "right"),
                             ("⎀", "insert"), ("F5", "f5"), ("F13", "f13"), ("cancel", "cancel")]:
            self.assertEqual(typed(legend)[0][1], name, legend)

    def test_a_word_reaches_its_named_key_in_any_case_and_spacing(self):
        # keymap-drawer's parse spells named keys as words (example-3x5: Tab, BSpace, Page Down),
        # and a drawing by hand in capitals (SPACE, RET, BSPC): one rule reads them all.
        for legend, name in [("Tab", "tab"), ("Esc", "escape"), ("BSpace", "delete"), ("BSPC", "delete"),
                             ("SPACE", "space"), ("RET", "return"), ("Enter", "return"), ("Page Down", "pagedown"),
                             ("Pg Dn", "pagedown"), ("Caps Lock", "capslock"), ("Ins", "insert"), ("f5", "f5"),
                             ("Cancel", "cancel")]:
            self.assertEqual(uskeys.name_of(legend), name, legend)
            self.assertEqual(typed(legend)[0][1], name, legend)

    def test_a_drawings_delete_deletes_forwards(self):
        # The feed's `delete` is Backspace; a drawing's Delete is ZMK's DEL, the forward one.
        self.assertEqual(uskeys.name_of("Delete"), "forwarddelete")
        self.assertEqual(uskeys.name_of("Del"), "forwarddelete")
        self.assertEqual(uskeys.name_of("⌫"), "delete")

    def test_a_word_that_names_no_key_is_none(self):
        for legend in ("Print Scrn", "vim", "e", "", "Tab Prev"):
            self.assertIsNone(uskeys.name_of(legend), legend)

    def test_every_spelling_of_a_key_is_accepted_for_it(self):
        self.assertTrue({"⇥", "↹", "tab"} <= set(uskeys.spellings("tab")))
        self.assertEqual(uskeys.spellings("printscreen"), [])

    def test_cancel_is_named(self):
        # K_CANCEL (0x9B) is what a cancel combo sends on Linux, where the keyboard's positions may
        # not reach the HUD to put the combo's legend on it: the feed has to name it itself.
        out = SignalDecoder().feed({"kind": "keys", "mods": 0, "keys": [0x9B]}, 0)
        self.assertEqual(["cancel"], [m["name"] for m in out if m["type"] == "keyDown"])

    def test_return_is_the_main_one_not_the_keypad(self):
        self.assertEqual(uskeys.USAGE_FOR_NAME["return"], 0x28)


class Corpus(unittest.TestCase):
    def test_a_legend_that_no_keyboard_can_type_is_skipped(self):
        self.assertIsNone(uskeys.keystrokes_for("vim mode"))
        self.assertIsNone(uskeys.keystrokes_for("🐳"))


if __name__ == "__main__":
    unittest.main()
