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
    from AppKit import (NSAlert, NSApp, NSApplication, NSApplicationActivationPolicyAccessory, NSBackingStoreBuffered,
                        NSColor, NSEvent, NSEventMaskLeftMouseDown, NSEventMaskLeftMouseUp, NSEventMaskRightMouseUp,
                        NSEventModifierFlagControl, NSEventTypeRightMouseUp, NSImage, NSMakeRect, NSMenu, NSMenuItem,
                        NSOnState, NSStatusBar, NSVariableStatusItemLength,
                        NSPanel, NSTextField, NSScreen, NSStatusWindowLevel, NSWindowCollectionBehaviorCanJoinAllSpaces,
                        NSWindowCollectionBehaviorFullScreenAuxiliary, NSWindowCollectionBehaviorStationary,
                        NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel)
    from Foundation import (NSActivityUserInitiatedAllowingIdleSystemSleep, NSNotificationCenter, NSObject,
                            NSProcessInfo, NSURL)
    from WebKit import WKUserContentController, WKWebView, WKWebViewConfiguration
    from PyObjCTools import AppHelper, MachSignals
except ImportError:
    sys.exit("panel: pyobjc is required: make venv (installs pyobjc-framework-Cocoa and -WebKit)")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import hudfeed  # noqa: E402
import session as session_mod  # noqa: E402  (host/session.py)
import panelstate  # noqa: E402  (host/panelstate.py)

ROOT = Path(__file__).resolve().parent.parent.parent
PAGES = ROOT / "hud"
STATE = Path(os.path.expanduser("~/.config/zmk-layer-hud/state.json"))
WIDTH, HEIGHT = 598, 480   # board + banner + the typed-keys strip below
MARGIN = 24
RUN = panelstate.default_dir()      # where panel.json says whether the HUD is shown
HIDDEN = os.environ.get("ZMKHUD_HIDDEN") == "1"   # `zmk-layer-hud start --hidden`

# Hidden is drawn as nothing, not ordered out. A window off screen makes its page a hidden page to
# WebKit, which slows its timers (and App Nap the process), and the page's timers are what count
# (hud.js): so the panel stays, transparent, with nothing painted on it, and the page runs as it
# does on screen. A window that paints nothing passes its clicks through, as AppKit does by default
# for transparent areas -- which ignoresMouseEvents would undo once set back to NO.
HIDE_JS = ("(function(){if(document.getElementById('zmkhud-hidden'))return;"
           "var s=document.createElement('style');s.id='zmkhud-hidden';"
           "s.textContent='*{visibility:hidden !important}';"
           "(document.head||document.documentElement).appendChild(s);})()")
