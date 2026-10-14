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

import asyncio
import json
import os
import secrets
import signal
import sys
import threading
import time
from pathlib import Path

DEBUG = os.environ.get("ZMKHUD_DEBUG") == "1"  # log every layer message with a timestamp

try:
    import objc
    from AppKit import (NSAlert, NSApplication, NSApplicationActivationPolicyAccessory, NSBackingStoreBuffered,
                        NSColor, NSCursor, NSEvent, NSEventMaskLeftMouseDown, NSMakeRect, NSMenu, NSMenuItem, NSOnState,
                        NSPanel, NSTextField, NSScreen, NSStatusWindowLevel, NSTrackingActiveAlways, NSTrackingArea,
                        NSTrackingInVisibleRect, NSTrackingMouseEnteredAndExited, NSTrackingMouseMoved,
                        NSWindowCollectionBehaviorCanJoinAllSpaces,
                        NSWindowCollectionBehaviorFullScreenAuxiliary, NSWindowCollectionBehaviorStationary,
                        NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel, NSWorkspace)
    from Foundation import (NSActivityUserInitiatedAllowingIdleSystemSleep, NSNotificationCenter, NSObject,
                            NSProcessInfo, NSURL, NSZeroRect)
    from WebKit import WKUserContentController, WKWebView, WKWebViewConfiguration
    from PyObjCTools import AppHelper, MachSignals
except ImportError:
    sys.exit("panel: pyobjc is required: make venv (installs pyobjc-framework-Cocoa and -WebKit)")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cgevent  # noqa: E402  (host/macos/cgevent.py)
import dock as dock_mod  # noqa: E402  (host/dock.py)
import hudfeed  # noqa: E402
import inject  # noqa: E402  (host/inject.py)
import panelcore  # noqa: E402  (host/panelcore.py)
import session as session_mod  # noqa: E402  (host/session.py)
import panelstate  # noqa: E402  (host/panelstate.py)
import remote as remote_mod  # noqa: E402  (host/remote.py)

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


