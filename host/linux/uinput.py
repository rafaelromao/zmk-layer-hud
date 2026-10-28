"""The touch keyboard's keys on Linux: a virtual keyboard made through /dev/uinput.

A uinput device is a keyboard as far as the compositor knows: it has its keys pressed and released,
and the compositor applies the user's layout to them (Hyprland's kb_layout, a US-International dead
key, AltGr), exactly as it does for the physical keyboard. Modifiers stay down while held, and a key
held down repeats the way any key does -- Wayland clients repeat it themselves -- so there is no
EV_REP here, and host/inject.py's watchdog is what keeps a silent page from holding one forever.

The usage -> keycode table is the kernel's own (drivers/hid/hid-input.c, hid_keyboard[], v6.12),
written out for the keyboard page up to the right GUI, and a few consumer keys from the same file's
consumer mappings; the ioctls and structures are include/uapi/linux/uinput.h's.

Writing to /dev/uinput needs permission: the `uinput` module loaded and the device node writable by
the user at the seat. `zmk-layer-hud typing setup` installs the rule that does it
(contrib/udev/61-zmk-layer-hud-uinput.rules) after asking; probe() says how things stand.

Stdlib only, and Python 3.9: the panel runs under the system interpreter.
"""

import errno
import os
import struct
import time

try:
    import fcntl
except ImportError:   # not Linux: the module still imports, for its tables and its tests
    fcntl = None

PATH = "/dev/uinput"
NAME = "zmk-layer-hud virtual keyboard"
UI_SET_EVBIT = 0x40045564      # _IOW('U', 100, int)
UI_SET_KEYBIT = 0x40045565     # _IOW('U', 101, int)
UI_DEV_SETUP = 0x405C5503      # _IOW('U', 3, struct uinput_setup), 92 bytes
UI_DEV_CREATE = 0x5501         # _IO('U', 1)
UI_DEV_DESTROY = 0x5502        # _IO('U', 2)
EV_SYN, EV_KEY, SYN_REPORT = 0x00, 0x01, 0
BUS_VIRTUAL = 0x06
SETUP = struct.Struct("HHHH80sI")    # struct input_id {bustype, vendor, product, version}; name[80]; ff_effects_max
EVENT = struct.Struct("@llHHi")      # struct input_event: struct timeval, type, code, value
WARMUP_S = 0.25                      # a new device is given this long for the compositor to take it in

# Keyboard page usage -> Linux keycode (hid-input.c hid_keyboard[]; KEY_UNKNOWN and none left out).
HID_KEYBOARD = {
    0x04: 30, 0x05: 48, 0x06: 46, 0x07: 32, 0x08: 18, 0x09: 33, 0x0A: 34, 0x0B: 35,
    0x0C: 23, 0x0D: 36, 0x0E: 37, 0x0F: 38, 0x10: 50, 0x11: 49, 0x12: 24, 0x13: 25,
    0x14: 16, 0x15: 19, 0x16: 31, 0x17: 20, 0x18: 22, 0x19: 47, 0x1A: 17, 0x1B: 45,
    0x1C: 21, 0x1D: 44, 0x1E: 2, 0x1F: 3, 0x20: 4, 0x21: 5, 0x22: 6, 0x23: 7,
    0x24: 8, 0x25: 9, 0x26: 10, 0x27: 11, 0x28: 28, 0x29: 1, 0x2A: 14, 0x2B: 15,
    0x2C: 57, 0x2D: 12, 0x2E: 13, 0x2F: 26, 0x30: 27, 0x31: 43, 0x32: 43, 0x33: 39,
    0x34: 40, 0x35: 41, 0x36: 51, 0x37: 52, 0x38: 53, 0x39: 58, 0x3A: 59, 0x3B: 60,
    0x3C: 61, 0x3D: 62, 0x3E: 63, 0x3F: 64, 0x40: 65, 0x41: 66, 0x42: 67, 0x43: 68,
    0x44: 87, 0x45: 88, 0x46: 99, 0x47: 70, 0x48: 119, 0x49: 110, 0x4A: 102, 0x4B: 104,
    0x4C: 111, 0x4D: 107, 0x4E: 109, 0x4F: 106, 0x50: 105, 0x51: 108, 0x52: 103, 0x53: 69,
    0x54: 98, 0x55: 55, 0x56: 74, 0x57: 78, 0x58: 96, 0x59: 79, 0x5A: 80, 0x5B: 81,
    0x5C: 75, 0x5D: 76, 0x5E: 77, 0x5F: 71, 0x60: 72, 0x61: 73, 0x62: 82, 0x63: 83,
    0x64: 86, 0x65: 127, 0x66: 116, 0x67: 117, 0x68: 183, 0x69: 184, 0x6A: 185, 0x6B: 186,
    0x6C: 187, 0x6D: 188, 0x6E: 189, 0x6F: 190, 0x70: 191, 0x71: 192, 0x72: 193, 0x73: 194,
    0x74: 134, 0x75: 138, 0x76: 130, 0x77: 132, 0x78: 128, 0x79: 129, 0x7A: 131, 0x7B: 137,
    0x7C: 133, 0x7D: 135, 0x7E: 136, 0x7F: 113, 0x80: 115, 0x81: 114, 0x85: 121, 0x87: 89,
    0x88: 93, 0x89: 124, 0x8A: 92, 0x8B: 94, 0x8C: 95, 0x90: 122, 0x91: 123, 0x92: 90,
    0x93: 91, 0x94: 85, 0x9C: 111, 0xB6: 179, 0xB7: 180, 0xD8: 111, 0xE0: 29, 0xE1: 42,
    0xE2: 56, 0xE3: 125, 0xE4: 97, 0xE5: 54, 0xE6: 100, 0xE7: 126,
}
# Consumer page usage -> Linux keycode (hid-input.c's consumer mappings): the media keys a keymap has.
CONSUMER = {0x6F: 225, 0x70: 224, 0xB5: 163, 0xB6: 165, 0xB7: 166, 0xB8: 161, 0xCD: 164, 0xE2: 113,
            0xE9: 115, 0xEA: 114}


