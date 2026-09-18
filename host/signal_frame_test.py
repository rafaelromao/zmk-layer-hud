"""Decoder tests for host/signal_frame.py. The vectors mirror
firmware/tests/test_layer_signal.c; keep both in sync."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from signal_frame import (  # noqa: E402
    KIND_LAYERS,
    KIND_POSITION,
    UNKNOWN,
    Decoder,
    crc8,
    decode_frame,
)


def frame(kind, payload):
    body = bytes([1, kind, len(payload)]) + bytes(payload)
    return b"\xa5\x5a" + body + bytes([crc8(body)])


LAYERS_1_22 = frame(KIND_LAYERS, [0x01, 0x00, 0x40, 0x00])  # layers 0 and 22


class Crc(unittest.TestCase):
    def test_matches_the_firmware_vector(self):
        # Same bytes the C test asserts, so the two implementations agree.
        self.assertEqual(crc8([0x01, 0x01, 0x04, 0x01, 0x00, 0x40, 0x00]),
                         LAYERS_1_22[-1])


class DecodeFrame(unittest.TestCase):
    def test_layers_bitmap(self):
        self.assertEqual(decode_frame(LAYERS_1_22), {"kind": "layers", "ids": [0, 22]})

    def test_no_layers_is_a_value(self):
        self.assertEqual(decode_frame(frame(KIND_LAYERS, [0, 0, 0, 0])),
                         {"kind": "layers", "ids": []})

    def test_position_press_and_release(self):
        self.assertEqual(decode_frame(frame(KIND_POSITION, [34, 1])),
                         {"kind": "press", "pos": 34})
        self.assertEqual(decode_frame(frame(KIND_POSITION, [34, 0])),
                         {"kind": "release", "pos": 34})

    def test_bad_crc_is_rejected(self):
        bad = bytearray(LAYERS_1_22)
        bad[-1] ^= 0x01
        self.assertIsNone(decode_frame(bytes(bad)))

    def test_flipped_payload_bit_is_rejected(self):
        bad = bytearray(LAYERS_1_22)
        bad[6] ^= 0x01
        self.assertIsNone(decode_frame(bytes(bad)))

    def test_a_kind_this_version_does_not_know(self):
        # Valid frame, unrecognised kind -- an older firmware's keys snapshot, say. It decodes to
        # UNKNOWN rather than None, so the stream skips it by its length instead of resynchronising
        # through its payload.
        self.assertIs(decode_frame(frame(0x7F, [1, 2])), UNKNOWN)
        self.assertIs(decode_frame(frame(0x03, [0x02, 0x04, 0x05])), UNKNOWN)

    def test_wrong_version_is_rejected(self):
        bad = bytearray(LAYERS_1_22)
        bad[2] = 2
        self.assertIsNone(decode_frame(bytes(bad)))


class StreamDecoder(unittest.TestCase):
    def test_whole_frame(self):
        self.assertEqual(Decoder().feed(LAYERS_1_22), [{"kind": "layers", "ids": [0, 22]}])

    def test_split_across_reads(self):
        d = Decoder()
        self.assertEqual(d.feed(LAYERS_1_22[:4]), [])
        self.assertEqual(d.feed(LAYERS_1_22[4:]), [{"kind": "layers", "ids": [0, 22]}])

    def test_two_frames_in_one_read(self):
        d = Decoder()
        self.assertEqual(
            d.feed(LAYERS_1_22 + frame(KIND_POSITION, [7, 1])),
            [{"kind": "layers", "ids": [0, 22]}, {"kind": "press", "pos": 7}],
        )

    def test_joins_a_stream_in_progress(self):
        # Half a frame, then a whole one: the reader that starts late still syncs.
        d = Decoder()
        self.assertEqual(d.feed(LAYERS_1_22[3:] + LAYERS_1_22),
                         [{"kind": "layers", "ids": [0, 22]}])

    def test_recovers_after_a_corrupt_frame(self):
        bad = bytearray(LAYERS_1_22)
        bad[-1] ^= 0xFF
        d = Decoder()
        self.assertEqual(d.feed(bytes(bad) + LAYERS_1_22),
                         [{"kind": "layers", "ids": [0, 22]}])

    def test_garbage_does_not_grow_the_buffer(self):
        d = Decoder(max_buffer=64)
        self.assertEqual(d.feed(b"\x00" * 4096), [])
        self.assertEqual(d.feed(LAYERS_1_22), [{"kind": "layers", "ids": [0, 22]}])

    def test_an_unknown_frame_is_skipped_whole(self):
        # The one that matters in practice: a keyboard still sending keys frames must not stop the
        # layers frame after it from being read.
        d = Decoder()
        stale = frame(0x03, [0x02] + list(range(16)))
        self.assertEqual(d.feed(stale + LAYERS_1_22), [{"kind": "layers", "ids": [0, 22]}])

    def test_payload_length_beyond_the_maximum_resyncs(self):
        d = Decoder()
        self.assertEqual(d.feed(b"\xa5\x5a\x01\x01\xfe" + LAYERS_1_22),
                         [{"kind": "layers", "ids": [0, 22]}])


if __name__ == "__main__":
    unittest.main()
