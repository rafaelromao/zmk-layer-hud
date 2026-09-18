"""Decoder tests for host/hudfeed.py: the keyboard's signal messages -> the pages' messages.

The wire format itself is host/signal_frame.py's business and is tested in signal_frame_test.py;
what is tested here is what the HUD gets told. Stream ties the two together.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import signal_frame  # noqa: E402
from hudfeed import INJECTABLE, SignalDecoder, Stream, split_report  # noqa: E402


def K(mods=0, *keys):
    """A `keys` message: the report snapshot the firmware sends."""
    return {"kind": "keys", "mods": mods, "keys": list(keys)}


def L(*ids):
    return {"kind": "layers", "ids": list(ids)}


class Layers(unittest.TestCase):
    def test_only_when_changed(self):
        d = SignalDecoder()
        self.assertEqual(d.feed(L(2)), [{"kind": "layers", "ids": [2]}])
        self.assertEqual(d.feed(L(2)), [])                      # the heartbeat: unchanged
        self.assertEqual(d.feed(L()), [{"kind": "layers", "ids": []}])

    def test_layer_zero_is_never_drawn(self):
        # The firmware sends the bitmap as the keymap holds it; the default layer is always on and
        # the page's stack does not include it.
        d = SignalDecoder()
        self.assertEqual(d.feed(L(0, 22)), [{"kind": "layers", "ids": [22]}])
        self.assertEqual(d.feed(L(22)), [])                     # the same set, spelled differently

    def test_several_layers_keep_their_order(self):
        d = SignalDecoder()
        self.assertEqual(d.feed(L(2, 14, 22)), [{"kind": "layers", "ids": [2, 14, 22]}])


class Positions(unittest.TestCase):
    """Firmware `positions;`: each press and release names the key that moved."""

    def test_press_and_release_pass_through(self):
        d = SignalDecoder()
        self.assertEqual(d.feed({"kind": "press", "pos": 13}), [{"kind": "press", "pos": 13}])
        self.assertEqual(d.feed({"kind": "release", "pos": 13}), [{"kind": "release", "pos": 13}])

    def test_a_position_is_not_swallowed_by_a_repeat(self):
        # Unlike the report encoding, the same key pressed twice is two messages.
        d = SignalDecoder()
        self.assertEqual(d.feed({"kind": "press", "pos": 0}), [{"kind": "press", "pos": 0}])
        self.assertEqual(d.feed({"kind": "press", "pos": 0}), [{"kind": "press", "pos": 0}])


class Keys(unittest.TestCase):
    def test_key_press_and_release(self):
        d = SignalDecoder()
        down = d.feed(K(0, 0x04))
        self.assertEqual(len(down), 1)
        self.assertEqual((down[0]["type"], down[0]["name"], down[0]["chars"], down[0]["code"]), ("keyDown", "a", "a", 4))
        up = d.feed(K(0))
        self.assertEqual((up[0]["type"], up[0]["chars"]), ("keyUp", "a"))

    def test_shift_gives_uppercase_and_symbols(self):
        d = SignalDecoder()
        msgs = d.feed(K(0x02, 0x04))                             # left shift + a
        self.assertEqual(msgs[0]["type"], "flagsChanged")
        self.assertTrue(msgs[0]["flags"]["shift"])
        self.assertEqual((msgs[1]["type"], msgs[1]["chars"]), ("keyDown", "A"))
        d.feed(K(0x02))
        msgs = d.feed(K(0x20, 0x1E))                             # right shift + 1
        self.assertEqual(msgs[-1]["chars"], "!")

    def test_named_keys(self):
        d = SignalDecoder()
        self.assertEqual(d.feed(K(0, 0x28))[0]["name"], "return")
        d.feed(K(0))
        self.assertEqual(d.feed(K(0, 0x50))[0]["name"], "left")
        d.feed(K(0))
        self.assertEqual(d.feed(K(0, 0x68))[0]["name"], "f13")
        d.feed(K(0))
        m = d.feed(K(0, 0x2C))[0]
        self.assertEqual((m["name"], m["chars"]), ("space", " "))

    def test_modifier_only_change(self):
        d = SignalDecoder()
        msgs = d.feed(K(0x08))                                   # cmd down
        self.assertEqual([m["type"] for m in msgs], ["flagsChanged"])
        self.assertTrue(msgs[0]["flags"]["cmd"])
        self.assertEqual(d.feed(K(0))[0]["flags"]["cmd"], False)

    def test_chord_reports_each_key_once(self):
        d = SignalDecoder()
        self.assertEqual([m["chars"] for m in d.feed(K(0, 0x04, 0x05))], ["a", "b"])
        self.assertEqual(d.feed(K(0, 0x04, 0x05)), [])           # resent unchanged: nothing new
        self.assertEqual([m["chars"] for m in d.feed(K(0, 0x05))], ["a"])  # a released

    def test_a_kind_this_version_does_not_know_is_ignored(self):
        # The firmware can add kinds without the host having to understand them.
        self.assertEqual(SignalDecoder().feed({"kind": "battery", "pct": 80}), [])


class DeadKeys(unittest.TestCase):
    """Accent macros type a US-International dead key then the letter, back to back."""

    def test_acute_a(self):
        d = SignalDecoder()
        self.assertEqual(d.feed(K(0, 0x34), now_ms=1000), [])          # ' held back
        self.assertEqual(d.feed(K(0), now_ms=1001), [])                 # its release: nothing yet...
        msgs = d.feed(K(0, 0x04), now_ms=1002)                          # a
        downs = [m for m in msgs if m.get("type") == "keyDown"]
        self.assertEqual(len(downs), 1)
        self.assertEqual((downs[0]["chars"], downs[0]["name"], downs[0]["composed"]), ("á", "á", [0x34, 0x04]))

    def test_tilde_and_umlaut_use_shift(self):
        d = SignalDecoder()
        d.feed(K(0x02, 0x35), now_ms=0)      # shift + ` = ~
        d.feed(K(0x02), now_ms=1)
        msgs = d.feed(K(0, 0x11), now_ms=2)  # n
        self.assertEqual([m["chars"] for m in msgs if m.get("type") == "keyDown"], ["ñ"])
        d.feed(K(0), now_ms=3)
        d.feed(K(0x02, 0x34), now_ms=10)     # shift + ' = "
        d.feed(K(0x02), now_ms=11)
        msgs = d.feed(K(0x02, 0x18), now_ms=12)  # shift + u
        self.assertEqual([m["chars"] for m in msgs if m.get("type") == "keyDown"], ["Ü"])

    def test_cedilla_is_the_us_international_special_case(self):
        d = SignalDecoder()
        d.feed(K(0, 0x34), now_ms=0)         # '
        d.feed(K(0), now_ms=1)
        msgs = d.feed(K(0, 0x06), now_ms=2)  # c -> ç, not ć
        self.assertEqual([m["chars"] for m in msgs if m.get("type") == "keyDown"], ["ç"])
        d.feed(K(0), now_ms=3)
        d.feed(K(0, 0x34), now_ms=10)
        d.feed(K(0), now_ms=11)
        msgs = d.feed(K(0x02, 0x06), now_ms=12)  # shift + c -> Ç
        self.assertEqual([m["chars"] for m in msgs if m.get("type") == "keyDown"], ["Ç"])

    def test_plain_apostrophe_is_released_on_timeout(self):
        d = SignalDecoder()
        self.assertEqual(d.feed(K(0, 0x34), now_ms=0), [])
        self.assertEqual(d.feed(K(0), now_ms=5), [])                     # release held with it
        self.assertEqual(d.flush(30), [])                                # not yet
        out = d.flush(61)
        self.assertEqual([(m["type"], m["chars"]) for m in out], [("keyDown", "'"), ("keyUp", "'")])

    def test_apostrophe_then_slow_letter_stays_two_keys(self):
        d = SignalDecoder()
        d.feed(K(0, 0x34), now_ms=0)
        d.feed(K(0), now_ms=5)
        msgs = d.feed(K(0, 0x04), now_ms=200)                             # too late to compose
        self.assertEqual([m["chars"] for m in msgs if m.get("type") == "keyDown"], ["'", "a"])

    def test_apostrophe_then_non_letter(self):
        d = SignalDecoder()
        d.feed(K(0, 0x34), now_ms=0)
        d.feed(K(0), now_ms=1)
        msgs = d.feed(K(0, 0x2C), now_ms=2)                               # space
        self.assertEqual([m["chars"] for m in msgs if m.get("type") == "keyDown"], ["'", " "])

    def test_option_layer_characters(self):
        d = SignalDecoder()
        msgs = d.feed(K(0x02 | 0x04, 0x1F), now_ms=0)                    # shift + alt + 2 = €
        self.assertEqual([m["chars"] for m in msgs if m.get("type") == "keyDown"], ["€"])
        d.feed(K(0), now_ms=1)
        msgs = d.feed(K(0x04, 0x2D), now_ms=2)                           # alt + - = en dash
        self.assertEqual([m["chars"] for m in msgs if m.get("type") == "keyDown"], ["–"])

    def test_compose_can_be_disabled(self):
        d = SignalDecoder(compose=False)
        msgs = d.feed(K(0, 0x34), now_ms=0)
        self.assertEqual([m["chars"] for m in msgs], ["'"])


class StreamTest(unittest.TestCase):
    """Bytes off a carrier, through the frame decoder, to the pages' messages."""

    def setUp(self):
        self.out = []
        self.stream = Stream(self.out.append, "Diamond")

    def frame(self, kind, payload):
        body = bytes([1, kind, len(payload)]) + bytes(payload)
        return b"\xa5\x5a" + body + bytes([signal_frame.crc8(body)])

    def test_a_frame_becomes_a_message_tagged_with_its_keyboard(self):
        self.stream.feed(self.frame(signal_frame.KIND_LAYERS, [0x00, 0x00, 0x40, 0x00]), 0)
        self.assertEqual(self.out, [{"kind": "layers", "ids": [22], "device": "Diamond"}])
        self.assertEqual(self.stream.frames_seen, 1)

    def test_a_frame_split_across_two_reads(self):
        data = self.frame(signal_frame.KIND_LAYERS, [0x04, 0x00, 0x00, 0x00])
        self.stream.feed(data[:3], 0)
        self.assertEqual(self.out, [])
        self.stream.feed(data[3:], 1)
        self.assertEqual([m["ids"] for m in self.out], [[2]])

    def test_a_keys_message_from_the_hid_reader_takes_the_same_path(self):
        # The carrier does not carry these -- HidKeysReader reads them off the keyboard's HID
        # reports and hands them straight to message(), which is why one decoder serves both.
        self.stream.message({"kind": "keys", "mods": 0, "keys": [0x04]}, 0)
        self.assertEqual([(m["type"], m["chars"], m["device"]) for m in self.out],
                         [("keyDown", "a", "Diamond")])

    def test_noise_produces_nothing_and_counts_nothing(self):
        # What a port that is not ours looks like; the reader gives up on it after probe_s.
        self.stream.feed(b"*** Booting Zephyr OS ***\r\n", 0)
        self.assertEqual((self.out, self.stream.frames_seen), ([], 0))

    def test_resync_makes_the_next_heartbeat_speak_again(self):
        # Something else drew on the pages, so the keyboard agreeing with itself is no longer
        # silence worth keeping: the next heartbeat has to restate what is really held.
        layers = self.frame(signal_frame.KIND_LAYERS, [0x02, 0x00, 0x00, 0x00])
        self.stream.feed(layers, 0)
        self.stream.feed(layers, 1)                      # heartbeat, unchanged
        self.assertEqual(len(self.out), 1)
        self.stream.resync()
        self.stream.feed(layers, 2)                      # same heartbeat, now a change again
        self.assertEqual([m["ids"] for m in self.out], [[1], [1]])

    def test_closing_releases_what_was_held(self):
        self.stream.message({"kind": "keys", "mods": 0, "keys": [0x04]}, 0)
        self.out.clear()
        self.stream.close()
        self.assertEqual([(m["type"], m["chars"], m["device"]) for m in self.out],
                         [("keyUp", "a", "Diamond")])