def keycode(page, usage):
    return (HID_KEYBOARD if page == 7 else CONSUMER if page == 12 else {}).get(usage)


def probe(path=PATH, exists=os.path.exists, access=os.access):
    """"ok" (this user can make a keyboard), "denied" (the node is there, not for this user), or
    "missing" (no node: the uinput module is not loaded)."""
    if not exists(path):
        return "missing"
    return "ok" if access(path, os.W_OK) else "denied"


def state_of(error):
    """What an OSError from opening the node means for the page's banner."""
    if error.errno in (errno.EACCES, errno.EPERM):
        return "denied"
    if error.errno in (errno.ENOENT, errno.ENODEV, errno.ENXIO):
        return "missing"
    return "error"


class Uinput:
    """One virtual keyboard: made by open(), its keys pressed by send(), gone with close() -- or
    with the process, the kernel then releasing whatever it held."""

    def __init__(self, name=NAME, path=PATH, opener=os.open, ioctl=None, write=os.write, close=os.close,
                 clock=time.monotonic):
        self.name, self.path = name, path
        self.opener, self.write, self.closer, self.clock = opener, write, close, clock
        self.ioctl = ioctl or (fcntl.ioctl if fcntl else None)
        self.fd = None
        self.made = None
        self.waiting = []     # frames sent while the compositor may not have the device yet

    def open(self):
        """Make the device. OSError when it cannot be: state_of() says why."""
        if EVENT.size != 24:
            raise OSError(errno.ENOTSUP, "a 64-bit process is needed to write input events")
        fd = self.opener(self.path, os.O_WRONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
        try:
            self.ioctl(fd, UI_SET_EVBIT, EV_KEY)
            for code in sorted(set(HID_KEYBOARD.values()) | set(CONSUMER.values())):
                self.ioctl(fd, UI_SET_KEYBIT, code)
            self.ioctl(fd, UI_DEV_SETUP, SETUP.pack(BUS_VIRTUAL, 0, 0, 1, self.name.encode()[:79], 0))
            self.ioctl(fd, UI_DEV_CREATE)
        except OSError:
            self.closer(fd)
            raise
        self.fd, self.made = fd, self.clock()
        return self

    def frame(self, changes):
        """The bytes for these changes: one event per key, then the report that ends the frame. A
        zero time is the kernel's to fill in."""
        events = [EVENT.pack(0, 0, EV_KEY, code, 1 if down else 0)
                  for code, down in ((keycode(page, usage), down) for page, usage, down in changes) if code]
        return b"".join(events) + EVENT.pack(0, 0, EV_SYN, SYN_REPORT, 0) if events else b""

    def send(self, changes):
        data = self.frame(changes)
        if not data or self.fd is None:
            return
        if self.clock() - self.made < WARMUP_S:
            self.waiting.append(data)
            return
        self._flush()
        self.write(self.fd, data)

    def tick(self):
        if self.waiting and self.fd is not None and self.clock() - self.made >= WARMUP_S:
            self._flush()

    def _flush(self):
        while self.waiting:
            self.write(self.fd, self.waiting.pop(0))

    def close(self):
        if self.fd is None:
            return
        fd, self.fd = self.fd, None
        try:
            self.ioctl(fd, UI_DEV_DESTROY)
        except OSError:
            pass
        self.closer(fd)
