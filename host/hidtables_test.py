"""hud/hidtables.js is hudfeed's tables as host/hidtables_gen.py writes them: when hudfeed's change,
`make hidtables` has to run, or the page's translator types differently from the feed's decoder."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hidtables_gen  # noqa: E402


class Generated(unittest.TestCase):
    def test_the_page_has_hudfeeds_tables(self):
        with open(hidtables_gen.OUT, encoding="utf-8") as f:
            self.assertEqual(f.read(), hidtables_gen.generate(), "hud/hidtables.js is behind: make hidtables")

    def test_pairs_keep_their_shift(self):
        t = hidtables_gen.tables()
        self.assertEqual(t["ALT_CHARS"]["30,0"], "¡")
        self.assertEqual(t["ALTGR_CHARS"]["54,1"], "Ç")
        self.assertEqual(t["DEAD_KEY_SPECIAL"]["'c"], "ç")


if __name__ == "__main__":
    unittest.main()
