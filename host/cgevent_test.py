"""host/macos/cgevent.py: keyboard events made, flagged and posted as CoreGraphics wants them, and a
held key repeated -- against a fake CoreGraphics, so it runs anywhere."""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "macos"))

import cgevent  # noqa: E402


class FakeCG:
    def __init__(self, granted=True):
        self.granted, self.events, self.posted, self.released, self.suppression = granted, {}, [], [], None
        self.next = 1000

    def CGEventSourceCreate(self, state):
        assert state == cgevent.HID_SYSTEM_STATE
        return 1

    def CGEventSourceSetLocalEventsSuppressionInterval(self, src, seconds):
        self.suppression = seconds

    def CGEventCreateKeyboardEvent(self, src, kvk, down):
        self.next += 1
        self.events[self.next] = {"kvk": kvk, "down": down, "flags": None, "fields": {}}
        return self.next

    def CGEventSetFlags(self, ev, flags):
        self.events[ev]["flags"] = flags

    def CGEventSetIntegerValueField(self, ev, field, value):
        self.events[ev]["fields"][field] = value

    def CGEventPost(self, tap, ev):
        assert tap == cgevent.HID_EVENT_TAP
        self.posted.append(self.events[ev])

    def CFRelease(self, ref):
        self.released.append(ref)

    def CGPreflightPostEventAccess(self):
        return self.granted

    def CGRequestPostEventAccess(self):
        return self.granted

    def CGDisplayScreenSize(self, display):
        return cgevent.CGSize(302.6, 196.7) if display == 1 else cgevent.CGSize(0, 0)


class Scheduler:
    def __init__(self):
        self.pending = []

    def schedule(self, seconds, fn):
        h = [seconds, fn]
        self.pending.append(h)
        return h

    def cancel(self, h):
        if h in self.pending:
            self.pending.remove(h)

    def run_next(self):
        h = self.pending.pop(0)
        h[1]()
        return h[0]


class Flags(unittest.TestCase):
    def test_each_modifier_and_its_side(self):
        self.assertEqual(cgevent.SHIFT | 0x2, cgevent.cg_flags(0x02))
        self.assertEqual(cgevent.SHIFT | 0x4, cgevent.cg_flags(0x20))
        self.assertEqual(cgevent.COMMAND | 0x8 | cgevent.ALTERNATE | 0x40, cgevent.cg_flags(0x08 | 0x40))
        self.assertEqual(cgevent.CONTROL | 0x2000, cgevent.cg_flags(0x10))
        self.assertEqual(0, cgevent.cg_flags(0))


class Backend(unittest.TestCase):
    def test_a_shifted_key(self):
        cg = FakeCG()
        b = cgevent.CGBackend(cg=cg)
        self.assertEqual(0.0, cg.suppression)    # the real keyboard and mouse are not held off
        b.send([(7, 0xE1, True), (7, 0x04, True)])
        b.send([(7, 0x04, False), (7, 0xE1, False)])
        self.assertEqual([(0x38, True, cgevent.SHIFT | 2), (0x00, True, cgevent.SHIFT | 2),
                          (0x00, False, cgevent.SHIFT | 2), (0x38, False, 0)],
                         [(e["kvk"], e["down"], e["flags"]) for e in cg.posted])
        self.assertTrue(all(e["fields"][cgevent.FIELD_USER_DATA] == cgevent.MARKER for e in cg.posted))
        self.assertEqual(4, len(cg.released))

    def test_media_keys_are_left_alone(self):
        cg = FakeCG()
        cgevent.CGBackend(cg=cg).send([(12, 0xE9, True)])
        self.assertEqual([], cg.posted)

    def test_a_held_key_repeats_and_stops(self):
        cg, s = FakeCG(), Scheduler()
        b = cgevent.CGBackend(cg=cg, schedule=s.schedule, cancel=s.cancel, delay=0.4, interval=0.03)
        b.send([(7, 0x04, True)])
        self.assertEqual(0.4, s.run_next())
        self.assertEqual(0.03, s.run_next())
        repeats = [e for e in cg.posted if e["fields"].get(cgevent.FIELD_AUTOREPEAT) == 1]
        self.assertEqual(2, len(repeats))
        self.assertTrue(all(e["kvk"] == 0x00 and e["down"] for e in repeats))
        b.send([(7, 0x04, False)])
        self.assertEqual([], s.pending)

    def test_a_modifier_does_not_repeat(self):
        cg, s = FakeCG(), Scheduler()
        cgevent.CGBackend(cg=cg, schedule=s.schedule, cancel=s.cancel).send([(7, 0xE1, True)])
        self.assertEqual([], s.pending)

    def test_access(self):
        self.assertTrue(cgevent.access(FakeCG(True)))
        self.assertFalse(cgevent.request_access(FakeCG(False)))

    def test_a_screens_size(self):
        # What the touch keyboard's keys are drawn their real size with; a display that does not say, nothing.
        self.assertEqual((302.6, 196.7), cgevent.screen_mm(1, FakeCG()))
        self.assertEqual((0, 0), cgevent.screen_mm(2, FakeCG()))


class Table(unittest.TestCase):
    # Apple's hid_usb_2_adb_keymap rows, by usage, and the kVK_* names HIToolbox's Events.h gives them.
    ANCHORS = {0x04: 0x00,   # kVK_ANSI_A
               0x28: 0x24,   # kVK_Return
               0x29: 0x35,   # kVK_Escape
               0x2A: 0x33,   # kVK_Delete
               0x2C: 0x31,   # kVK_Space
               0x35: 0x32,   # kVK_ANSI_Grave
               0x64: 0x0A,   # kVK_ISO_Section
               0x49: 0x72,   # kVK_Help (Insert)
               0x4F: 0x7C, 0x50: 0x7B, 0x51: 0x7D, 0x52: 0x7E,   # right, left, down, up
               0xE0: 0x3B, 0xE1: 0x38, 0xE2: 0x3A, 0xE3: 0x37,   # control, shift, option, command
               0xE4: 0x3E, 0xE5: 0x3C, 0xE6: 0x3D, 0xE7: 0x36}

    def test_anchors(self):
        for usage, kvk in self.ANCHORS.items():
            self.assertEqual(kvk, cgevent.USB_TO_KVK[usage], hex(usage))

    def test_every_letter_and_digit(self):
        self.assertTrue(all(u in cgevent.USB_TO_KVK for u in range(0x04, 0x28)))


if __name__ == "__main__":
    unittest.main()
