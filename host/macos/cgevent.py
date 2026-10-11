"""The touch keyboard's keys on macOS: CoreGraphics keyboard events, posted where the HID system
posts the keyboard's own.

A key event carries a virtual keycode, and macOS types it through the current input source as it
types a key of the physical keyboard: the user's layout, Option's characters and dead keys
included. The modifiers are set on every event rather than left to the system, which keeps no
modifier state for posted events: the flags say exactly what the touch keyboard holds, the device
bits too (left or right). The source is the HID system's, with local events never suppressed, so
the physical keyboard and the mouse keep working while the touch keyboard types.

A posted key held down does not repeat (only the HID system repeats the keyboard's keys), so
Repeater does, at the user's own key-repeat delay and rate (NSEvent.keyRepeatDelay/Interval, which
the panel reads and passes in).

Posting events needs the user's leave: Privacy & Security > Accessibility, asked for once with
CGRequestPostEventAccess (macOS 10.15+). The grant belongs to the app macOS holds responsible for the
panel -- the ZMK Layer HUD login item, or the terminal it was started from.

The usage -> virtual keycode table is Apple's own (IOHIDFamily, Cosmo_USB2ADB.c, hid_usb_2_adb_keymap),
the keyboard page up to the right GUI; the constants are CoreGraphics' (CGEventTypes.h) and
IOKit's (IOLLEvent.h). CoreGraphics is reached through ctypes, loaded on first use, as menubar.py
reaches Carbon: nothing here imports a framework binding.
"""

import ctypes
import ctypes.util

KEY_PAGE = 7
HID_SYSTEM_STATE = 1           # kCGEventSourceStateHIDSystemState
HID_EVENT_TAP = 0              # kCGHIDEventTap
FIELD_AUTOREPEAT = 8           # kCGKeyboardEventAutorepeat
FIELD_USER_DATA = 42           # kCGEventSourceUserData
MARKER = 0x7A6D6B68            # "zmkh": this HUD's events, should anything want to tell them apart

# Keyboard page usage -> macOS virtual keycode (kVK_*), from hid_usb_2_adb_keymap.
USB_TO_KVK = {
    0x04: 0x00, 0x05: 0x0B, 0x06: 0x08, 0x07: 0x02, 0x08: 0x0E, 0x09: 0x03, 0x0A: 0x05, 0x0B: 0x04,
    0x0C: 0x22, 0x0D: 0x26, 0x0E: 0x28, 0x0F: 0x25, 0x10: 0x2E, 0x11: 0x2D, 0x12: 0x1F, 0x13: 0x23,
    0x14: 0x0C, 0x15: 0x0F, 0x16: 0x01, 0x17: 0x11, 0x18: 0x20, 0x19: 0x09, 0x1A: 0x0D, 0x1B: 0x07,
    0x1C: 0x10, 0x1D: 0x06, 0x1E: 0x12, 0x1F: 0x13, 0x20: 0x14, 0x21: 0x15, 0x22: 0x17, 0x23: 0x16,
    0x24: 0x1A, 0x25: 0x1C, 0x26: 0x19, 0x27: 0x1D, 0x28: 0x24, 0x29: 0x35, 0x2A: 0x33, 0x2B: 0x30,
    0x2C: 0x31, 0x2D: 0x1B, 0x2E: 0x18, 0x2F: 0x21, 0x30: 0x1E, 0x31: 0x2A, 0x32: 0x2A, 0x33: 0x29,
    0x34: 0x27, 0x35: 0x32, 0x36: 0x2B, 0x37: 0x2F, 0x38: 0x2C, 0x39: 0x39, 0x3A: 0x7A, 0x3B: 0x78,
    0x3C: 0x63, 0x3D: 0x76, 0x3E: 0x60, 0x3F: 0x61, 0x40: 0x62, 0x41: 0x64, 0x42: 0x65, 0x43: 0x6D,
    0x44: 0x67, 0x45: 0x6F, 0x46: 0x69, 0x47: 0x6B, 0x48: 0x71, 0x49: 0x72, 0x4A: 0x73, 0x4B: 0x74,
    0x4C: 0x75, 0x4D: 0x77, 0x4E: 0x79, 0x4F: 0x7C, 0x50: 0x7B, 0x51: 0x7D, 0x52: 0x7E, 0x53: 0x47,
    0x54: 0x4B, 0x55: 0x43, 0x56: 0x4E, 0x57: 0x45, 0x58: 0x4C, 0x59: 0x53, 0x5A: 0x54, 0x5B: 0x55,
    0x5C: 0x56, 0x5D: 0x57, 0x5E: 0x58, 0x5F: 0x59, 0x60: 0x5B, 0x61: 0x5C, 0x62: 0x52, 0x63: 0x41,
    0x64: 0x0A, 0x65: 0x6E, 0x66: 0x7F, 0x67: 0x51, 0x68: 0x69, 0x69: 0x6B, 0x6A: 0x71, 0x6B: 0x6A,
    0x6C: 0x40, 0x6D: 0x4F, 0x6E: 0x50, 0x6F: 0x5A, 0x75: 0x72, 0x7F: 0x4A, 0x80: 0x48, 0x81: 0x49,
    0x85: 0x5F, 0x87: 0x5E, 0x89: 0x5D, 0x90: 0x68, 0x91: 0x66, 0xE0: 0x3B, 0xE1: 0x38, 0xE2: 0x3A,
    0xE3: 0x37, 0xE4: 0x3E, 0xE5: 0x3C, 0xE6: 0x3D, 0xE7: 0x36,
}
# HID modifier bit -> the event flags it sets: the modifier's, and the device bit for its side.
SHIFT, CONTROL, ALTERNATE, COMMAND = 0x00020000, 0x00040000, 0x00080000, 0x00100000
FLAGS = {0: CONTROL | 0x0001, 1: SHIFT | 0x0002, 2: ALTERNATE | 0x0020, 3: COMMAND | 0x0008,
         4: CONTROL | 0x2000, 5: SHIFT | 0x0004, 6: ALTERNATE | 0x0040, 7: COMMAND | 0x0010}


