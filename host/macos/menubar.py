#!/usr/bin/env python3
"""zmk-layer-hud's menubar icon on macOS: a process of its own, so it stays when the HUD quits,
the way Omarchy's bar icon does (host/linux/omarchy).

The keyboard symbol has the live WPM beside it while the HUD runs, shown or hidden, and is dimmed
while the HUD is hidden or not running. A click shows or hides the HUD, or starts it; a right-click
offers the same, Quit HUD, and Remove Icon. What it shows comes from $STATE/panel.json, which the
running panel keeps (host/panelstate.py), and what it does goes through the command line, like
any other caller.

host/macos/start.sh starts it with the HUD, unless `zmk-layer-hud menubar disable` said not to.
"""

import os
import subprocess
import sys
from pathlib import Path

try:
    import objc
    from AppKit import (NSApp, NSApplication, NSApplicationActivationPolicyAccessory, NSEventModifierFlagControl,
                        NSEventMaskLeftMouseUp, NSEventMaskRightMouseUp, NSEventTypeRightMouseUp, NSImage,
                        NSImageLeft, NSMenu, NSMenuItem, NSStatusBar, NSVariableStatusItemLength)
    from Foundation import NSObject, NSTimer
    from PyObjCTools import AppHelper
except ImportError:
    sys.exit("menubar: pyobjc is required: make venv (installs pyobjc-framework-Cocoa)")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import panelstate  # noqa: E402  (host/panelstate.py)

ROOT = Path(__file__).resolve().parent.parent.parent
COMMAND = str(ROOT / "bin" / "zmk-layer-hud")
RUN = panelstate.default_dir()


class Icon(NSObject):
    def init(self):
        self = objc.super(Icon, self).init()
        self.st = None
        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        button = self.item.button()
        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_("keyboard", "zmk-layer-hud") \
            if hasattr(NSImage, "imageWithSystemSymbolName_accessibilityDescription_") else None
        if image is not None:
            image.setTemplate_(True)         # drawn in the menubar's own colour, light or dark
            button.setImage_(image)
            button.setImagePosition_(NSImageLeft)
        else:
            button.setTitle_("⌨")
        button.setTarget_(self)
        button.setAction_("clicked:")
        button.sendActionOn_(NSEventMaskLeftMouseUp | NSEventMaskRightMouseUp)
        self.menu = NSMenu.alloc().initWithTitle_("zmk-layer-hud")
        self.refresh_(None)
        # panel.json is replaced whole by a rename, and a HUD that died leaves it behind: looked at
        # twice a second, with its pid, rather than watched.
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(0.5, self, "refresh:", None, True)
        return self

    def refresh_(self, timer):
        st = panelstate.read(RUN)
        if st == self.st and timer is not None:
            return
        self.st = st
        button = self.item.button()
        button.setAppearsDisabled_(not (st and st["shown"]))
        text = f" {st['wpm']}" if st else ""
        if button.image() is not None:
            button.setTitle_(text)
        else:
            button.setTitle_("⌨" + text)
        button.setToolTip_("zmk-layer-hud: " + ("click to hide it (it keeps counting)" if st and st["shown"] else
                                                "hidden and counting: click to show it" if st else
                                                "not running: click to start it"))

    @objc.python_method
    def run(self, verb):
        env = dict(os.environ, ZMKHUD_STATE=RUN)
        subprocess.Popen([COMMAND, verb], env=env, stdin=subprocess.DEVNULL,
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
        items = [("Start HUD", "start")] if self.st is None else \
            [("Hide HUD" if self.st["shown"] else "Show HUD", "hide" if self.st["shown"] else "show"), ("Quit HUD", "stop")]
        for title, verb in items:
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, "verb:", "")
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

    def remove_(self, sender):
        # Until the next `zmk-layer-hud start`; `zmk-layer-hud menubar disable` keeps it away.
        AppHelper.stopEventLoop()


def main():
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
