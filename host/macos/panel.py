#!/usr/bin/env python3
"""zmk-layer-hud macOS host: two transparent, always-on-top, non-activating overlay windows
(the layer HUD top-right, the typed-keys strip bottom-left) on the recording display, fed by
host/hudfeed.py over its WebSocket. No Hammerspoon, no event tap: the keyboard's HID reports are
the only source.

    .venv/bin/python3 host/macos/panel.py            # or host/macos/start.sh
    ZMKHUD_SCREEN=main|external  ZMKHUD_PORT=8766    # optional

Needs pyobjc-framework-Cocoa and pyobjc-framework-WebKit in the venv (make venv installs them on
macOS). The first run asks for Input Monitoring for the app that launched this (your terminal,
or the launchd agent); hudfeed.py inherits it as a child process.
"""

import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

try:
    import objc  # noqa: F401
    from AppKit import (NSApp, NSApplication, NSApplicationActivationPolicyAccessory, NSBackingStoreBuffered, NSColor,
                        NSMakeRect, NSPanel, NSScreen, NSStatusWindowLevel, NSWindowCollectionBehaviorCanJoinAllSpaces,
                        NSWindowCollectionBehaviorFullScreenAuxiliary, NSWindowCollectionBehaviorStationary,
                        NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel)
    from Foundation import NSObject, NSURL
    from WebKit import WKUserContentController, WKWebView, WKWebViewConfiguration
    from PyObjCTools import AppHelper
except ImportError:
    sys.exit("panel: pyobjc is required: make venv (installs pyobjc-framework-Cocoa and -WebKit)")

ROOT = Path(__file__).resolve().parent.parent.parent
PAGES = ROOT / "hud"
RUN = ROOT / "run"
PORT = int(os.environ.get("ZMKHUD_PORT", "8766"))
HUD_W, HUD_H = 598, 392
KEYS_W, KEYS_H = 900, 72
MARGIN = 24
WINDOWS = []


def recording_screen():
    """The external display when there is one (the laptop keeps the script), else the main one.
    ZMKHUD_SCREEN=main forces the main display."""
    screens = list(NSScreen.screens())
    main = NSScreen.mainScreen()
    if os.environ.get("ZMKHUD_SCREEN") == "main":
        return main
    for s in screens:
        if s != screens[0]:
            return s  # screens[0] is the one with the menu bar
    return main


class CloseHandler(NSObject):
    """The page's ✕ button posts "close" through webkit.messageHandlers.zmkhud."""

    def userContentController_didReceiveScriptMessage_(self, controller, message):
        if str(message.body()) == "close":
            print("panel: close requested by the page", flush=True)
            quit_app()


def overlay(frame, page, handler):
    """A borderless, non-activating panel that floats above everything on every Space."""
    panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        frame, NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel, NSBackingStoreBuffered, False)
    panel.setLevel_(NSStatusWindowLevel)
    panel.setOpaque_(False)
    panel.setBackgroundColor_(NSColor.clearColor())
    panel.setHasShadow_(False)
    panel.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces
                                 | NSWindowCollectionBehaviorStationary
                                 | NSWindowCollectionBehaviorFullScreenAuxiliary)
    panel.setHidesOnDeactivate_(False)
    panel.setFloatingPanel_(True)

    config = WKWebViewConfiguration.alloc().init()
    ucc = WKUserContentController.alloc().init()
    ucc.addScriptMessageHandler_name_(handler, "zmkhud")
    config.setUserContentController_(ucc)
    web = WKWebView.alloc().initWithFrame_configuration_(((0, 0), (frame.size.width, frame.size.height)), config)
    web.setValue_forKey_(False, "drawsBackground")  # transparent page background
    url = NSURL.fileURLWithPath_(str(PAGES / page))
    # The query string survives a file URL in WKWebView; the page connects to hudfeed's WebSocket.
    url = NSURL.URLWithString_relativeToURL_(f"{page}?ws=ws://127.0.0.1:{PORT}", url)
    web.loadFileURL_allowingReadAccessToURL_(url, NSURL.fileURLWithPath_(str(PAGES)))
    panel.setContentView_(web)
    panel.orderFrontRegardless()
    WINDOWS.append(panel)
    return panel


def quit_app(*_):
    AppHelper.stopEventLoop()


def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)  # no Dock icon, no menu bar

    RUN.mkdir(exist_ok=True)
    feed_log = (RUN / "hudfeed.log").open("w")
    feed = subprocess.Popen([sys.executable, "-u", str(ROOT / "host" / "hudfeed.py"), "--debug", "--port", str(PORT)],
                            stdout=feed_log, stderr=feed_log, start_new_session=True)

    screen = recording_screen()
    vf = screen.visibleFrame()  # excludes the menu bar and the Dock; origin bottom-left
    hud_frame = NSMakeRect(vf.origin.x + vf.size.width - HUD_W - MARGIN, vf.origin.y + vf.size.height - HUD_H - MARGIN, HUD_W, HUD_H)
    keys_frame = NSMakeRect(vf.origin.x + MARGIN, vf.origin.y + MARGIN, KEYS_W, KEYS_H)

    handler = CloseHandler.alloc().init()
    overlay(hud_frame, "index.html", handler)
    overlay(keys_frame, "keys.html", handler)
    print(f"panel: HUD on {screen.localizedName()} ({int(vf.size.width)}x{int(vf.size.height)}); "
          f"feed pid {feed.pid}, log {RUN / 'hudfeed.log'}", flush=True)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, quit_app)

    def watch_feed():
        feed.wait()
        print("panel: hudfeed exited; quitting", flush=True)
        AppHelper.callAfter(quit_app)
    threading.Thread(target=watch_feed, daemon=True).start()

    try:
        AppHelper.runEventLoop(installInterrupt=False)
    finally:
        for w in WINDOWS:
            w.orderOut_(None)
        try:
            os.killpg(feed.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            feed.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


if __name__ == "__main__":
    main()
