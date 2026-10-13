#!/usr/bin/env python3
"""zmk-layer-hud -- the accessibility bus watch (host/a11y.py), on buses of the test's own.

The watch runs on its thread as it does in the feed; the test puts the apps' signals on the fake
bus and waits for what the watch says. jeepney's own messages are used throughout, so what is
parsed here is what the real bus would carry."""

import contextlib
import os
import queue
import sys
import threading
import time
import unittest
from collections import deque
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import jeepney  # noqa: F401
    from jeepney import DBusAddress, HeaderFields, new_error, new_method_return, new_signal
    HAVE_JEEPNEY = True
except ImportError:
    HAVE_JEEPNEY = False

import a11y  # noqa: E402

WAIT_S = 3          # for the watch's thread to have taken a signal in


class FakeBus:
    """A bus as the watch sees it: replies to what it is asked, and delivers the signals a test
    puts on it through the watch's own filters, as jeepney does."""

    def __init__(self, roles=None, focused=(), enabled=True, fail=()):
        self.roles = dict(roles or {})          # (sender, path) -> AtspiRole
        self.focused = set(focused)             # (sender, path) that GetState says are focused
        self.enabled = enabled                  # org.a11y.Status IsEnabled
        self.fail = set(fail)                   # members answered with an error
        self.hold = {}                          # member -> an Event the answer waits for (an app that is slow)
        self.inbox = queue.Queue()
        self.filters = []
        self.calls = []
        self.closed = False

    # ---- what the watch asks ----
    def send_and_get_reply(self, msg, *, timeout=None):
        f = msg.header.fields
        member, dest, path = f.get(HeaderFields.member), f.get(HeaderFields.destination), f.get(HeaderFields.path)
        self.calls.append((member, dest, path, msg.body))
        if member in self.hold:
            self.hold[member].wait()
        if member in self.fail:
            return new_error(msg, "org.freedesktop.DBus.Error.Failed", "s", ("no",))
        if member == "GetAddress":
            return new_method_return(msg, "s", ("unix:path=/run/user/1000/at-spi/bus_0",))
        if member == "Get":                     # org.a11y.Status IsEnabled
            return new_method_return(msg, "v", (("b", self.enabled),))
        if member == "Set":
            self.enabled = msg.body[2][1]
            return new_method_return(msg)
        if member == "GetRole":
            return new_method_return(msg, "u", (self.roles.get((dest, path), 0),))
        if member == "GetState":
            bits = (1 << a11y.STATE_FOCUSED) if (dest, path) in self.focused else 0
            return new_method_return(msg, "au", ([bits, 0],))
        return new_method_return(msg)           # RegisterEvent, AddMatch

    # ---- what it listens to ----
    def filter(self, rule, *, queue=None, bufsize=1):
        q = queue if queue is not None else deque(maxlen=bufsize)
        self.filters.append((rule, q))
        return contextlib.nullcontext(q)

    def recv_until_filtered(self, q, *, timeout=None):
        deadline = time.monotonic() + (timeout or 0)
        while not q:
            try:
                self._route(self.inbox.get(timeout=max(0.0, deadline - time.monotonic())))
            except queue.Empty:
                raise TimeoutError
        # What arrived while the watch was asking an app is in the queues already, as jeepney
        # routes what it reads while it waits for a reply.
        while True:
            try:
                self._route(self.inbox.get_nowait())
            except queue.Empty:
                break
        return q.popleft()

    def _route(self, msg):
        for rule, into in self.filters:
            if rule.matches(msg):
                into.append(msg)

    def close(self):
        self.closed = True

    # ---- the apps ----
    def focus(self, sender, path, gained=True):
        msg = new_signal(DBusAddress(path, bus_name=sender, interface=a11y.OBJECT_EVENTS), "StateChanged",
                         "siiva{sv}", ("focused", 1 if gained else 0, 0, ("i", 0), {}))
        msg.header.fields[HeaderFields.sender] = sender
        self.inbox.put(msg)

    def deactivate(self, sender, path="/org/a11y/atspi/accessible/win"):
        msg = new_signal(DBusAddress(path, bus_name=sender, interface=a11y.WINDOW_EVENTS), "Deactivate",
                         "siiva{sv}", ("", 0, 0, ("i", 0), {}))
        msg.header.fields[HeaderFields.sender] = sender
        self.inbox.put(msg)


