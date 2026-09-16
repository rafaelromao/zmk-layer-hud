"""Decoder tests for host/hudfeed.py. The vectors mirror firmware/tests/test_layer_signal.c;
keep both in sync."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hudfeed import BASE_USAGE, COMMIT_USAGE, LayerReader, decode_keys, decode_report  # noqa: E402

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


class ReaderDedup(unittest.TestCase):
    """The reader only reports a set when it differs from the last announced one."""

    def test_unchanged_set_is_suppressed(self):
        seen = []
        r = LayerReader(seen.append, log=lambda *a: None)

        class Dev:
            def __init__(self, reports):
                self.reports = list(reports)

            def read(self, n, timeout_ms=0):
                if not self.reports:
                    r.stop()
                    return []
                return self.reports.pop(0)

            def close(self):
                pass

        r._read_loop("p", Dev([
            [1, 0, 0, 0xC2, C, 0, 0, 0, 0],   # {2}
            [1, 0, 0, 0x04, 0, 0, 0, 0, 0],   # typing: ignored
            [1, 0, 0, 0xC2, C, 0, 0, 0, 0],   # {2} again (heartbeat): suppressed
            [1, 0, 0, C, 0, 0, 0, 0, 0],      # {}
        ]), "test")
        self.assertEqual(seen, [[2], []])


if __name__ == "__main__":
    unittest.main()
