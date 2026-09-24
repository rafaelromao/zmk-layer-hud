"""Tests for host/hudpoke.py: what `zmk-layer-hud poke` puts on the socket.

Only the messages; the socket itself is websockets' business, and the pages' reading of these
messages is hud/tests/words_test.js's.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hudpoke  # noqa: E402


def sent(argv):
    args = hudpoke.parse_args(argv)
    return [msg for _, msg in hudpoke.says_combos(hudpoke.messages(args), args.combos)]


class Combos(unittest.TestCase):
    def test_typing_says_no_combos_by_default(self):
        keys = [m for m in sent(["--type", "zebra"]) if m["kind"] == "key"]
        self.assertTrue(keys)
        self.assertTrue(all(m["combos"] is False for m in keys))

    def test_combos_says_so_on_every_key(self):
        keys = [m for m in sent(["--type", "zebra", "--combos"]) if m["kind"] == "key"]
        self.assertTrue(keys)
        self.assertTrue(all(m["combos"] is True for m in keys))

    def test_a_legend_is_typing_too(self):
        keys = [m for m in sent(["--legend", "á"]) if m["kind"] == "key"]
        self.assertTrue(keys)
        self.assertTrue(all(m["combos"] is False for m in keys))

    def test_only_keys_carry_it(self):
        for m in sent(["--layers", "13", "--press", "16", "--combos"]):
            self.assertNotIn("combos", m)

    def test_a_raw_message_that_says_keeps_its_word(self):
        raw = [(0, {"kind": "key", "type": "keyDown", "chars": "z", "combos": True})]
        self.assertIs(next(hudpoke.says_combos(iter(raw), False))[1]["combos"], True)


if __name__ == "__main__":
    unittest.main()