SHOW_JS = "(function(){var s=document.getElementById('zmkhud-hidden');if(s)s.remove();})()"


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
    """The page, filling the panel. The window is moved by dragging the page anywhere but on its
    controls, and that is done here rather than by AppKit's movable-by-background: that asks one of
    WebKit's own views whether a press may move the window, never this one, so it moved the window
    from under the slider too. The page says where its controls are ({"kind": "nodrag", "rects":
    [[x, y, w, h], ...]}, page points from the top left); a press anywhere else starts a window drag
    (Host.mouse_down)."""

    no_drag = []

    def mouseDownCanMoveWindow(self):
        return False

    @objc.python_method
    def on_control(self, event):
        """Whether a mouse-down in the window lands on one of the page's controls."""
        p = self.convertPoint_fromView_(event.locationInWindow(), None)
        y = p.y if self.isFlipped() else self.frame().size.height - p.y
        return any(x <= p.x <= x + w and top <= y <= top + h for x, top, w, h in DragWebView.no_drag)

    def willOpenMenu_withEvent_(self, menu, event):
        """The page's own menu (right-click) gets the sessions: save the one being typed into under
        a name, load a saved one, or start a new one -- what `zmk-layer-hud session` does."""
        objc.super(DragWebView, self).willOpenMenu_withEvent_(menu, event)
        store = Host.instance.feed.sessions if Host.instance and Host.instance.feed else None
        if store is None or not store.persist:
            return
        bridge = Host.instance.bridge
        try:
            active = session_mod.status(store.dir)[1]["name"]
            names = sorted(session_mod.sessions(store.dir))
        except Exception as e:
            log(f"session: {type(e).__name__}: {e}")
            return
        menu.addItem_(NSMenuItem.separatorItem())
        head = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(f"Session: {active}", None, "")
        head.setEnabled_(False)
        menu.addItem_(head)
        for title, action in (("Save Session As…", "saveSession:"), ("New Session", "newSession:")):
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
            item.setTarget_(bridge)
            menu.addItem_(item)
        load = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Load Session", None, "")
        sub = NSMenu.alloc().initWithTitle_("Load Session")
        for name in names:
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(name, "loadSession:", "")
            item.setTarget_(bridge)
            item.setRepresentedObject_(name)
            if name == active:
                item.setState_(NSOnState)
            sub.addItem_(item)
        load.setSubmenu_(sub)
        load.setEnabled_(bool(names))
        menu.addItem_(load)


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
        elif kind == "nodrag":
            rects = msg.get("rects")
            if isinstance(rects, list):
                DragWebView.no_drag = [tuple(float(v) for v in r) for r in rects
                                       if isinstance(r, list) and len(r) == 4 and all(isinstance(v, (int, float)) for v in r)]
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

    # The menu's session items (DragWebView.willOpenMenu_withEvent_). What was counted and not yet
    # written goes first, and the store is read again at once, so the page shows the change now
    # rather than at the next poll.
    @objc.python_method
    def _sessions(self, what, *args):
        store = Host.instance.feed.sessions if Host.instance and Host.instance.feed else None
        if store is None:
            return
        try:
            store.flush()
            what(store.dir, *args)
            store.reload(announce=True)
        except session_mod.SessionError as e:
            ask_name(str(e), None)          # said in a plain alert, with nothing to type
        except Exception as e:
            log(f"session: {type(e).__name__}: {e}")

    def saveSession_(self, sender):
        name = ask_name("Save the session being typed into as:", "")
        if name:
            self._sessions(session_mod.save, name)

    def loadSession_(self, sender):
        self._sessions(session_mod.load, str(sender.representedObject()))

    def newSession_(self, sender):
        self._sessions(session_mod.new)

    # The menubar icon (Host.make_status_item): a click shows or hides the HUD, a right-click (or a
    # control-click) offers the same and Quit.
    def statusClicked_(self, sender):
        event = NSApp().currentEvent()
        if event is not None and (event.type() == NSEventTypeRightMouseUp
                                  or event.modifierFlags() & NSEventModifierFlagControl):
            Host.instance.status_menu()
        else:
            Host.instance.set_shown(not Host.instance.shown)

    def toggleHUD_(self, sender):
        Host.instance.set_shown(not Host.instance.shown)

    def quitHUD_(self, sender):
        AppHelper.stopEventLoop()

    def windowDidMove_(self, notification):
        f = notification.object().frame()
        save_state({"frame": [f.origin.x, f.origin.y, f.size.width, f.size.height]})

    def applicationWillTerminate_(self, notification):
        """The one way out that still runs Python: stopEventLoop (✕, a signal) ends in
        NSApp.terminate_, which calls exit() rather than returning from the run loop, so code
        after runEventLoop never runs. What the session has counted and not yet written goes now."""
        if Host.instance:
            Host.instance.stop()

    def webView_didStartProvisionalNavigation_(self, webview, navigation):
        Host.instance.page_leaving()

    def webView_didFinishNavigation_(self, webview, navigation):
        Host.instance.page_ready()


def ask_name(message, default):
    """A small modal alert, the panel's one dialog: with a field when default is a string (-> what
    was typed, or None if cancelled), without one to say something (-> None)."""
    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)   # an accessory app's alert is behind otherwise
    alert = NSAlert.alloc().init()
    alert.setMessageText_(message)
    field = None
    if default is not None:
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 240, 24))
        field.setStringValue_(default)
        alert.setAccessoryView_(field)
        alert.addButtonWithTitle_("Save")
        alert.addButtonWithTitle_("Cancel")
        alert.window().setInitialFirstResponder_(field)
    ok = alert.runModal() == 1000   # NSAlertFirstButtonReturn
    return field.stringValue().strip() if field is not None and ok else None


