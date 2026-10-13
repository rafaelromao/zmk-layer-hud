"""The accessibility bus (AT-SPI2): whether a password field has focus, on Linux.

Linux has no switch like macOS's secure input. What it has is the accessibility bus, where every
toolkit that exposes its widgets to assistive technology -- GTK, Qt, Chromium and the browsers and
Electron apps built on it, Firefox -- says which widget gained focus and what it is. A password
field is `password text` (AtspiRole, atspi-constants.h): a browser's login form, a password
manager's, polkit's dialog alike. This watches the bus the way a screen reader does. It asks the
registry for `object:state-changed:focused` and `window:deactivate`, and when the widget that
gained focus is a password field, says a secret is being typed until that widget loses focus,
another gains it, its window deactivates, or the widget no longer says it is focused when asked
again (once a second, in case an event was missed). host/hudfeed.py SecureInput asks `probe` at
every keystroke, and the feed then passes on nothing typed. While the app is being asked what
the widget that just gained focus is -- a round trip, and up to ASK_S for one that hangs --
`probe` says it does not know yet (None), and the feed holds the keys without telling the page:
the first keys typed into a password field would otherwise go on before the answer came.

A field that had focus before the watch looked -- the feed started, or the bus came back, with a
login form already up; a window brought back to the front with its field still focused, which
some apps say with `window:activate` and no new focus event -- is looked for: the active window
is asked for a focused password field (org.a11y.atspi.Collection, which at-spi2-atk's apps and
Qt answer), and where the app has no Collection (GTK4) its tree is walked a bounded way.

The apps expose their widgets only while the bus says assistive technology is wanted: the
org.a11y.Status IsEnabled property on the session bus, which a screen reader sets when it starts
and `gsettings set org.gnome.desktop.interface toolkit-accessibility true` sets by hand. This sets
it the same way when it finds it off (feed.accessibility: 1), and says so in the log, because
Chromium and Firefox read it when they start: a browser running since before shows nothing until
it is started again. GTK and Qt apps follow it live. The cost is each app keeping an accessibility
tree, which is what a screen reader asks of them.

jeepney (pure Python, host/requirements.txt) speaks D-Bus; nothing of GLib or pyatspi. Where it is
missing, or on macOS, there is no watch (available).
"""

import sys
import threading
import time
from collections import deque

try:
    from jeepney import DBusAddress, HeaderFields, MatchRule, MessageType, Properties, message_bus, new_method_call
    from jeepney.io.blocking import open_dbus_connection
    HAVE_JEEPNEY = True
except ImportError:         # macOS, or a venv from before the pin
    HAVE_JEEPNEY = False

ROLE_PASSWORD_TEXT = 40     # AtspiRole ATSPI_ROLE_PASSWORD_TEXT
STATE_ACTIVE = 1            # AtspiStateType ATSPI_STATE_ACTIVE: the window in front
STATE_FOCUSED = 12          # AtspiStateType ATSPI_STATE_FOCUSED: a bit of the first word GetState returns
BUS_PATH, BUS_NAME = "/org/a11y/bus", "org.a11y.Bus"                      # on the session bus
REGISTRY_PATH, REGISTRY_NAME = "/org/a11y/atspi/registry", "org.a11y.atspi.Registry"
ROOT_PATH = "/org/a11y/atspi/accessible/root"                             # the desktop, whose children are the apps
OBJECT_EVENTS, WINDOW_EVENTS = "org.a11y.atspi.Event.Object", "org.a11y.atspi.Event.Window"
EVENTS = ("object:state-changed:focused", "window:deactivate", "window:activate")
ACCESSIBLE = "org.a11y.atspi.Accessible"
COLLECTION = "org.a11y.atspi.Collection"
MATCH_ALL, MATCH_ANY, SORT_CANONICAL = 1, 2, 1     # AtspiCollectionMatchType, AtspiCollectionSortOrder (atspi-constants.h)
WALK, WALK_DEPTH = 256, 8   # a tree walked where the app has no Collection: calls in all, and levels down
TIMEOUT_S = 5               # for the bus and the registry
ASK_S = 2                   # for an app asked what a widget is: one that hangs must not hold the watch
RETRY_S = 10                # after the bus could not be reached, or went away
CONFIRM_S = 1.0             # while a field is said to have focus, how often it is asked again
QUEUE = 64                  # signals kept while an app is being asked


