"""host/linux/uinput.py: the virtual keyboard's device made as uinput.h says, and its keys written as
input events -- against a fake kernel, so it runs anywhere. The kernel's table is held to a few of
hid-input.c's rows (v6.12) and the evdev codes they name in input-event-codes.h."""

import errno
import os
import struct
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "linux"))

import uinput  # noqa: E402


class Kernel:
    def __init__(self, fail_open=None):
        self.fail_open, self.calls, self.written, self.closed = fail_open, [], [], []

    def open(self, path, flags):
        if self.fail_open:
            raise OSError(self.fail_open, os.strerror(self.fail_open))
        self.calls.append(("open", path, flags))
        return 7

    def ioctl(self, fd, req, arg=0):
        self.calls.append(("ioctl", req, arg))

    def write(self, fd, data):
        self.written.append(data)
        return len(data)

    def close(self, fd):
        self.closed.append(fd)


class Clock:
    def __init__(self):
        self.now = 10.0

    def __call__(self):
        return self.now


def device(kernel, clock=None):
    return uinput.Uinput(opener=kernel.open, ioctl=kernel.ioctl, write=kernel.write, close=kernel.close,
                         clock=clock or Clock())


class Device(unittest.TestCase):
    def test_the_structures_are_the_kernels_sizes(self):
        self.assertEqual(92, uinput.SETUP.size)
        self.assertEqual(24, uinput.EVENT.size)

    def test_the_ioctl_numbers(self):
        def ioc(direction, nr, size):
            return (direction << 30) | (size << 16) | (ord("U") << 8) | nr
        self.assertEqual(ioc(1, 100, 4), uinput.UI_SET_EVBIT)
        self.assertEqual(ioc(1, 101, 4), uinput.UI_SET_KEYBIT)
        self.assertEqual(ioc(1, 3, 92), uinput.UI_DEV_SETUP)
        self.assertEqual(ioc(0, 1, 0), uinput.UI_DEV_CREATE)
        self.assertEqual(ioc(0, 2, 0), uinput.UI_DEV_DESTROY)

    def test_it_is_made_with_every_key_it_can_press(self):
        k = Kernel()
        device(k).open()
        reqs = [c[1] for c in k.calls if c[0] == "ioctl"]
        self.assertEqual(uinput.UI_SET_EVBIT, reqs[0])
        keybits = {c[2] for c in k.calls if c[0] == "ioctl" and c[1] == uinput.UI_SET_KEYBIT}
        self.assertEqual(set(uinput.HID_KEYBOARD.values()) | set(uinput.CONSUMER.values()), keybits)
        setup = next(c[2] for c in k.calls if c[0] == "ioctl" and c[1] == uinput.UI_DEV_SETUP)
        bus, vendor, product, version, name, _ = uinput.SETUP.unpack(setup)
        self.assertEqual((uinput.BUS_VIRTUAL, b"zmk-layer-hud virtual keyboard"), (bus, name.rstrip(b"\0")))
        self.assertEqual(uinput.UI_DEV_CREATE, reqs[-1])
        self.assertTrue(k.calls[0][2] & os.O_WRONLY)

    def test_keys_are_written_as_one_frame_after_the_warmup(self):
        k, clock = Kernel(), Clock()
        d = device(k, clock).open()
        d.send([(7, 0xE1, True), (7, 0x04, True)])
        self.assertEqual([], k.written)               # the compositor may not have the device yet
        clock.now += uinput.WARMUP_S
        d.tick()
        d.send([(7, 0x04, False)])
        events = [struct.unpack("@llHHi", w[i:i + 24]) for w in k.written for i in range(0, len(w), 24)]
        self.assertEqual([(0, 0, 1, 42, 1), (0, 0, 1, 30, 1), (0, 0, 0, 0, 0),
                          (0, 0, 1, 30, 0), (0, 0, 0, 0, 0)], events)

    def test_a_usage_it_has_no_code_for_writes_nothing(self):
        k, clock = Kernel(), Clock()
        d = device(k, clock).open()
        clock.now += 1
        d.send([(7, 0x00, True), (1, 0x81, True)])
        self.assertEqual([], k.written)

    def test_closing_destroys_it(self):
        k = Kernel()
        d = device(k).open()
        d.close()
        self.assertEqual(("ioctl", uinput.UI_DEV_DESTROY, 0), k.calls[-1])
        self.assertEqual([7], k.closed)

    def test_why_it_cannot_be_made(self):
        for err, said in ((errno.EACCES, "denied"), (errno.EPERM, "denied"), (errno.ENOENT, "missing"),
                          (errno.ENODEV, "missing"), (errno.EIO, "error")):
            with self.assertRaises(OSError) as e:
                device(Kernel(fail_open=err)).open()
            self.assertEqual(said, uinput.state_of(e.exception))

    def test_probe(self):
        self.assertEqual("missing", uinput.probe(exists=lambda p: False))
        self.assertEqual("denied", uinput.probe(exists=lambda p: True, access=lambda p, m: False))
        self.assertEqual("ok", uinput.probe(exists=lambda p: True, access=lambda p, m: True))


class Table(unittest.TestCase):
    # hid-input.c hid_keyboard[] rows, by usage: what input-event-codes.h names each code.
    ANCHORS = {0x04: 30,    # KEY_A
               0x28: 28,    # KEY_ENTER
               0x29: 1,     # KEY_ESC
               0x2A: 14,    # KEY_BACKSPACE
               0x2C: 57,    # KEY_SPACE
               0x31: 43, 0x32: 43,   # KEY_BACKSLASH, and Non-US # on it too
               0x35: 41,    # KEY_GRAVE
               0x4F: 106, 0x50: 105, 0x51: 108, 0x52: 103,   # RIGHT, LEFT, DOWN, UP
               0x64: 86,    # KEY_102ND
               0x66: 116,   # KEY_POWER
               0x68: 183, 0x73: 194,   # KEY_F13, KEY_F24
               0xE0: 29, 0xE1: 42, 0xE2: 56, 0xE3: 125, 0xE4: 97, 0xE5: 54, 0xE6: 100, 0xE7: 126}

    def test_anchors(self):
        for usage, code in self.ANCHORS.items():
            self.assertEqual(code, uinput.keycode(7, usage), hex(usage))

    def test_media_keys(self):
        self.assertEqual((115, 114, 113, 164), tuple(uinput.keycode(12, u) for u in (0xE9, 0xEA, 0xE2, 0xCD)))

    def test_every_letter_and_digit(self):
        self.assertTrue(all(uinput.keycode(7, u) for u in range(0x04, 0x28)))


if __name__ == "__main__":
    unittest.main()
