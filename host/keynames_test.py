"""The key vocabulary (host/keynames.py): what its records say, as the feed, the pages and the tools
read it. The test oracle (host/ways.py) and the page share these spellings, so they are pinned here
rather than by the sweeps."""

import json
import os
import re
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import keynames  # noqa: E402
import keymap  # noqa: E402
from keynames import CONTROL_CHARS, GLYPHS, MOD_BITS, MOD_GLYPHS, NAMED, name_of, spellings  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node")


def read(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as f:
        return f.read()


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



class Adapters(unittest.TestCase):
    """The adapters keep their own words for keys, each keyed by the key name: the browser's
    (site/demo.js), a text field's (hud/hidtext.js TEXT_KEYS, and the Android keyboard's codes for
    those), keymap-drawer's glyph ids (host/keymap.py GLYPHS). Each must name keys the vocabulary
    knows, as the vocabulary names them."""

    @unittest.skipUnless(NODE, "node is not installed")
    def test_the_browsers_named_keys(self):
        out = subprocess.run([NODE, "-e", "process.stdout.write(JSON.stringify(require(process.argv[1]).NAMED))",
                              os.path.join(REPO, "site", "demo.js")], capture_output=True, text=True, check=True)
        control = {NAMED[u]: c for u, c in CONTROL_CHARS.items()}
        for key, (name, chars) in json.loads(out.stdout).items():
            self.assertIn(name, NAMED.values(), key)
            self.assertEqual(chars, control.get(name, ""), key)

    def test_a_text_fields_named_keys(self):
        src = read("hud", "hidtext.js")
        table = re.search(r"const TEXT_KEYS = \{(.*?)\};", src, re.S).group(1)
        text_keys = dict(re.findall(r'"(\w+)": "(\w+)"', table))
        self.assertTrue(text_keys)
        for name in text_keys:
            self.assertIn(name, NAMED.values())
        self.assertIn('TEXT_KEYS["f" + i] = "f" + i', src)    # f1..f24, which the vocabulary names
        # The Android keyboard's codes are keyed by the text field's words.
        kotlin = read("apps", "android", "sink", "src", "main", "kotlin", "io", "github", "rafaelromao", "zmkhud",
                      "sink", "ImeSink.kt")
        android = re.findall(r'put\("(\w+)", KeyEvent', kotlin)
        self.assertTrue(android)
        self.assertLessEqual(set(android), set(text_keys.values()))

    def test_keymap_drawers_glyphs_read_as_their_keys(self):
        for glyph_id, name in [("mdi:keyboard-space", "space"), ("mdi:keyboard-return", "return"),
                               ("mdi:keyboard-tab", "tab"), ("mdi:keyboard-esc", "escape"),
                               ("mdi:backspace-outline", "delete"), ("mdi:backspace-reverse-outline", "forwarddelete"),
                               ("mdi:arrow-left", "left"), ("mdi:arrow-right", "right"), ("mdi:arrow-up", "up"),
                               ("mdi:arrow-down", "down"), ("mdi:format-horizontal-align-left", "home"),
                               ("mdi:format-horizontal-align-right", "end"), ("mdi:home", "home"),
                               ("mdi:page-first", "home"), ("mdi:page-last", "end"),
                               ("mdi:chevron-double-up", "pageup"), ("mdi:chevron-double-down", "pagedown"),
                               ("mdi:arrow-up-bold-outline", "pageup"), ("mdi:arrow-down-bold-outline", "pagedown"),
                               ("mdi:keyboard-caps", "capslock"), ("mdi:apple-keyboard-caps", "capslock")]:
            self.assertEqual(name_of(keymap.GLYPHS[glyph_id]), name, glyph_id)
        for glyph_id, flag in [("mdi:apple-keyboard-shift", "shift"), ("mdi:apple-keyboard-control", "ctrl"),
                               ("mdi:apple-keyboard-option", "alt"), ("mdi:apple-keyboard-command", "cmd")]:
            self.assertEqual(keymap.GLYPHS[glyph_id], MOD_GLYPHS[flag], glyph_id)


if __name__ == "__main__":
    unittest.main()
