"""hud/keynames.js is the key vocabulary as host/keynames_gen.py writes it: when host/keynames.py
changes, `make keynames` has to run, or the pages spell and type keys differently from the feed."""

import json
import os
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import keynames  # noqa: E402
import keynames_gen  # noqa: E402

NODE = shutil.which("node")

# Legends the page and the tools must read alike: every spelling, written as drawings write them,
# and some that name no key.
CASES = sorted({s for s in keynames.SPELLINGS} | {s.upper() for s in keynames.SPELLINGS} |
               {s.title() for s in keynames.SPELLINGS} |
               {"Page Down", "Pg Dn", "Caps Lock", " Tab ", "B Space", "Print Scrn", "Tab Prev", "vim", "e",
                "E", "", "⇥⇥", "F25", "f 5", "Delete", "DEL"})


class Generated(unittest.TestCase):
    def test_the_page_has_the_vocabulary(self):
        with open(keynames_gen.OUT, encoding="utf-8") as f:
            self.assertEqual(f.read(), keynames_gen.generate(), "hud/keynames.js is behind: make keynames")

    def test_pairs_keep_their_shift(self):
        t = keynames_gen.tables()
        self.assertEqual(t["ALT_CHARS"]["30,0"], "¡")
        self.assertEqual(t["ALTGR_CHARS"]["54,1"], "Ç")
        self.assertEqual(t["DEAD_KEY_SPECIAL"]["'c"], "ç")

    def test_the_modifiers_keep_the_order_a_chord_is_drawn_in(self):
        self.assertEqual(list(keynames_gen.tables()["MOD_GLYPHS"]), ["cmd", "ctrl", "alt", "shift"])

    @unittest.skipUnless(NODE, "node is not installed")
    def test_the_page_reads_a_legend_as_the_tools_do(self):
        # The one rule twice, once per language: name_of here, nameOf in the page.
        script = ("const T = require(process.argv[1]); const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
                  "process.stdout.write(JSON.stringify(cases.map(T.nameOf)));")
        out = subprocess.run([NODE, "-e", script, keynames_gen.OUT], input=json.dumps(CASES), capture_output=True,
                             text=True, check=True)
        page = dict(zip(CASES, json.loads(out.stdout)))
        self.assertEqual(page, {c: keynames.name_of(c) for c in CASES})


if __name__ == "__main__":
    unittest.main()