def available():
    """Where the watch can run: Linux, with jeepney in the venv. Whether the bus is there is found
    out by trying, and said in the log."""
    return sys.platform == "linux" and HAVE_JEEPNEY


def returned(reply):
    """The body of a method's reply, or an error for what the other side said instead."""
    if reply.header.message_type != MessageType.method_return:
        name = reply.header.fields.get(HeaderFields.error_name, "?")
        raise RuntimeError(f"{name}: {reply.body[0] if reply.body else ''}")
    return reply.body


def connect_buses():
    """The session bus, and the accessibility bus it names (org.a11y.Bus GetAddress), connected."""
    session = open_dbus_connection(bus="SESSION")
    try:
        ask = new_method_call(DBusAddress(BUS_PATH, bus_name=BUS_NAME, interface="org.a11y.Bus"), "GetAddress")
        bus = open_dbus_connection(bus=returned(session.send_and_get_reply(ask, timeout=TIMEOUT_S))[0])
    except BaseException:
        session.close()
        raise
    return session, bus


def is_enabled(session):
    """Whether the apps are told assistive technology is wanted (org.a11y.Status IsEnabled)."""
    status = Properties(DBusAddress(BUS_PATH, bus_name=BUS_NAME, interface="org.a11y.Status"))
    return bool(returned(session.send_and_get_reply(status.get("IsEnabled"), timeout=TIMEOUT_S))[0][1])