class Reports(unittest.TestCase):
    """The HID reports the host reads for the strip, split into what the decoder wants."""

    def test_zmk_keyboard_report(self):
        # [report id 1, modifiers, reserved, keys...] as hidapi returns it
        self.assertEqual(split_report([1, 0x02, 0, 0x04, 0x05, 0, 0, 0, 0]), (0x02, b"\x04\x05\x00\x00\x00\x00"))

    def test_other_report_ids_are_not_ours(self):
        self.assertIsNone(split_report([2, 0xCD, 0, 0, 0, 0]))  # consumer
        self.assertIsNone(split_report([3, 0, 0, 0x04]))        # mouse

    def test_short_or_empty(self):
        self.assertIsNone(split_report([]))
        self.assertIsNone(split_report([1, 0]))

    def test_without_report_id(self):
        self.assertEqual(split_report([0x02, 0, 0x04], report_id=None), (0x02, b"\x04"))
        self.assertIsNone(split_report([0], report_id=None))

    def test_a_report_becomes_the_same_message_the_firmware_would_send(self):
        # The point of the split: both sources hand SignalDecoder the identical shape, so the
        # layout tables and dead-key composition below cannot tell them apart.
        mods, keys = split_report([1, 0x02, 0, 0x04, 0, 0, 0, 0, 0])
        d = SignalDecoder()
        msgs = d.feed({"kind": "keys", "mods": mods, "keys": list(keys)})
        self.assertEqual([m["chars"] for m in msgs if m.get("type") == "keyDown"], ["A"])


class Injectable(unittest.TestCase):
    """What a WebSocket client is allowed to put on the pages (host/hudpoke.py)."""

    def test_the_cosmetic_kinds_are_accepted(self):
        for kind in ("layers", "press", "release", "key", "device"):
            self.assertIn(kind, INJECTABLE)

    def test_the_keymap_is_not(self):
        # It is large, it is built from files the feed already watches, and a page given a wrong
        # one has no way back to the right one.
        self.assertNotIn("keymap", INJECTABLE)

    def test_close_is_not_injectable_either(self):
        # It is handled before this list and exits the process; it must not be broadcast.
        self.assertNotIn("close", INJECTABLE)


if __name__ == "__main__":
    unittest.main()
