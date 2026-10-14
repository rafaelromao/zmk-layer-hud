"""The key vocabulary (host/keynames.py): what its records say, as the feed, the pages and the tools
read it. The test oracle (host/ways.py) and the page share these spellings, so they are pinned here
rather than by the sweeps."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import keynames  # noqa: E402
from keynames import GLYPHS, MOD_BITS, MOD_GLYPHS, NAMED, name_of, spellings  # noqa: E402


class Records(unittest.TestCase):
    def test_a_named_key_is_named_by_each_of_its_usages(self):
        self.assertEqual(NAMED[0x28], "return")
        self.assertEqual(NAMED[0x58], "return")     # the keypad's Enter
        self.assertEqual(NAMED[0x2A], "delete")     # Backspace
        self.assertEqual(NAMED[0x4C], "forwarddelete")
        self.assertEqual(NAMED[0x9B], "cancel")
        self.assertEqual(NAMED[0x3E], "f5")
        self.assertEqual(NAMED[0x73], "f24")

    def test_no_usage_names_two_keys(self):
        usages = [u for _, us, _, _, _ in keynames.NAMED_KEYS for u in us]
        self.assertEqual(len(usages), len(set(usages)))

    def test_glyphs(self):
        self.assertEqual([GLYPHS[n] for n in ("space", "return", "delete", "forwarddelete", "tab", "f5")],
                         ["␣", "↵", "⌫", "⌦", "⇥", "F5"])
        self.assertNotIn("printscreen", GLYPHS)      # no glyph: the strip shows its name

    def test_modifiers_collapse_left_and_right(self):
        self.assertEqual(MOD_BITS, {0x01: "ctrl", 0x02: "shift", 0x04: "alt", 0x08: "cmd",
                                    0x10: "ctrl", 0x20: "shift", 0x40: "alt", 0x80: "cmd"})
        self.assertEqual("".join(MOD_GLYPHS.values()), "⌘⌃⌥⇧")   # the order a chord is drawn

    def test_every_named_key_with_a_glyph_has_a_spelling(self):
        # A named key the decoder can report but no legend spells is a key the strip would show
        # as a bare name; the tables have to stay in step.
        spelled = set(keynames.SPELLINGS.values())
        self.assertEqual({n for n in NAMED.values() if n not in spelled}, {"printscreen", "scrolllock", "pause", "menu"},
                         "a named key gained or lost a legend spelling")


class Spellings(unittest.TestCase):
    def test_glyphs_and_words_reach_their_named_key(self):
        for legend, name in [("␣", "space"), ("↵", "return"), ("⏎", "return"), ("⎋", "escape"), ("⌫", "delete"),
                             ("⇥", "tab"), ("↹", "tab"), ("→", "right"), ("⎀", "insert"), ("F5", "f5"),
                             ("F13", "f13"), ("cancel", "cancel")]:
            self.assertEqual(name_of(legend), name, legend)

    def test_a_word_reaches_its_named_key_in_any_case_and_spacing(self):
        # keymap-drawer's parse spells named keys as words (example-3x5: Tab, BSpace, Page Down),
        # and a drawing by hand in capitals (SPACE, RET, BSPC): one rule reads them all.
        for legend, name in [("Tab", "tab"), ("Esc", "escape"), ("BSpace", "delete"), ("BSPC", "delete"),
                             ("SPACE", "space"), ("RET", "return"), ("Enter", "return"), ("Page Down", "pagedown"),
                             ("Pg Dn", "pagedown"), ("Caps Lock", "capslock"), ("Ins", "insert"), ("f5", "f5"),
                             ("Cancel", "cancel")]:
            self.assertEqual(name_of(legend), name, legend)

    def test_a_drawings_delete_deletes_forwards(self):
        # The feed's `delete` is Backspace; a drawing's Delete is ZMK's DEL, the forward one.
        self.assertEqual(name_of("Delete"), "forwarddelete")
        self.assertEqual(name_of("Del"), "forwarddelete")
        self.assertEqual(name_of("⌫"), "delete")

    def test_a_legend_that_names_no_key_is_none(self):
        for legend in ("Print Scrn", "vim", "e", "", "Tab Prev", None, 5):
            self.assertIsNone(name_of(legend), legend)

    def test_every_spelling_of_a_key(self):
        self.assertTrue({"⇥", "↹", "tab"} <= set(spellings("tab")))
        self.assertEqual(spellings("printscreen"), [])
        self.assertEqual(spellings(None), [])


if __name__ == "__main__":
    unittest.main()
