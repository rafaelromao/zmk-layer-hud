#!/usr/bin/env python3
"""zmk-layer-hud macOS host: one transparent, always-on-top, non-activating window with the layer
HUD and the typed-keys strip below it. The feed (host/hudfeed.py) runs in this process and its
messages are injected straight into the page: no WebSocket, no other source than the keyboard.

    .venv/bin/python3 host/macos/panel.py            # or host/macos/start.sh

The window opens on the screen that has keyboard focus, can be dragged anywhere, and remembers
its position in ~/.config/zmk-layer-hud/state.json. Needs pyobjc-framework-Cocoa and
pyobjc-framework-WebKit in the venv (make venv installs them on macOS). The first run asks for
Input Monitoring for the app that launched this (your terminal).
"""

import json
import os
import signal
import sys
import time
from pathlib import Path

DEBUG = os.environ.get("ZMKHUD_DEBUG") == "1"  # log every layer and position message with a timestamp

try:
    import objc
    from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory, NSBackingStoreBuffered, NSColor,
                        NSMakeRect, NSPanel, NSScreen, NSStatusWindowLevel, NSWindowCollectionBehaviorCanJoinAllSpaces,
                        NSWindowCollectionBehaviorFullScreenAuxiliary, NSWindowCollectionBehaviorStationary,
                        NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel)
    from Foundation import NSNotificationCenter, NSObject, NSURL
    from WebKit import WKUserContentController, WKWebView, WKWebViewConfiguration
    from PyObjCTools import AppHelper
except ImportError:
    sys.exit("panel: pyobjc is required: make venv (installs pyobjc-framework-Cocoa and -WebKit)")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import hudfeed  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
PAGES = ROOT / "hud"
STATE = Path(os.path.expanduser("~/.config/zmk-layer-hud/state.json"))
WIDTH, HEIGHT = 598, 480   # board + banner + the typed-keys strip below
MARGIN = 24


def log(*a):
    print("panel:", *a, file=sys.stderr, flush=True)


def load_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state):
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state))
    except OSError as e:
        log(f"could not save {STATE}: {e}")


def focused_screen():
    """The screen with keyboard focus (the key window's screen), else the one with the menu bar."""
    return NSScreen.mainScreen() or NSScreen.screens()[0]


def initial_frame():
    """The remembered frame when it is still on some screen, else top-right of the focused screen."""
    saved = load_state().get("frame")
    if saved and len(saved) == 4:
        x, y, w, h = saved
        for s in NSScreen.screens():
            f = s.frame()
            if f.origin.x <= x + w / 2 <= f.origin.x + f.size.width and f.origin.y <= y + h / 2 <= f.origin.y + f.size.height:
                return NSMakeRect(x, y, WIDTH, HEIGHT)
    vf = focused_screen().visibleFrame()  # excludes menu bar and Dock; origin bottom-left
    return NSMakeRect(vf.origin.x + vf.size.width - WIDTH - MARGIN, vf.origin.y + vf.size.height - HEIGHT - MARGIN, WIDTH, HEIGHT)


class DragWebView(WKWebView):
    """Dragging anywhere on the page moves the window; clicks still reach the page (the ✕)."""

    def mouseDownCanMoveWindow(self):
        return True


class Bridge(NSObject):
    """Page → host messages (the ✕ posts "close") and window events."""

    def userContentController_didReceiveScriptMessage_(self, controller, message):
        body = str(message.body())
        if body == "close":
            log("close requested by the page")
            AppHelper.stopEventLoop()
            return
        try:
            msg = json.loads(body)
        except ValueError:
            return
        if msg.get("kind") == "size":
            Host.instance.resize(msg.get("width"), msg.get("height"))

    def windowDidMove_(self, notification):
        f = notification.object().frame()
        save_state({"frame": [f.origin.x, f.origin.y, f.size.width, f.size.height]})

    def webView_didFinishNavigation_(self, webview, navigation):
        Host.instance.page_ready()


