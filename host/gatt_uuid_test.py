"""The host looks for the BLE service the firmware defines.

The UUIDs are written twice, in C in firmware/src/signal_gatt.c and in Python in
host/hudfeed.py, and nothing else joins the two: a host that asks for another service or
characteristic reads nothing at all, and says no more than that no keyboard was found. So this
reads the firmware's own source rather than a copy of its values."""

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from hudfeed import BLE_SERVICE_UUID, BLE_SIGNAL_UUID  # noqa: E402

GATT_C = os.path.join(HERE, os.pardir, "firmware", "src", "signal_gatt.c")

HEX = r"0x([0-9a-fA-F]+)[uUlL]*"
# #define ZLS_BT_SERVICE_UUID BT_UUID_128_ENCODE(0xd1f0a7c2, 0x6b3e, 0x4f8a, 0x9c21, 0x5e7b4a0d9f31)
ENCODE = re.compile(r"^\s*#\s*define\s+(ZLS_BT_\w+_UUID)\s+BT_UUID_128_ENCODE\(\s*"
                    + r"\s*,\s*".join([HEX] * 5) + r"\s*\)", re.M)
READABLE = re.compile(r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b")
WIDTHS = (8, 4, 4, 4, 12)


def readable(groups):
    """BT_UUID_128_ENCODE's five arguments -> the UUID as bleak spells it. Zephyr's macro takes
    the readable form's groups in order: the hyphens become commas and each group gains 0x."""
    values = [int(g, 16) for g in groups]
    for group, value, width in zip(groups, values, WIDTHS):
        if value >= 16 ** width:
            raise ValueError(f"0x{group} does not fit in {width} hex digits")
    return "-".join(f"{value:0{width}x}" for value, width in zip(values, WIDTHS))


def firmware_source():
    with open(GATT_C, encoding="utf-8") as f:
        return f.read()


class Readable(unittest.TestCase):
    def test_zephyrs_own_example(self):
        # The example in Zephyr's include/zephyr/bluetooth/uuid.h, so the order is Zephyr's.
        self.assertEqual(readable(("6E400001", "B5A3", "F393", "E0A9", "E50E24DCCA9E")),
                         "6e400001-b5a3-f393-e0a9-e50e24dcca9e")

    def test_a_group_too_wide_is_refused(self):
        with self.assertRaises(ValueError):
            readable(("1d1f0a7c2", "6b3e", "4f8a", "9c21", "5e7b4a0d9f31"))


class FirmwareAndHost(unittest.TestCase):
    def setUp(self):
        self.src = firmware_source()
        self.fw = {m.group(1): readable(m.groups()[1:]) for m in ENCODE.finditer(self.src)}

    def test_both_macros_are_read(self):
        # A pattern that matched nothing would leave every check below with nothing to check.
        self.assertEqual(sorted(self.fw), ["ZLS_BT_SERVICE_UUID", "ZLS_BT_SIGNAL_UUID"])

    def test_the_host_scans_for_the_firmwares_service(self):
        self.assertEqual(BLE_SERVICE_UUID, self.fw["ZLS_BT_SERVICE_UUID"])

    def test_the_host_subscribes_to_the_firmwares_characteristic(self):
        self.assertEqual(BLE_SIGNAL_UUID, self.fw["ZLS_BT_SIGNAL_UUID"])

    def test_the_comment_says_what_the_macros_define(self):
        # The readable forms above the macros are the ones a reader copies, so they cannot drift
        # from the macros either.
        written = {uuid.lower() for uuid in READABLE.findall(self.src)}
        self.assertEqual(written, set(self.fw.values()))


if __name__ == "__main__":
    unittest.main()
