#!/usr/bin/env python3
"""zmk-layer-hud's menubar icon on macOS: a process of its own, so it stays when the HUD quits,
the way Omarchy's bar icon does (host/linux/omarchy).

The keyboard symbol has a WPM to its left while the HUD runs, shown or hidden -- the live one, or
the session's average or top, as its menu picked -- and is dimmed while the HUD is hidden or not
running. A click shows or hides the HUD, or starts it; a right-click offers the three WPMs, each
with its number now, then the same, Quit HUD, and Remove Icon. What it shows comes from $STATE/panel.json, which the
running panel keeps (host/panelstate.py), and what it does goes through the command line, like
any other caller.

It also holds the global shortcuts (host/shortcuts.py): Ctrl+Alt+L shows or hides the HUD, and
Ctrl+Alt+Cmd+L starts or stops it, unless the config's `shortcuts:` says other keys. They are
Carbon hotkeys, which need no Accessibility or Input Monitoring grant, and they go with the icon.
The right-click menu shows them beside their items.

host/macos/start.sh starts it with the HUD, unless `zmk-layer-hud menubar disable` said not to.
"""

import ctypes
import fcntl
import os
import subprocess
import sys
from pathlib import Path

try:
    import objc
    from AppKit import (NSApp, NSApplication, NSApplicationActivationPolicyAccessory, NSBezierPath, NSColor,
                        NSControlStateValueOff, NSControlStateValueOn,
                        NSCompositingOperationClear, NSCompositingOperationSourceOver, NSEventModifierFlagCommand,
                        NSEventModifierFlagControl, NSEventModifierFlagOption, NSEventModifierFlagShift,
                        NSEventMaskLeftMouseUp, NSEventMaskRightMouseUp, NSEventTypeRightMouseUp, NSGraphicsContext,
                        NSImage, NSImageRight, NSMenu, NSMenuItem, NSStatusBar, NSVariableStatusItemLength)
    from Foundation import NSObject, NSTimer
    from PyObjCTools import AppHelper
except ImportError:
    sys.exit("menubar: pyobjc is required: make venv (installs pyobjc-framework-Cocoa)")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import panelstate  # noqa: E402  (host/panelstate.py)
import shortcuts  # noqa: E402  (host/shortcuts.py)

ROOT = Path(__file__).resolve().parent.parent.parent
COMMAND = str(ROOT / "bin" / "zmk-layer-hud")
RUN = panelstate.default_dir()


# ---------- Carbon's hotkeys, through ctypes (PyObjC does not wrap them) ----------

def fourcc(code):
    return int.from_bytes(code.encode("ascii"), "big")


class EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


class EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


HANDLER = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
SIGNATURE = fourcc("ZMKH")
# The ANSI virtual keycodes (HIToolbox Events.h): where the key is, whatever the input source.
KEYCODES = dict(zip("asdfhgzxcv", range(0x00, 0x0A)), b=0x0B, q=0x0C, w=0x0D, e=0x0E, r=0x0F, y=0x10, t=0x11,
                o=0x1F, u=0x20, i=0x22, p=0x23, l=0x25, j=0x26, k=0x28, n=0x2D, m=0x2E,
                **{"1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15, "6": 0x16, "5": 0x17, "9": 0x19, "7": 0x1A,
                   "8": 0x1C, "0": 0x1D})
CARBON_MODS = {"gui": 0x100, "shift": 0x200, "alt": 0x800, "ctrl": 0x1000}
# The menu's names for the WPMs the icon can show (panelstate.WPM_CHOICES).
WPM_ITEMS = (("current", "Current WPM"), ("average", "Average WPM"), ("top", "Top WPM"))
MENU_MODS = {"gui": NSEventModifierFlagCommand, "shift": NSEventModifierFlagShift,
             "alt": NSEventModifierFlagOption, "ctrl": NSEventModifierFlagControl}