def cg_flags(mods):
    """The HID modifier byte as CGEventFlags."""
    out = 0
    for bit, flags in FLAGS.items():
        if mods & (1 << bit):
            out |= flags
    return out


class CoreGraphics:
    """The dozen CoreGraphics calls, typed, loaded on first use."""

    def __init__(self):
        cg = ctypes.CDLL(ctypes.util.find_library("CoreGraphics"))
        cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
        p, b = ctypes.c_void_p, ctypes.c_bool
        sig = [("CGEventSourceCreate", p, [ctypes.c_int32]),
               ("CGEventSourceSetLocalEventsSuppressionInterval", None, [p, ctypes.c_double]),
               ("CGEventCreateKeyboardEvent", p, [p, ctypes.c_uint16, b]),
               ("CGEventSetFlags", None, [p, ctypes.c_uint64]),
               ("CGEventSetIntegerValueField", None, [p, ctypes.c_uint32, ctypes.c_int64]),
               ("CGEventPost", None, [ctypes.c_uint32, p]),
               ("CGPreflightPostEventAccess", b, []),
               ("CGRequestPostEventAccess", b, [])]
        for name, res, args in sig:
            fn = getattr(cg, name)
            fn.restype, fn.argtypes = res, args
            setattr(self, name, fn)
        self.CFRelease = cf.CFRelease
        self.CFRelease.restype, self.CFRelease.argtypes = None, [p]


_cg = None


def core_graphics():
    global _cg
    if _cg is None:
        _cg = CoreGraphics()
    return _cg


def access(cg=None):
    """Whether this process may post events."""
    return bool((cg or core_graphics()).CGPreflightPostEventAccess())


def request_access(cg=None):
    """Ask for it: macOS shows its prompt once, and the setting is in Privacy & Security >
    Accessibility from then on. True when it is already granted."""
    return bool((cg or core_graphics()).CGRequestPostEventAccess())


class Repeater:
    """A held key, typed again at the user's key-repeat rate: after `delay` seconds, then every
    `interval`. Only the last key that went down repeats, as on a keyboard; any change stops it.
    `schedule(seconds, fn)` returns a handle `cancel(handle)` takes -- the panel's run loop."""

    def __init__(self, post, schedule, cancel, delay=0.5, interval=0.05):
        self.post, self.schedule, self.cancel = post, schedule, cancel
        self.delay, self.interval = delay, interval
        self.handle, self.kvk = None, None

    def start(self, kvk):
        self.stop()
        self.kvk = kvk
        self.handle = self.schedule(self.delay, self._again)

    def stop(self):
        if self.handle is not None:
            self.cancel(self.handle)
        self.handle, self.kvk = None, None

    def _again(self):
        if self.kvk is None:
            return
        self.post(self.kvk, True, repeat=True)
        self.handle = self.schedule(self.interval, self._again)


class CGBackend:
    """host/inject.py's backend on macOS: each change a keyboard event at the HID event tap."""

    def __init__(self, cg=None, schedule=None, cancel=None, delay=0.5, interval=0.05):
        self.cg = cg or core_graphics()
        self.source = self.cg.CGEventSourceCreate(HID_SYSTEM_STATE)
        if self.source:
            # The physical keyboard and mouse are not held off after each posted key.
            self.cg.CGEventSourceSetLocalEventsSuppressionInterval(self.source, 0.0)
        self.mods = 0
        self.repeater = Repeater(self.post, schedule, cancel, delay, interval) if schedule else None

    def post(self, kvk, down, repeat=False):
        ev = self.cg.CGEventCreateKeyboardEvent(self.source, kvk, down)
        if not ev:
            return
        try:
            self.cg.CGEventSetFlags(ev, cg_flags(self.mods))
            self.cg.CGEventSetIntegerValueField(ev, FIELD_USER_DATA, MARKER)
            if repeat:
                self.cg.CGEventSetIntegerValueField(ev, FIELD_AUTOREPEAT, 1)
            self.cg.CGEventPost(HID_EVENT_TAP, ev)
        finally:
            self.cg.CFRelease(ev)

    def send(self, changes):
        if self.repeater:
            self.repeater.stop()
        last_down = None
        for page, usage, down in changes:
            kvk = USB_TO_KVK.get(usage) if page == KEY_PAGE else None
            if kvk is None:
                continue        # media keys are system events on macOS, not key events
            if 0xE0 <= usage <= 0xE7:
                bit = 1 << (usage - 0xE0)
                self.mods = self.mods | bit if down else self.mods & ~bit
            elif down:
                last_down = kvk
            self.post(kvk, down)
        if self.repeater and last_down is not None:
            self.repeater.start(last_down)

    def close(self):
        if self.repeater:
            self.repeater.stop()
        if self.source:
            self.cg.CFRelease(self.source)
            self.source = None