class Host:
    """Owns the window, the page and the in-process feed; marshals feed messages to the page."""

    instance = None

    def __init__(self):
        Host.instance = self
        self.stopped = False
        self.shown = not HIDDEN
        self.ready = False
        self.loaded = False             # the first page has loaded (page_ready): a later one is a reload
        self.queue = []
        self.standing = {}              # kind -> the latest of it, for a reloaded page
        self.bridge = Bridge.alloc().init()

        frame = initial_frame()
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel, NSBackingStoreBuffered, False)
        panel.setLevel_(NSStatusWindowLevel)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(False)
        panel.setMovableByWindowBackground_(False)   # DragWebView and mouse_down move it instead
        panel.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces
                                     | NSWindowCollectionBehaviorStationary
                                     | NSWindowCollectionBehaviorFullScreenAuxiliary)
        panel.setHidesOnDeactivate_(False)
        panel.setFloatingPanel_(True)
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self.bridge, "windowDidMove:", "NSWindowDidMoveNotification", panel)
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self.bridge, "applicationWillTerminate:", "NSApplicationWillTerminateNotification", None)

        config = WKWebViewConfiguration.alloc().init()
        ucc = WKUserContentController.alloc().init()
        ucc.addScriptMessageHandler_name_(self.bridge, "zmkhud")
        config.setUserContentController_(ucc)
        web = DragWebView.alloc().initWithFrame_configuration_(((0, 0), (WIDTH, HEIGHT)), config)
        web.setValue_forKey_(False, "drawsBackground")  # transparent page background
        web.setNavigationDelegate_(self.bridge)
        # WebKit also takes a page for hidden when its window is covered, and the HUD counts
        # whatever is over it. SPI, hence the guard: without it a covered HUD only counts slower.
        if web.respondsToSelector_("_setWindowOcclusionDetectionEnabled:"):
            web._setWindowOcclusionDetectionEnabled_(False)
        # And App Nap would slow the whole process, the feed's threads with it, once macOS decides
        # nobody is looking.
        self.activity = NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
            NSActivityUserInitiatedAllowingIdleSystemSleep, "the HUD counts what is typed")
        web.loadFileURL_allowingReadAccessToURL_(NSURL.fileURLWithPath_(str(PAGES / "index.html")),
                                                 NSURL.fileURLWithPath_(str(PAGES)))
        panel.setContentView_(web)
        # Every press in the panel is looked at first: on a control it goes to the page as it is,
        # anywhere else it moves the window and the page never sees it (nothing there takes a click).
        self.monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(NSEventMaskLeftMouseDown, self.mouse_down)
        if not self.shown:
            panel.setAlphaValue_(0.0)
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
        self.make_status_item()
        # Width from the config (hud.width), whether or not its keymap converts; the height follows
        # the page (see resize).
        width = (self.feed.cfg.get("hud") or {}).get("width")
        if width:
            self.resize(int(width), None)
        # main() put the signal's handler in first: the CLI signals whatever pid this file names.
        panelstate.write(RUN, self.shown)

    def make_status_item(self):
        """The menubar icon, which is how a hidden HUD comes back without a terminal. Kept on self:
        an NSStatusItem nobody holds leaves the menubar."""
        self.status = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        button = self.status.button()
        image = None
        if hasattr(NSImage, "imageWithSystemSymbolName_accessibilityDescription_"):
            image = NSImage.imageWithSystemSymbolName_accessibilityDescription_("keyboard", "zmk-layer-hud")
        if image is not None:
            image.setTemplate_(True)         # drawn in the menubar's own colour, light or dark
            button.setImage_(image)
        else:
            button.setTitle_("⌨")
        button.setTarget_(self.bridge)
        button.setAction_("statusClicked:")
        button.sendActionOn_(NSEventMaskLeftMouseUp | NSEventMaskRightMouseUp)
        self.menu = NSMenu.alloc().initWithTitle_("zmk-layer-hud")
        self.menu_toggle = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", "toggleHUD:", "")
        self.menu_toggle.setTarget_(self.bridge)
        self.menu.addItem_(self.menu_toggle)
        self.menu.addItem_(NSMenuItem.separatorItem())
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Quit HUD", "quitHUD:", "")
        quit_item.setTarget_(self.bridge)
        self.menu.addItem_(quit_item)
        self.show_status()

    def show_status(self):
        button = self.status.button()
        button.setAppearsDisabled_(not self.shown)       # dimmed while hidden
        button.setToolTip_("zmk-layer-hud: click to " + ("hide it (it keeps counting)" if self.shown else "show it"))
        self.menu_toggle.setTitle_("Hide HUD" if self.shown else "Show HUD")

    def status_menu(self):
        # The menu only for this click: one set on the item for good would open on a left click too.
        self.status.setMenu_(self.menu)
        self.status.button().performClick_(None)
        self.status.setMenu_(None)

    def set_shown(self, shown):
        """On screen or off it; the page goes on as before either way."""
        if shown != self.shown:
            self.shown = shown
            self.panel.setAlphaValue_(1.0 if shown else 0.0)
            if self.ready:          # otherwise page_ready puts the style in, with the page
                self.web.evaluateJavaScript_completionHandler_(SHOW_JS if shown else HIDE_JS, None)
            if DEBUG and not shown:
                AppHelper.callLater(3.0, self.say_visibility)
        if shown:
            self.panel.orderFrontRegardless()
        if getattr(self, "status", None) is not None:
            self.show_status()
        panelstate.write(RUN, shown)

    def say_visibility(self):
        """ZMKHUD_DEBUG: what WebKit makes of the hidden HUD. `visible` is the point of hiding it
        this way; `hidden` would mean its timers are being slowed."""
        def said(value, error):
            log(f"hidden: the page is {value}; the window's occlusion state {int(self.panel.occlusionState())}")
        self.web.evaluateJavaScript_completionHandler_("document.visibilityState", said)

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

    # What a page loaded afresh (a reload from the page's menu) has to be told again: the latest of
    # each, since the feed says them once, when they change. Keys come and go and are not kept.
    STANDING = ("keymap", "layers", "device", "session", "secure")

    def mouse_down(self, event):
        if event.window() is None or event.window() != self.panel:
            return event
        if self.web.on_control(event):
            if DEBUG:
                log("mouse down on a control: the page has it")
            return event
        self.panel.performWindowDragWithEvent_(event)
        return None

    def page_ready(self):
        self.ready = True
        replay = [self.standing[k] for k in self.STANDING if k in self.standing] if self.loaded else []
        if not self.shown:
            replay.insert(0, HIDE_JS)
        self.loaded = True
        for js in replay + self.queue:
            self.web.evaluateJavaScript_completionHandler_(js, None)
        self.queue = []

    def page_leaving(self):
        """A reload has begun: until the new page is ready, messages wait for it."""
        self.ready = False

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
        if msg["kind"] in self.STANDING and not msg.get("sent"):
            self.standing[msg["kind"]] = js
        if self.ready:
            self.web.evaluateJavaScript_completionHandler_(js, None)
        else:
            self.queue.append(js)

    def stop(self):
        if self.stopped:
            return
        self.stopped = True
        panelstate.remove(RUN)
        self.feed.stop()
        self.socket.stop()
        self.panel.orderOut_(None)


def main():
    # Handlers first: a signal that comes in before its handler takes the default action, which
    # for these is to end the process. MachSignals rather than signal.signal: Python runs a
    # handler only when Python next runs on the main thread, and an idle HUD runs none there, so a
    # `stop` used to wait for the next keystroke. These wake the run loop instead -- its default
    # mode only, so they wait while a menu or an alert is open.
    for sig in (signal.SIGINT, signal.SIGTERM):
        MachSignals.signal(sig, lambda _sig: AppHelper.stopEventLoop())

    def asked(_sig):
        # `zmk-layer-hud show` / `hide`: what they want is in panel.want.
        want = panelstate.wanted(RUN)
        if want is not None and Host.instance:
            Host.instance.set_shown(want)
    MachSignals.signal(panelstate.SIGNAL, asked)
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)  # no Dock icon, no menu bar
    Host()
    # Host.stop runs on the way out from applicationWillTerminate_: this never returns.
    AppHelper.runEventLoop(installInterrupt=False)


if __name__ == "__main__":
    main()