class Hotkeys:
    """The shortcuts as Carbon hotkeys on the application's event target: pressed, they call
    pressed(name). set() takes the ones there away and binds the ones given."""

    def __init__(self, pressed):
        self.carbon = c = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        c.GetApplicationEventTarget.restype = ctypes.c_void_p
        c.InstallEventHandler.argtypes = [ctypes.c_void_p, HANDLER, ctypes.c_ulong, ctypes.POINTER(EventTypeSpec),
                                          ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        c.RegisterEventHotKey.argtypes = [ctypes.c_uint32, ctypes.c_uint32, EventHotKeyID, ctypes.c_void_p,
                                          ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
        c.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
        c.GetEventParameter.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                        ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
        self.target = c.GetApplicationEventTarget()
        self.names, self.refs, self.pressed = [], [], pressed

        def handle(call, event, data):
            hk = EventHotKeyID()
            if c.GetEventParameter(event, fourcc("----"), fourcc("hkid"), None, ctypes.sizeof(hk), None,
                                   ctypes.byref(hk)) == 0 and hk.signature == SIGNATURE and hk.id < len(self.names):
                self.pressed(self.names[hk.id])
            return 0
        self.handler = HANDLER(handle)      # kept: Carbon holds only the pointer
        spec = EventTypeSpec(fourcc("keyb"), 5)     # kEventClassKeyboard, kEventHotKeyPressed
        ref = ctypes.c_void_p()
        err = c.InstallEventHandler(self.target, self.handler, 1, ctypes.byref(spec), None, ctypes.byref(ref))
        if err:
            print(f"menubar: no hotkeys: InstallEventHandler said {err}", file=sys.stderr)
            self.target = None

    def set(self, sets):
        for ref in self.refs:
            self.carbon.UnregisterEventHotKey(ref)
        self.names, self.refs = [], []
        if self.target is None:
            return
        for name, sc in sets.items():
            if not sc:
                continue
            mods, key = sc
            ref = ctypes.c_void_p()
            err = self.carbon.RegisterEventHotKey(KEYCODES[key], sum(CARBON_MODS[m] for m in mods),
                                                  EventHotKeyID(SIGNATURE, len(self.names)), self.target, 0,
                                                  ctypes.byref(ref))
            if err:
                # -9878 (eventHotKeyExistsErr): another app has these keys.
                print(f"menubar: {shortcuts.label(sc, 'Darwin')} is not bound ({name}): "
                      f"RegisterEventHotKey said {err}", file=sys.stderr)
                continue
            self.names.append(name)
            self.refs.append(ref)


def crossed(base):
    """The keyboard symbol struck through, for a HUD that is not running, as Omarchy's bar draws it.
    SF Symbols has no keyboard.slash, so the stroke is drawn: a gap cut first, then the line in it,
    the way the system's own .slash symbols are."""
    size = base.size()

    def draw(rect):
        base.drawInRect_(rect)
        line = NSBezierPath.bezierPath()
        line.moveToPoint_((rect.origin.x + 1.5, rect.origin.y + 0.5))
        line.lineToPoint_((rect.origin.x + rect.size.width - 1.5, rect.origin.y + rect.size.height - 0.5))
        line.setLineCapStyle_(1)        # round
        context = NSGraphicsContext.currentContext()
        context.setCompositingOperation_(NSCompositingOperationClear)
        line.setLineWidth_(3.2)
        line.stroke()
        context.setCompositingOperation_(NSCompositingOperationSourceOver)
        NSColor.blackColor().set()
        line.setLineWidth_(1.4)
        line.stroke()
        return True
    image = NSImage.imageWithSize_flipped_drawingHandler_(size, False, draw)
    image.setTemplate_(True)
    return image


class Icon(NSObject):
    def init(self):
        self = objc.super(Icon, self).init()
        self.st = None
        self.choice = "current"         # which WPM the icon shows (panelstate.wpm_choice)
        self.look = None                # what the button shows, to set it again only when that changes
        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        button = self.item.button()
        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_("keyboard", "zmk-layer-hud") \
            if hasattr(NSImage, "imageWithSystemSymbolName_accessibilityDescription_") else None
        self.image = self.stopped_image = None
        if image is not None:
            image.setTemplate_(True)         # drawn in the menubar's own colour, light or dark
            self.image, self.stopped_image = image, crossed(image)
            button.setImage_(image)
            button.setImagePosition_(NSImageRight)     # the live WPM to its left
        else:
            button.setTitle_("⌨")
        button.setTarget_(self)
        button.setAction_("clicked:")
        button.sendActionOn_(NSEventMaskLeftMouseUp | NSEventMaskRightMouseUp)
        self.menu = NSMenu.alloc().initWithTitle_("zmk-layer-hud")
        self.config_seen, self.shortcuts = False, {}
        self.hotkeys = Hotkeys(self.hotkey)
        self.reshortcut()
        self.refresh_(None)
        # panel.json is replaced whole by a rename, and a HUD that died leaves it behind: looked at
        # twice a second, with its pid, rather than watched.
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(0.5, self, "refresh:", None, True)
        return self

    @objc.python_method
    def reshortcut(self):
        """The config's shortcuts bound, when the config is new or has changed since it was read."""
        try:
            path = shortcuts.keymap_config()
            st = os.stat(path) if path else None
            seen = (path, st.st_mtime_ns, st.st_size) if st else None
        except OSError:
            seen = None
        if seen == self.config_seen:
            return
        self.config_seen = seen
        try:
            sets = shortcuts.read(seen[0] if seen else None)
        except Exception as e:      # a config being written, or one that says something wrong
            print(f"menubar: shortcuts: {e}", file=sys.stderr)
            return
        if sets != self.shortcuts:
            self.shortcuts = sets
            self.hotkeys.set(sets)

    @objc.python_method
    def hotkey(self, name):
        if name == "power":
            self.run("power")
        elif self.st is not None:       # nothing to show or hide
            self.run("toggle")

    def refresh_(self, timer):
        if timer is not None:
            self.reshortcut()
        st, choice = panelstate.read(RUN), panelstate.wpm_choice(RUN)
        self.st, self.choice = st, choice   # the menu's numbers, kept even when the button stays as it is
        n = panelstate.wpm_shown(st, choice)
        look = (st is not None, bool(st and st["shown"]), n)
        if look == self.look and timer is not None:
            return
        self.look = look
        button = self.item.button()
        button.setAppearsDisabled_(not (st and st["shown"]))
        text = (f"{n} " if n is not None else "— ") if st else ""
        if self.image is not None:
            button.setImage_(self.image if st else self.stopped_image)
            button.setTitle_(text)
        else:
            button.setTitle_(text + "⌨")
        button.setToolTip_("zmk-layer-hud: " + ("click to hide it (it keeps counting)" if st and st["shown"] else
                                                "hidden and counting: click to show it" if st else
                                                "not running: click to start it"))

    @objc.python_method
    def run(self, *argv):
        env = dict(os.environ, ZMKHUD_STATE=RUN)
        subprocess.Popen([COMMAND, *argv], env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    def clicked_(self, sender):
        event = NSApp().currentEvent()
        if event is not None and (event.type() == NSEventTypeRightMouseUp
                                  or event.modifierFlags() & NSEventModifierFlagControl):
            self.pop_menu()
        else:
            self.run("start" if self.st is None else "toggle")

    @objc.python_method
    def pop_menu(self):
        self.menu.removeAllItems()
        if self.st is not None:
            # Which WPM the icon shows, each with its number now; the one it shows is checked.
            for choice, title in WPM_ITEMS:
                n = panelstate.wpm_shown(self.st, choice)
                item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    f"{title}: {'—' if n is None else n}", "wpm:", "")
                item.setTarget_(self)
                item.setRepresentedObject_(choice)
                item.setState_(NSControlStateValueOn if choice == self.choice else NSControlStateValueOff)
                self.menu.addItem_(item)
            self.menu.addItem_(NSMenuItem.separatorItem())
        # The third of each is the shortcut that does the same, drawn beside it by the menu.
        items = [("Start HUD", "start", "power")] if self.st is None else \
            [("Hide HUD" if self.st["shown"] else "Show HUD", "hide" if self.st["shown"] else "show", "toggle"),
             ("Quit HUD", "stop", "power")]
        for title, verb, shortcut in items:
            sc = self.shortcuts.get(shortcut)
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, "verb:", sc[1] if sc else "")
            if sc:
                item.setKeyEquivalentModifierMask_(sum(MENU_MODS[m] for m in sc[0]))
            item.setTarget_(self)
            item.setRepresentedObject_(verb)
            self.menu.addItem_(item)
        self.menu.addItem_(NSMenuItem.separatorItem())
        remove = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Remove Icon", "remove:", "")
        remove.setTarget_(self)
        self.menu.addItem_(remove)
        # The menu only for this click: one set on the item for good would open on a left click too.
        self.item.setMenu_(self.menu)
        self.item.button().performClick_(None)
        self.item.setMenu_(None)

    def verb_(self, sender):
        self.run(str(sender.representedObject()))

    def wpm_(self, sender):
        # Through the command line like the rest; the button follows on its next look (refresh_).
        self.run("menubar", "wpm", str(sender.representedObject()))

    def remove_(self, sender):
        # Until the next `zmk-layer-hud start`; `zmk-layer-hud menubar disable` keeps it away.
        AppHelper.stopEventLoop()


def main():
    # One icon, however many times it is started: start.sh starts it with every HUD, and pgrep on
    # macOS does not see its own ancestors -- this, when the HUD was started from this icon.
    os.makedirs(RUN, exist_ok=True)
    lock = open(os.path.join(RUN, "menubar.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit(0)
    # A session of its own: a login item's launchd job ends its whole process group when the HUD
    # quits, and this is to stay.
    try:
        os.setsid()
    except OSError:
        pass
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    icon = Icon.alloc().init()      # noqa: F841  (kept: an NSStatusItem nobody holds leaves the menubar)
    AppHelper.runEventLoop(installInterrupt=True)


if __name__ == "__main__":
    main()