class PasswordFocus:
    """Whether a password field has focus, kept up from the accessibility bus on a thread of its
    own. `probe()` is what SecureInput asks: True while one has, None while the app is still being
    asked what the widget that just gained focus is, else False. `connect` is connect_buses, or a
    test's own buses."""

    def __init__(self, log=print, enable=True, connect=None, confirm_s=CONFIRM_S, retry_s=RETRY_S):
        self.log, self.enable, self.confirm_s, self.retry_s = log, enable, confirm_s, retry_s
        self.connect = connect or connect_buses
        self.secret = False
        self.asking = False         # a widget gained focus and the app has not yet said what it is
        self.said = None            # the last thing logged, so a bus that stays away is said once
        self._stop = threading.Event()
        self._thread = None

    def probe(self):
        if self.secret:
            return True
        return None if self.asking else False

    def start(self):
        self._thread = threading.Thread(target=self._run, name="a11y-focus", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def _say(self, key, text):
        if self.said != key:
            self.said = key
            self.log(text)

    def _run(self):
        while not self._stop.is_set():
            try:
                self._watch()
            except Exception as e:
                self._say("away", f"a11y: not watching the accessibility bus: {type(e).__name__}: {e} "
                                  f"(again in {self.retry_s} s; a password field in a window is not seen until then)")
            self.secret, self.asking = False, False
            self._stop.wait(self.retry_s)

    def _watch(self):
        session, bus = self.connect()
        try:
            if self.enable:
                self._enable(session)
            self._register(bus)
            signals = deque(maxlen=QUEUE)
            with bus.filter(MatchRule(type="signal", interface=OBJECT_EVENTS, member="StateChanged"), queue=signals), \
                 bus.filter(MatchRule(type="signal", interface=WINDOW_EVENTS), queue=signals):
                self._say("watching", "a11y: watching the accessibility bus for a password field with focus")
                # One may have focus already: a login form up before the feed started, or before
                # the bus came back. Held back while it is looked for, as while an app is asked.
                self.asking = True
                field = self._find_active(bus)        # (sender, path) of the password field that has focus
                self.secret = field is not None
                self.asking = False
                asked = time.monotonic()
                while not self._stop.is_set():
                    try:
                        msg = bus.recv_until_filtered(signals, timeout=self.confirm_s)
                    except TimeoutError:
                        msg = None
                    if msg is not None:
                        field = self._on_signal(bus, msg, field)
                        # Known now: said before `asking` is let go, so there is no moment with
                        # both false and a widget still unresolved.
                        self.secret = field is not None
                        self.asking = False
                        if len(signals) >= QUEUE - 1:
                            self._overflow()
                            if field is None:          # a focus change may have been dropped: look
                                self.asking = True
                                field = self._find_active(bus)
                                self.secret = field is not None
                                self.asking = False
                    if field is not None and time.monotonic() - asked >= self.confirm_s:
                        asked = time.monotonic()
                        if not self._focused(bus, *field):
                            field = None
                    self.secret = field is not None
        finally:
            self.secret, self.asking = False, False
            for c in (bus, session):
                try:
                    c.close()
                except Exception:
                    pass

    def _enable(self, session):
        """Tell the apps assistive technology is wanted, when nothing has yet."""
        if is_enabled(session):
            return
        status = Properties(DBusAddress(BUS_PATH, bus_name=BUS_NAME, interface="org.a11y.Status"))
        returned(session.send_and_get_reply(status.set("IsEnabled", "b", True), timeout=TIMEOUT_S))
        self.log("a11y: told the apps that assistive technology is wanted (org.a11y.Status IsEnabled), which is "
                 "how a password field with focus is seen. Chromium and Firefox read that when they start: "
                 "one running since before shows nothing until it is started again. feed.accessibility: 0 "
                 "leaves the setting alone.")

    def _register(self, bus):
        """The registry tells the apps which events have a listener; the bus, which to deliver here."""
        registry = DBusAddress(REGISTRY_PATH, bus_name=REGISTRY_NAME, interface="org.a11y.atspi.Registry")
        for event in EVENTS:
            returned(bus.send_and_get_reply(new_method_call(registry, "RegisterEvent", "s", (event,)), timeout=TIMEOUT_S))
        for rule in (MatchRule(type="signal", interface=OBJECT_EVENTS, member="StateChanged"),
                     MatchRule(type="signal", interface=WINDOW_EVENTS)):          # Activate and Deactivate
            returned(bus.send_and_get_reply(message_bus.AddMatch(rule), timeout=TIMEOUT_S))

    def _on_signal(self, bus, msg, field):
        """The password field with focus after this signal: the one that just gained it, the one
        from before if nothing says otherwise, or none."""
        fields = msg.header.fields
        interface, member = fields.get(HeaderFields.interface), fields.get(HeaderFields.member)
        sender, path = fields.get(HeaderFields.sender), fields.get(HeaderFields.path)
        if interface == OBJECT_EVENTS and member == "StateChanged":
            detail, gained = msg.body[0], msg.body[1]       # (siiva{sv}): the state, 1 gained / 0 lost
            if detail != "focused":
                return field
            if gained:
                self.asking = True          # held back until the app says; cleared by the watch
                return (sender, path) if self._role(bus, sender, path) == ROLE_PASSWORD_TEXT else None
            return None if field == (sender, path) else field
        if interface == WINDOW_EVENTS and member == "Deactivate":
            return None if field is not None and field[0] == sender else field
        if interface == WINDOW_EVENTS and member == "Activate" and field is None:
            # Brought to the front again with its field still focused: some apps send no new
            # focus event for that, so the window is asked.
            self.asking = True
            return self._find(bus, sender, path)
        return field

    def _overflow(self):
        """The signals' queue filled while an app was being asked: the oldest were dropped, and a
        focus change may have gone with them."""
        self._say("overflow", f"a11y: more than {QUEUE} signals arrived while an app was being asked what a "
                              "widget is; the oldest were dropped, and a password field with focus may be missed "
                              "until the next focus change")

    def _find(self, bus, sender, window):
        """A focused password field in `window`, as (sender, path), or None. The app's Collection
        is asked first (one call); where it has none, the tree is walked within WALK calls and
        WALK_DEPTH levels, and a timeout ends the walk with nothing found."""
        rule = ([1 << STATE_FOCUSED, 0], MATCH_ALL, {}, MATCH_ALL,
                [0, 1 << (ROLE_PASSWORD_TEXT - 32), 0, 0], MATCH_ANY, [], MATCH_ALL, False)
        try:
            ask = new_method_call(DBusAddress(window, bus_name=sender, interface=COLLECTION), "GetMatches",
                                  "(aiia{ss}iaiiasib)uib", (rule, SORT_CANONICAL, 1, True))
            found = returned(bus.send_and_get_reply(ask, timeout=ASK_S))[0]
            return (found[0][0], found[0][1]) if found else None
        except Exception:
            pass                                    # no Collection (GTK4), or no answer: walk
        calls, queue = 0, deque([(sender, window, 0)])
        try:
            while queue and calls < WALK:
                s, p, depth = queue.popleft()
                ask = new_method_call(DBusAddress(p, bus_name=s, interface=ACCESSIBLE), "GetChildren")
                calls += 1
                for cs, cp in returned(bus.send_and_get_reply(ask, timeout=ASK_S))[0]:
                    if calls >= WALK:
                        return None
                    calls += 1
                    if self._state(bus, cs, cp) & (1 << STATE_FOCUSED):
                        return (cs, cp) if self._role(bus, cs, cp) == ROLE_PASSWORD_TEXT else None
                    if depth + 1 < WALK_DEPTH:
                        queue.append((cs, cp, depth + 1))
        except Exception:
            return None
        return None

    def _find_active(self, bus):
        """A focused password field in the window in front, looked for through the registry's
        desktop: its children are the apps, theirs the windows, and the active one is asked."""
        try:
            ask = new_method_call(DBusAddress(ROOT_PATH, bus_name=REGISTRY_NAME, interface=ACCESSIBLE), "GetChildren")
            apps = returned(bus.send_and_get_reply(ask, timeout=ASK_S))[0]
            for app, root in apps:
                ask = new_method_call(DBusAddress(root, bus_name=app, interface=ACCESSIBLE), "GetChildren")
                try:
                    windows = returned(bus.send_and_get_reply(ask, timeout=ASK_S))[0]
                except Exception:
                    continue                        # an app that is gone, or will not say
                for ws, wp in windows:
                    if self._state(bus, ws, wp) & (1 << STATE_ACTIVE):
                        return self._find(bus, ws, wp)
        except Exception:
            return None
        return None

    def _state(self, bus, sender, path):
        """The first word of the widget's states, or 0 for one that will not say."""
        try:
            ask = new_method_call(DBusAddress(path, bus_name=sender, interface=ACCESSIBLE), "GetState")
            return returned(bus.send_and_get_reply(ask, timeout=ASK_S))[0][0]
        except Exception:
            return 0

    def _role(self, bus, sender, path):
        """What the widget is, by the app that owns it; None when it will not say."""
        try:
            ask = new_method_call(DBusAddress(path, bus_name=sender, interface=ACCESSIBLE), "GetRole")
            return returned(bus.send_and_get_reply(ask, timeout=ASK_S))[0]
        except Exception:
            return None

    def _focused(self, bus, sender, path):
        """Whether the widget still says it has focus; a widget that is gone says no."""
        try:
            ask = new_method_call(DBusAddress(path, bus_name=sender, interface=ACCESSIBLE), "GetState")
            states = returned(bus.send_and_get_reply(ask, timeout=ASK_S))[0]       # au: two words of bits
            return bool(states[0] & (1 << STATE_FOCUSED))
        except Exception:
            return False
