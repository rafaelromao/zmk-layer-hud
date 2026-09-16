"""Decoder tests for host/hudfeed.py. The layer vectors mirror firmware/tests/test_layer_signal.c;
keep both in sync."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hudfeed import BASE_USAGE, COMMIT_USAGE, ReportDecoder, decode_keys, decode_report  # noqa: E402

C = COMMIT_USAGE


class DecodeKeys(unittest.TestCase):
    def test_no_commit_is_not_an_announcement(self):
        self.assertIsNone(decode_keys([0xC2, 0xCE, 0, 0, 0, 0]))

    def test_commit_alone_is_the_empty_set(self):
        self.assertEqual(decode_keys([C, 0, 0, 0, 0, 0]), [])

    def test_two_layers(self):
        self.assertEqual(decode_keys([0xC2, 0xCE, C, 0, 0, 0]), [2, 14])

    def test_unrelated_usages_are_ignored(self):
        # a real key (0x04 = a), a modifier (0xE1), a layer, the commit, another key
        self.assertEqual(decode_keys([0x04, 0xE1, 0xC5, C, 0x2C, 0]), [5])

    def test_order_does_not_matter(self):
        self.assertEqual(decode_keys([C, 0xC1]), [1])

    def test_base_alone_is_layer_zero_and_not_reported(self):
        self.assertEqual(decode_keys([BASE_USAGE, C]), [])

    def test_last_representable_id(self):
        self.assertEqual(decode_keys([0xDE, C]), [30])

    def test_custom_base_and_commit(self):
        self.assertEqual(decode_keys([0xA6, 0xB0], base=0xA5, commit=0xB0), [1])


class DecodeReport(unittest.TestCase):
    def test_zmk_keyboard_report(self):
        # [report id 1, modifiers, reserved, keys...] as hidapi returns it
        self.assertEqual(decode_report([1, 0, 0, 0xC2, 0xD6, C, 0, 0, 0]), [2, 22])

    def test_other_report_ids_are_ignored(self):
        self.assertIsNone(decode_report([2, 0xCD, 0, 0, 0, 0]))  # consumer report
        self.assertIsNone(decode_report([3, 0, 0, C]))  # mouse report

    def test_short_or_empty_reports(self):
        self.assertIsNone(decode_report([]))
        self.assertIsNone(decode_report([1, 0]))

    def test_without_report_id(self):
        self.assertEqual(decode_report([0, 0, 0xC1, C], report_id=None), [1])
        self.assertIsNone(decode_report([0], report_id=None))

    def test_twelve_slot_report(self):
        keys = [0xC0 + l for l in range(1, 12)] + [C]
        self.assertEqual(decode_report([1, 0, 0] + keys), list(range(1, 12)))


def R(mods=0, *keys):
    return [1, mods, 0] + list(keys) + [0] * (12 - len(keys))


class Decoder(unittest.TestCase):
    """The report stream -> layers / key / flagsChanged messages, all from the keyboard."""

    def test_layers_only_when_changed(self):
        d = ReportDecoder()
        self.assertEqual(d.feed(R(0, 0xC2, C)), [{"kind": "layers", "ids": [2]}])
        self.assertEqual(d.feed(R(0, 0xC2, C)), [])             # heartbeat: unchanged
        self.assertEqual(d.feed(R(0, C)), [{"kind": "layers", "ids": []}])

    def test_key_press_and_release(self):
        d = ReportDecoder()
        down = d.feed(R(0, 0x04))
        self.assertEqual(len(down), 1)
        self.assertEqual((down[0]["type"], down[0]["name"], down[0]["chars"], down[0]["code"]), ("keyDown", "a", "a", 4))
        up = d.feed(R(0))
        self.assertEqual((up[0]["type"], up[0]["chars"]), ("keyUp", "a"))

    def test_shift_gives_uppercase_and_symbols(self):
        d = ReportDecoder()
        msgs = d.feed(R(0x02, 0x04))                             # left shift + a
        self.assertEqual(msgs[0]["type"], "flagsChanged")
        self.assertTrue(msgs[0]["flags"]["shift"])
        self.assertEqual((msgs[1]["type"], msgs[1]["chars"]), ("keyDown", "A"))
        d.feed(R(0x02))
        msgs = d.feed(R(0x20, 0x1E))                             # right shift + 1
        self.assertEqual(msgs[-1]["chars"], "!")

    def test_named_keys(self):
        d = ReportDecoder()
        self.assertEqual(d.feed(R(0, 0x28))[0]["name"], "return")
        d.feed(R(0))
        self.assertEqual(d.feed(R(0, 0x50))[0]["name"], "left")
        d.feed(R(0))
        self.assertEqual(d.feed(R(0, 0x68))[0]["name"], "f13")
        d.feed(R(0))
        m = d.feed(R(0, 0x2C))[0]
        self.assertEqual((m["name"], m["chars"]), ("space", " "))

    def test_announcement_usages_are_not_keys(self):
        d = ReportDecoder()
        msgs = d.feed(R(0, 0x04, 0xC2, C))                        # 'a' held while the keyboard announces
        kinds = [(m["kind"], m.get("type")) for m in msgs]
        self.assertEqual(kinds, [("layers", None), ("key", "keyDown")])
        self.assertEqual(d.feed(R(0, 0x04)), [])                 # release of the announcement: nothing

    def test_modifier_only_change(self):
        d = ReportDecoder()
        msgs = d.feed(R(0x08))                                   # cmd down
        self.assertEqual([m["type"] for m in msgs], ["flagsChanged"])
        self.assertTrue(msgs[0]["flags"]["cmd"])
        self.assertEqual(d.feed(R(0))[0]["flags"]["cmd"], False)

    def test_other_reports_ignored(self):
        d = ReportDecoder()
        self.assertEqual(d.feed([2, 0xCD, 0]), [])
        self.assertEqual(d.feed([]), [])


if __name__ == "__main__":
    unittest.main()
