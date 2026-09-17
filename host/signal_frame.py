"""The wire format between the keyboard and this host.

Mirrors firmware/src/signal_frame.h. The vectors in signal_frame_test.py are the
same bytes as the ones in firmware/tests/test_layer_signal.c, so a change on one
side that is not mirrored on the other fails both test suites.

    A5 5A  ver  kind  len  payload...  crc8

Over USB the frames arrive on a CDC-ACM serial port, which is a byte stream with
no message boundaries, so the reader has to find them: Decoder.feed() scans for
the magic, checks the CRC, and skips a byte at a time when either fails, which is
what lets it join a stream already in progress or recover from a dropped byte.
Over BLE each notification is already exactly one frame, but it carries the same
framing so this decoder handles both without caring which it is reading.
"""

from __future__ import annotations

MAGIC = b"\xa5\x5a"
VERSION = 1

KIND_LAYERS = 0x01
KIND_POSITION = 0x02
KIND_KEYS = 0x03

HEADER_LEN = 5  # magic0 magic1 version kind len
KEYS_MAX = 16
MAX_PAYLOAD = 1 + KEYS_MAX  # the keys frame is the widest: modifiers, then usages
MAX_LEN = HEADER_LEN + MAX_PAYLOAD + 1


def crc8(data) -> int:
    """CRC-8, polynomial 0x07, init 0x00 — the same few lines as zls_crc8()."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def decode_frame(frame):
    """One complete frame -> a message dict, or None when it is not valid.

    Unknown kinds decode to None rather than raising: the length field means a
    reader can skip a frame it does not understand without losing sync, so the
    firmware can add kinds without a version bump.
    """
    data = bytes(frame)
    if len(data) < HEADER_LEN + 1 or data[:2] != MAGIC or data[2] != VERSION:
        return None

    payload_len = data[4]
    if len(data) != HEADER_LEN + payload_len + 1:
        return None
    # The CRC starts at the version byte: the magic is excluded so it can be
    # verified without knowing where the frame actually began.
    if data[-1] != crc8(data[2:-1]):
        return None

    kind, payload = data[3], data[HEADER_LEN:-1]

    if kind == KIND_LAYERS and payload_len == 4:
        state = int.from_bytes(payload, "little")
        return {"kind": "layers", "ids": [i for i in range(32) if state & (1 << i)]}

    if kind == KIND_POSITION and payload_len == 2:
        return {"kind": "press" if payload[1] else "release", "pos": payload[0]}

    if kind == KIND_KEYS and payload_len >= 1:
        # The same (modifiers, usages) split_report() used to return, so the
        # decoder above this can diff snapshots exactly as it always did.
        return {"kind": "keys", "mods": payload[0], "keys": list(payload[1:])}

    return None


class Decoder:
    """Byte stream -> messages. Hand it whatever a read() returned; it keeps the
    remainder and returns the messages that completed."""

    def __init__(self, max_buffer=4096):
        self._buf = bytearray()
        self._max_buffer = max_buffer

    def feed(self, chunk):
        self._buf.extend(chunk)
        # A stream that never yields a valid frame must not grow without bound:
        # keep only what could still be the start of one.
        if len(self._buf) > self._max_buffer:
            del self._buf[: -MAX_LEN]

        out = []
        while True:
            start = self._buf.find(MAGIC)
            if start < 0:
                # Keep a trailing byte: it may be the first half of the magic.
                del self._buf[: max(0, len(self._buf) - 1)]
                break
            if start:
                del self._buf[:start]
            if len(self._buf) < HEADER_LEN + 1:
                break

            payload_len = self._buf[4]
            if payload_len > MAX_PAYLOAD:
                del self._buf[:1]  # not a real header; resync past it
                continue

            total = HEADER_LEN + payload_len + 1
            if len(self._buf) < total:
                break

            msg = decode_frame(self._buf[:total])
            if msg is None:
                del self._buf[:1]  # bad crc or unknown shape: resync
                continue

            del self._buf[:total]
            out.append(msg)

        return out