def save_state(changes):
    """Merged into what is there: the frame and whether it was docked are kept side by side (and the
    Linux panel's own, should one config be shared)."""
    state = load_state()
    state = state if isinstance(state, dict) else {}
    state.update(changes)
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        panelstate.write_json(str(STATE), state)     # whole or not at all
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
    (Host.mouse_down).

    The pointer is the panel's to set as well. WebKit sets it only for the key window, and a panel
    that never takes the keyboard is never that: so over a control the panel shows the hand the
    page asks for (every control does), and the arrow elsewhere. The page's hover styles want the
    mouse's moves, which WebKit too tracks only in the key window: the panel tracks them always
    (track_pointer) and passes them on."""

    no_drag = []
    POINTER = NSTrackingMouseMoved | NSTrackingMouseEnteredAndExited | NSTrackingInVisibleRect | NSTrackingActiveAlways

    def mouseDownCanMoveWindow(self):
        return False

    def acceptsFirstMouse_(self, event):
        # The panel is never the key window: every click on it is a first click, and a key
        # touched on the board has to reach the page on that one.
        return True

    @objc.python_method
    def track_pointer(self):
        """Mouse moves over the page, key window or not (mouseMoved_, mouseExited_). The rect is
        the visible one, kept up by AppKit, so this is done once."""
        self.addTrackingArea_(NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(NSZeroRect, self.POINTER, self, None))

    def mouseMoved_(self, event):
        if Host.instance and Host.instance.shown:
            (NSCursor.pointingHandCursor() if self.on_control(event) else NSCursor.arrowCursor()).set()
        objc.super(DragWebView, self).mouseMoved_(event)      # the page's :hover

    def mouseExited_(self, event):
        NSCursor.arrowCursor().set()     # or the hand would stay over whatever is next
        objc.super(DragWebView, self).mouseExited_(event)

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
    """Page messages, handed to the panel (host/panelcore.py; the page's counts and the heatmap it
    switched to go on to the session), and window events. Only this panel's own page can post here,
    so it needs no token the way a WebSocket client does."""

    def userContentController_didReceiveScriptMessage_(self, controller, message):
        # The touch keyboard types only for the page itself: its main frame, loaded from disk.
        frame = message.frameInfo()
        from_page = bool(frame.isMainFrame()) and str(frame.securityOrigin().protocol()) == "file"
        Host.instance.core.page(str(message.body()), from_page)

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

    def windowDidMove_(self, notification):
        if Host.instance and Host.instance.docked:
            return                  # the dock's place is not where the floating HUD goes back to
        f = notification.object().frame()
        save_state({"frame": [f.origin.x, f.origin.y, f.size.width, f.size.height]})

    def screensChanged_(self, notification):
        """A screen came, went or changed (the Dock moved, the resolution): docked, it docks again."""
        if Host.instance and Host.instance.docked:
            Host.instance.redock()

    def applicationWillTerminate_(self, notification):
        """The one way out that still runs Python: stopEventLoop (✕, a signal) ends in
        NSApp.terminate_, which calls exit() rather than returning from the run loop, so code
        after runEventLoop never runs. What the session has counted and not yet written goes now."""
        if Host.instance:
            Host.instance.stop()

    def webView_decidePolicyForNavigationAction_decisionHandler_(self, webview, action, handler):
        """Only the HUD's own pages load in the panel. The page can type (the touch keyboard), so
        whatever is shown there must be it."""
        url = action.request().URL()
        path = str(url.path() or "") if url is not None and url.isFileURL() else ""
        handler(1 if path.startswith(str(PAGES) + "/") else 0)   # WKNavigationActionPolicyAllow : Cancel

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


class OnMain:
    """An object's methods called on the main thread, whoever calls them: remote mode's typing,
    asked for on the remote server's loop and pressed where the panel's own keys are."""

    def __init__(self, obj):
        self.obj = obj

    def __getattr__(self, name):
        fn = getattr(self.obj, name)
        return lambda *a: AppHelper.callAfter(fn, *a)


class Host:
    """Owns the window, the page and the in-process feed; marshals feed messages to the page."""

    instance = None

    def __init__(self):
        Host.instance = self
        self.stopped = False
        self.feed = None
        self.ready = False
        self.loaded = False             # the first page has loaded (page_ready): a later one is a reload
        self.queue = []
        self.standing = {}              # kind -> the latest of it, for a reloaded page
        self.dock_screen = None
        self.page_h = 0                 # how tall the page last said it is
        self.bridge = Bridge.alloc().init()
        # The touch keyboard's keys (host/inject.py): CoreGraphics events, once macOS allows them,
        # repeated at the user's own key-repeat settings while held. Before the page, whose
        # navigation lets its keys go.
        self.typing = inject.Typing(self.make_typist_backend, lambda msg: self.core.tell(msg), AppHelper.callLater,
                                    pages=[inject.KEY_PAGE], log=log)
        # The panel (host/panelcore.py): what the page says and the CLI asks, and panel.json; this
        # window is how it is drawn and placed here.
        self.core = panelcore.Panel(
            panelcore.Window(show=self.show_window, front=lambda: self.panel.orderFrontRegardless(),
                             dock=self.dock_window, resize=self.resize, no_drag=self.set_no_drag,
                             open=self.open_url, tell=self.deliver, typing_access=self.request_typing,
                             close=self.close),
            RUN, self.typing, self.settings, said=self.said, shown=not HIDDEN, log=log)

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
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self.bridge, "screensChanged:", "NSApplicationDidChangeScreenParametersNotification", None)

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
        web.track_pointer()              # the hand over a control, and the page's hover styles
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
        # Counts reach the session through the bridge alone, so the socket gets no tally token. It
        # gets the run's socket token, kept in $STATE/token for `poke` (hudfeed.Hub.process_request).
        self.token = secrets.token_hex(16)
        hub = hudfeed.Hub(on_sent_in=lambda msg: AppHelper.callAfter(self.deliver, msg), token=self.token)
        self.socket = hudfeed.HubThread(hub, int(os.environ.get("ZMKHUD_PORT", "8766")), log=log)

        # Remote mode (host/remote.py): a paired tablet's browser typing here, through keys of its
        # own pressed on the main thread like the page's; its server runs on a loop of its own, and
        # serves nothing until `zmk-layer-hud remote on` asks.
        self.remote_typing = inject.Typing(self.make_typist_backend, lambda msg: self.remote.tell(msg),
                                           AppHelper.callLater, pages=[inject.KEY_PAGE], log=log)
        loop = asyncio.new_event_loop()
        self.remote = remote_mod.Controller(RUN, OnMain(self.remote_typing), log=log, loop=loop)
        self.remote_thread = threading.Thread(target=lambda: loop.run_until_complete(self.remote.run()),
                                              name="remote", daemon=True)
        self.remote_thread.start()

        def emit(msg):
            AppHelper.callAfter(self.deliver, msg)
            self.socket.send(msg)
            self.remote.hub_said(msg)
        self.feed = hudfeed.Feed(emit, log=log).start()
        hub.on_inject, hub.sessions = self.feed.resync, self.feed.sessions
        self.socket.start()
        # Width from the config (hud.width), whether or not its keymap converts; the height follows
        # the page (see resize).
        width = (self.feed.cfg.get("hud") or {}).get("width")
        if width:
            self.resize(int(width), None)
        # Docked from the start when asked (`start --dock`, dock.start), or when it was left docked.
        if panelcore.starts_docked(load_state().get("dock"), self.settings(), os.environ):
            AppHelper.callAfter(self.core.set_dock, True)
        # main() put the signal's handler in first: the CLI signals whatever pid this file names.
        self.core.start()
        panelstate.write_token(RUN, self.token)

    def settings(self):
        """The config's touch:, dock: and remote: (host/keymap.py host_settings), its defaults when
        the config does not load."""
        km = hudfeed.keymap_mod
        try:
            return km.host_settings(self.feed.cfg or {})
        except km.KeymapError as e:
            log(f"touch keyboard: {e}; its defaults instead")
            return km.host_settings({})

    def target_screen(self):
        """The screen to dock on: the one dock.screen names, else the one under the pointer (the HUD
        is never the key window, so the focused screen is not its to know), else the main one."""
        wanted = self.settings()["dock"]["screen"].lower()
        screens = list(NSScreen.screens())
        if wanted:
            for s in screens:
                if wanted in str(s.localizedName()).lower():
                    return s
        at = NSEvent.mouseLocation()
        for s in screens:
            f = s.frame()
            if f.origin.x <= at.x < f.origin.x + f.size.width and f.origin.y <= at.y < f.origin.y + f.size.height:
                return s
        return focused_screen()

    def dock_frame(self):
        """Along the bottom of the dock's screen, above the Dock (visibleFrame leaves it and the menu
        bar out), as wide as the screen and as tall as the page within its share."""
        vf = self.dock_screen.visibleFrame()
        h = dock_mod.height(self.page_h, vf.size.height, self.settings()["dock"]["height_pct"])
        return NSMakeRect(vf.origin.x, vf.origin.y, vf.size.width, h), vf

    def dock_message(self, vf, settings):
        msg = {"kind": "dock", "on": True, "split": bool(settings["dock"]["split"]),
               "max_height": dock_mod.max_height(vf.size.height, settings["dock"]["height_pct"])}
        if settings["touch"]["enabled"]:       # a keyboard: its keys their real size, or no keyboard
            msg.update(px_per_mm=self.px_per_mm(self.dock_screen), width=vf.size.width)
        return msg

    @staticmethod
    def px_per_mm(screen):
        """How many of a screen's points -- the page's CSS pixels -- are a millimetre on it: its size
        as its EDID tells macOS (CGDisplayScreenSize) over its frame, which a scaled screen has fewer
        points in than its panel has pixels."""
        f = screen.frame()
        try:
            wmm, hmm = cgevent.screen_mm(int(screen.deviceDescription()["NSScreenNumber"]))
        except Exception as e:      # no CoreGraphics, no number: measured as CSS measures
            log(f"dock: the screen's size: {type(e).__name__}: {e}")
            wmm = hmm = 0
        ppmm, measured = dock_mod.px_per_mm(f.size.width, f.size.height, wmm, hmm)
        if not measured:
            log(f"dock: {screen.localizedName()} does not say how big it is; its keys drawn at 96 points to the inch")
        return ppmm

    @property
    def shown(self):
        return self.core.shown

    @property
    def docked(self):
        return self.core.docked

    def dock_window(self, on):
        """Docked, the panel is a strip over the bottom of a screen -- an overlay: macOS has no room
        for an app to reserve -- and every click on it is the page's; floating, it goes back where
        it was (panelcore.Panel.set_dock)."""
        msg = None
        if on:
            self.dock_screen = self.target_screen()
            frame, vf = self.dock_frame()
            self.place(frame)
            msg = self.dock_message(vf, self.settings())
            log(f"docked on {self.dock_screen.localizedName()}")
        else:
            self.place(initial_frame())
        save_state({"dock": on})
        return msg

    def redock(self):
        screens = list(NSScreen.screens())
        if self.dock_screen not in screens:
            self.dock_screen = self.target_screen()
        frame, vf = self.dock_frame()
        self.place(frame)
        self.deliver(self.dock_message(vf, self.settings()))     # another screen, or the same one at another size

    def place(self, frame):
        self.panel.setFrame_display_(frame, True)
        self.web.setFrame_(((0, 0), (frame.size.width, frame.size.height)))

    def said(self, msg):
        """A page message for the session: its counts, its heatmap and preferences (session.Store.SAID)."""
        store = self.feed.sessions if self.feed else None
        if store is None:
            return
        try:
            store.apply_said(msg)
        except Exception as e:  # a count that could not be kept must not take the panel down
            log(f"session: {type(e).__name__}: {e}")

    @staticmethod
    def set_no_drag(rects):
        DragWebView.no_drag = rects

    @staticmethod
    def open_url(url):
        NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(url))

    @staticmethod
    def close():
        log("close requested by the page")
        AppHelper.stopEventLoop()

    def request_typing(self):
        """`zmk-layer-hud typing setup`: macOS's prompt, and its Accessibility pane, where the
        switch is. Typing that is waiting for it notices within seconds (inject.Typing)."""
        if cgevent.request_access():
            log("typing: macOS allows this app to post key events")
            return
        NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"))

    @staticmethod
    def make_typist_backend():
        if not cgevent.access():
            raise inject.Unavailable("denied", "macOS has not allowed this app to post key events (Accessibility)")

        def schedule(seconds, fn):
            handle = {"live": True}
            AppHelper.callLater(seconds, lambda: handle["live"] and fn())
            return handle

        def cancel(handle):
            handle["live"] = False
        return cgevent.CGBackend(schedule=schedule, cancel=cancel,
                                 delay=float(NSEvent.keyRepeatDelay()), interval=float(NSEvent.keyRepeatInterval()))

    def show_window(self, shown):
        """Drawn, or drawn as nothing (HIDE_JS); the page goes on as before either way."""
        self.panel.setAlphaValue_(1.0 if shown else 0.0)
        if self.ready:          # otherwise page_ready puts the style in, with the page
            self.web.evaluateJavaScript_completionHandler_(SHOW_JS if shown else HIDE_JS, None)
        if DEBUG and not shown:
            AppHelper.callLater(3.0, self.say_visibility)

    def say_visibility(self):
        """ZMKHUD_DEBUG: what WebKit makes of the hidden HUD. `visible` is the point of hiding it
        this way; `hidden` would mean its timers are being slowed."""
        def said(value, error):
            log(f"hidden: the page is {value}; the window's occlusion state {int(self.panel.occlusionState())}")
        self.web.evaluateJavaScript_completionHandler_("document.visibilityState", said)

    def resize(self, width, height):
        """The page knows how tall the layout is; keep the top-left corner where the user put it.
        Docked, the bottom edge stays on the screen's and the width is the screen's."""
        if self.docked:
            if height:
                self.page_h = float(height)
                self.place(self.dock_frame()[0])
            return
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
    STANDING = ("keymap", "layers", "device", "session", "secure", "touch", "typing", "dock")

    def mouse_down(self, event):
        if event.window() is None or event.window() != self.panel:
            return event
        if self.docked:
            return event            # docked, nothing moves it: every press is the page's
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
        """A reload has begun: until the new page is ready, messages wait for it, and the keys it
        held go up."""
        self.ready = False
        self.typing.release()

    def deliver(self, msg):
        data = json.dumps(msg, ensure_ascii=False)
        if DEBUG and msg["kind"] == "layers":   # never the positions: with the keymap, those are the typing
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
        self.typing.stop()              # keys up before the panel goes
        self.remote.stop()              # no tablet's keys after this
        self.remote_thread.join(timeout=2)
        self.remote_typing.stop()
        self.core.stop()
        panelstate.remove_token(RUN, self.token)
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
        # `zmk-layer-hud show` / `hide`, `dock`, `typing setup`: what they want is in $STATE.
        if Host.instance:
            Host.instance.core.wake()
    MachSignals.signal(panelstate.SIGNAL, asked)
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)  # no Dock icon, no menu bar
    Host()
    # Host.stop runs on the way out from applicationWillTerminate_: this never returns.
    AppHelper.runEventLoop(installInterrupt=False)


if __name__ == "__main__":
    main()