def settles(what, want, within=WAIT_S):
    """Whether `what()` comes to `want` within the wait."""
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if what() == want:
            return True
        time.sleep(0.01)
    return what() == want


BROWSER, MANAGER = ":1.40", ":1.41"
LOGIN = (BROWSER, "/org/a11y/atspi/accessible/2001")      # a password field in the browser
SEARCH = (BROWSER, "/org/a11y/atspi/accessible/2002")     # a text field beside it
VAULT = (MANAGER, "/org/a11y/atspi/accessible/7")         # the password manager's master password
PASSWORD, TEXT = a11y.ROLE_PASSWORD_TEXT, 61


@unittest.skipUnless(HAVE_JEEPNEY, "jeepney is not in this venv")
class Watch(unittest.TestCase):
    def setUp(self):
        self.logged = []
        self.session = FakeBus()
        self.bus = FakeBus(roles={LOGIN: PASSWORD, SEARCH: TEXT, VAULT: PASSWORD}, focused={LOGIN, VAULT})

    def watch(self, **kw):
        focus = a11y.PasswordFocus(log=self.logged.append, connect=lambda: (self.session, self.bus),
                                   confirm_s=kw.pop("confirm_s", 0.2), retry_s=kw.pop("retry_s", 0.1), **kw)
        self.addCleanup(focus.stop)
        focus.start()
        self.assertTrue(settles(lambda: focus.said, "watching"), self.logged)
        return focus

    def test_a_password_field_with_focus_is_a_secret_until_it_loses_it(self):
        focus = self.watch()
        self.assertFalse(focus.probe())
        self.bus.focus(*LOGIN)
        self.assertTrue(settles(focus.probe, True))
        self.assertIn(("GetRole", BROWSER, LOGIN[1], ()), self.bus.calls)    # the app was asked what it is
        self.bus.focus(*LOGIN, gained=False)
        self.assertTrue(settles(focus.probe, False))

    def test_another_widget_taking_focus_ends_it(self):
        focus = self.watch()
        self.bus.focus(*LOGIN)
        self.assertTrue(settles(focus.probe, True))
        self.bus.focus(*SEARCH)
        self.assertTrue(settles(focus.probe, False))
        self.bus.focus(*VAULT)                   # and a password field in another app begins it again
        self.assertTrue(settles(focus.probe, True))

    def test_the_window_going_away_ends_it(self):
        focus = self.watch()
        self.bus.focus(*LOGIN)
        self.assertTrue(settles(focus.probe, True))
        self.bus.deactivate(MANAGER)             # some other app's window: not this one
        time.sleep(0.05)
        self.assertTrue(focus.probe())
        self.bus.deactivate(BROWSER)
        self.assertTrue(settles(focus.probe, False))

    def test_a_field_that_no_longer_says_it_has_focus_ends_it(self):
        # An event missed (a window closed, a tab gone): the field is asked again once a second.
        focus = self.watch(confirm_s=0.1)
        self.bus.focus(*LOGIN)
        self.assertTrue(settles(focus.probe, True))
        time.sleep(0.3)
        self.assertTrue(focus.probe())           # asked, and it still has focus
        self.assertIn(("GetState", BROWSER, LOGIN[1], ()), self.bus.calls)
        self.bus.focused.discard(LOGIN)
        self.assertTrue(settles(focus.probe, False))

    def test_while_the_app_is_asked_the_answer_is_not_known(self):
        # The keys are held meanwhile (hudfeed.SecureInput): the first typed into a password field
        # would otherwise go on before the app said what the widget is.
        self.bus.hold["GetRole"] = threading.Event()
        focus = self.watch()
        self.bus.focus(*SEARCH)
        self.assertTrue(settles(focus.probe, None))
        self.bus.hold["GetRole"].set()
        self.assertTrue(settles(focus.probe, False))
        self.bus.hold["GetRole"] = threading.Event()
        self.bus.focus(*LOGIN)
        self.assertTrue(settles(focus.probe, None))
        self.bus.hold["GetRole"].set()
        self.assertTrue(settles(focus.probe, True))

    def test_a_flood_of_signals_while_an_app_is_asked_is_said(self):
        # The queue keeps the last QUEUE; what was dropped may have been a focus change.
        self.bus.hold["GetRole"] = threading.Event()
        focus = self.watch()
        self.bus.focus(*SEARCH)
        self.assertTrue(settles(focus.probe, None))
        for _ in range(a11y.QUEUE + 5):
            self.bus.focus(*SEARCH, gained=False)
        self.bus.hold["GetRole"].set()
        self.assertTrue(settles(lambda: focus.said, "overflow"), self.logged)
        self.assertEqual(1, len([line for line in self.logged if "dropped" in line]))
        self.assertFalse(focus.probe())

    def test_a_widget_the_app_will_not_describe_is_no_secret(self):
        self.bus.fail.add("GetRole")
        focus = self.watch()
        self.bus.focus(*LOGIN)
        time.sleep(0.1)
        self.assertFalse(focus.probe())

    def test_the_apps_are_told_assistive_technology_is_wanted_once(self):
        self.session.enabled = False
        self.watch()
        self.assertTrue(self.session.enabled)
        sets = [c for c in self.session.calls if c[0] == "Set"]
        self.assertEqual(1, len(sets))
        self.assertEqual(("org.a11y.Status", "IsEnabled", ("b", True)), sets[0][3])
        self.assertTrue(any("started again" in line for line in self.logged), self.logged)   # and the browser caveat said
        self.logged.clear()
        self.session.calls.clear()

    def test_and_left_alone_when_it_is_already_on_or_asked_not_to(self):
        self.session.enabled = True
        self.watch()
        self.assertFalse([c for c in self.session.calls if c[0] == "Set"])
        self.session = FakeBus(enabled=False)
        self.bus = FakeBus()
        self.watch(enable=False)
        self.assertFalse(self.session.enabled)
        self.assertFalse([c for c in self.session.calls if c[0] in ("Get", "Set")])

    def test_the_registry_and_the_bus_are_asked_for_the_events(self):
        self.watch()
        registered = [c[3][0] for c in self.bus.calls if c[0] == "RegisterEvent"]
        self.assertEqual(list(a11y.EVENTS), registered)
        matches = [c[3][0] for c in self.bus.calls if c[0] == "AddMatch"]
        self.assertEqual(2, len(matches))
        self.assertTrue(all("type='signal'" in m for m in matches), matches)
        self.assertEqual(2, len(self.bus.filters))

    def test_a_bus_that_is_away_is_said_once_and_tried_again(self):
        tries = []

        def connect():
            tries.append(1)
            if len(tries) < 3:
                raise OSError("no session bus")
            return self.session, self.bus
        focus = a11y.PasswordFocus(log=self.logged.append, connect=connect, confirm_s=0.1, retry_s=0.05)
        self.addCleanup(focus.stop)
        focus.start()
        self.assertTrue(settles(lambda: focus.said, "watching"))
        self.assertEqual(3, len(tries))
        away = [line for line in self.logged if "not watching" in line]
        self.assertEqual(1, len(away), self.logged)          # said once, not at every try
        self.assertIn("no session bus", away[0])
        self.assertFalse(focus.probe())

    def test_stopping_closes_the_buses_and_forgets_the_secret(self):
        focus = self.watch()
        self.bus.focus(*LOGIN)
        self.assertTrue(settles(focus.probe, True))
        focus.stop()
        self.assertTrue(settles(lambda: self.bus.closed and self.session.closed, True))
        self.assertFalse(focus.probe())


class Available(unittest.TestCase):
    def test_linux_with_jeepney_only(self):
        with mock.patch.object(a11y, "HAVE_JEEPNEY", True), mock.patch.object(a11y.sys, "platform", "linux"):
            self.assertTrue(a11y.available())
        with mock.patch.object(a11y, "HAVE_JEEPNEY", False), mock.patch.object(a11y.sys, "platform", "linux"):
            self.assertFalse(a11y.available())
        with mock.patch.object(a11y, "HAVE_JEEPNEY", True), mock.patch.object(a11y.sys, "platform", "darwin"):
            self.assertFalse(a11y.available())      # macOS has its switch


if __name__ == "__main__":
    unittest.main()