class Host:
    """Owns the window, the page and the in-process feed; marshals feed messages to the page."""

    instance = None

    def __init__(self):
        Host.instance = self
        self.ready = False
        self.queue = []
        self.bridge = Bridge.alloc().init()

        frame = initial_frame()
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel, NSBackingStoreBuffered, False)
        panel.setLevel_(NSStatusWindowLevel)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(False)
        panel.setMovableByWindowBackground_(True)
        panel.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces
                                     | NSWindowCollectionBehaviorStationary
                                     | NSWindowCollectionBehaviorFullScreenAuxiliary)
        panel.setHidesOnDeactivate_(False)
        panel.setFloatingPanel_(True)
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self.bridge, "windowDidMove:", "NSWindowDidMoveNotification", panel)

        config = WKWebViewConfiguration.alloc().init()
        ucc = WKUserContentController.alloc().init()
        ucc.addScriptMessageHandler_name_(self.bridge, "zmkhud")
        config.setUserContentController_(ucc)
        web = DragWebView.alloc().initWithFrame_configuration_(((0, 0), (WIDTH, HEIGHT)), config)
        web.setValue_forKey_(False, "drawsBackground")  # transparent page background
        web.setNavigationDelegate_(self.bridge)
        web.loadFileURL_allowingReadAccessToURL_(NSURL.fileURLWithPath_(str(PAGES / "index.html")),
                                                 NSURL.fileURLWithPath_(str(PAGES)))
        panel.setContentView_(web)
        panel.orderFrontRegardless()
        self.panel, self.web = panel, web
        log(f"HUD on {focused_screen().localizedName()} at {int(frame.origin.x)},{int(frame.origin.y)}")

        # The feed runs in this process; its worker threads hand messages to the main thread.
        self.feed = hudfeed.Feed(lambda msg: AppHelper.callAfter(self.deliver, msg), log=log).start()
        # Width from the config (hud.width); the height follows the page (see resize).
        width = ((self.feed.source.cfg if self.feed.source else {}).get("hud") or {}).get("width")
        if width:
            self.resize(int(width), None)

    def resize(self, width, height):
        """The page knows how tall the layout is; keep the top-left corner where the user put it."""
        f = self.panel.frame()
        w = float(width or f.size.width)
        h = float(height or f.size.height)
        if abs(w - f.size.width) < 1 and abs(h - f.size.height) < 1:
            return
        top = f.origin.y + f.size.height
        self.panel.setFrame_display_(NSMakeRect(f.origin.x, top - h, w, h), True)
        self.web.setFrame_(((0, 0), (w, h)))

    def page_ready(self):
        self.ready = True
        for js in self.queue:
            self.web.evaluateJavaScript_completionHandler_(js, None)
        self.queue = []

    def deliver(self, msg):
        data = json.dumps(msg, ensure_ascii=False)
        if DEBUG and msg["kind"] in ("layers", "press", "release"):
            log(f"{time.monotonic() * 1000:.0f}ms {data}")
        if msg["kind"] == "keymap":
            js = f"hud.load({data})"
        elif msg["kind"] == "layers":
            js = f"hud.setLayers({json.dumps(msg['ids'])})"
        elif msg["kind"] == "key":
            js = f"hud.key({data})"  # hud.key forwards to the strip on the same page
        elif msg["kind"] == "device":
            js = f"hud.setDevice({json.dumps(msg['name'])})"
        elif msg["kind"] == "press":
            js = f"hud.pressAt({int(msg['pos'])})"
        elif msg["kind"] == "release":
            js = f"hud.releaseAt({int(msg['pos'])})"
        else:
            return
        if msg.get("device"):
            js = f"hud.setDevice({json.dumps(msg['device'])}); " + js
        if self.ready:
            self.web.evaluateJavaScript_completionHandler_(js, None)
        else:
            self.queue.append(js)

    def stop(self):
        self.feed.stop()
        self.panel.orderOut_(None)


def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)  # no Dock icon, no menu bar
    host = Host()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: AppHelper.stopEventLoop())
    try:
        AppHelper.runEventLoop(installInterrupt=False)
    finally:
        host.stop()


if __name__ == "__main__":
    main()
