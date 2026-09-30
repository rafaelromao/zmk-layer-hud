#!/usr/bin/env python3
"""zmk-layer-hud macOS host: one transparent, always-on-top, non-activating window with the layer
HUD and the typed-keys strip below it. The feed (host/hudfeed.py) runs in this process and its
messages are injected straight into the page. They go out on the feed's WebSocket as well
(ZMKHUD_PORT, 127.0.0.1 only), as on Linux, and what a client sends in there (`zmk-layer-hud
poke`) is drawn on this page, marked as not the keyboard's.

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
    """The remembered frame when it is still on some screen, else top-right of the focused screen.

    Its own size too, not the default: Cocoa's origin is the bottom-left, and the page's first
    size keeps the top where it is (resize), so a frame restored at 480 points tall put that top
    480 above the saved bottom -- and the panel crept up by the difference on every start."""
    saved = load_state().get("frame")
    if saved and len(saved) == 4:
        x, y, w, h = saved
        for s in NSScreen.screens():
            f = s.frame()
            if f.origin.x <= x + w / 2 <= f.origin.x + f.size.width and f.origin.y <= y + h / 2 <= f.origin.y + f.size.height:
                return NSMakeRect(x, y, w, h)
    vf = focused_screen().visibleFrame()  # excludes menu bar and Dock; origin bottom-left
    return NSMakeRect(vf.origin.x + vf.size.width - WIDTH - MARGIN, vf.origin.y + vf.size.height - HEIGHT - MARGIN, WIDTH, HEIGHT)


class DragWebView(WKWebView):
    """Dragging anywhere on the page moves the window; clicks still reach the page (the ✕)."""

    def mouseDownCanMoveWindow(self):
        return True


class Bridge(NSObject):
    """Page → host messages (the ✕ posts "close"; the page's counts and the heatmap it switched
    to go to the session) and window events. Only this panel's own page can post here, so it needs
    no token the way a WebSocket client does."""

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
        kind = msg.get("kind") if isinstance(msg, dict) else None
        if kind == "size":
            Host.instance.resize(msg.get("width"), msg.get("height"))
        elif kind in ("tally", "heatmap", "pref"):
            store = Host.instance.feed.sessions if Host.instance and Host.instance.feed else None
            if store is None:
                return
            try:
                if kind == "tally":
                    store.apply(msg)
                elif kind == "heatmap":
                    store.set_heatmap(msg.get("mode"))
                else:
                    store.set_pref(msg.get("name"), msg.get("value"))
            except Exception as e:  # a count that could not be kept must not take the panel down
                log(f"session: {type(e).__name__}: {e}")

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

        # The feed runs in this process; its worker threads hand messages to the main thread, and
        # to the socket the Linux panel's feed serves too (ZMKHUD_PORT): a browser can watch, and
        # `zmk-layer-hud poke` drives this page -- what it sends in comes back here (on_sent_in).
        # Counts reach the session through the bridge alone, so the socket gets no tally token.
        hub = hudfeed.Hub(on_sent_in=lambda msg: AppHelper.callAfter(self.deliver, msg))
        self.socket = hudfeed.HubThread(hub, int(os.environ.get("ZMKHUD_PORT", "8766")), log=log)

        def emit(msg):
            AppHelper.callAfter(self.deliver, msg)
            self.socket.send(msg)
        self.feed = hudfeed.Feed(emit, log=log).start()
        hub.on_inject, hub.sessions = self.feed.resync, self.feed.sessions
        self.socket.start()
        # Width from the config (hud.width), whether or not its keymap converts; the height follows
        # the page (see resize).
        width = (self.feed.cfg.get("hud") or {}).get("width")
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
        if msg.get("sent"):
            js = f"hud.receive({data})"   # sent in (poke): lit the same, and never counted (`sent`)
        elif msg["kind"] == "keymap":
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
            js = f"hud.receive({data})"   # the rest (the session) through the page's own dispatcher
        if msg.get("device"):
            js = f"hud.setDevice({json.dumps(msg['device'])}); " + js
        if self.ready:
            self.web.evaluateJavaScript_completionHandler_(js, None)
        else:
            self.queue.append(js)

    def stop(self):
        self.feed.stop()
        self.socket.stop()
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
