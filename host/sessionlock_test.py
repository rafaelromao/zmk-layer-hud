#!/usr/bin/env python3
"""zmk-layer-hud -- the session lock probe (host/sessionlock.py), against a Hyprland of the
test's own: a socket that answers `j/monitors` with whatever the test says, and a /proc."""

import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sessionlock  # noqa: E402


class FakeHyprland:
    """Hyprland's control socket: one request per connection, answered and closed."""

    def __init__(self, reply):
        self.reply = reply                      # a list (as JSON), bytes, or a callable giving either
        self.requests = []
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, ".socket.sock")
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.server.bind(self.path)
        except PermissionError:                 # a sandbox that lets no socket be bound: the wire is not tried here
            self.server.close()
            self.dir.cleanup()
            raise unittest.SkipTest("this process may not bind a socket")
        self.server.listen(4)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            with conn:
                self.requests.append(conn.recv(4096))
                reply = self.reply() if callable(self.reply) else self.reply
                conn.sendall(reply if isinstance(reply, bytes) else json.dumps(reply).encode("utf-8"))

    def close(self):
        self.server.close()
        self.dir.cleanup()


class Locked(unittest.TestCase):
    """What Hyprland's answer means, with `monitors` standing in for the wire."""

    def locked(self, answer):
        with mock.patch.object(sessionlock, "monitors", return_value=answer):
            return sessionlock.locked("/a/socket")

    def test_a_lock_on_a_monitor(self):
        self.assertTrue(self.locked([{"name": "eDP-1", "solitaryBlockedBy": ["LOCK"]}, {"name": "DP-1", "solitaryBlockedBy": []}]))

    def test_no_lock(self):
        self.assertFalse(self.locked([{"solitaryBlockedBy": ["WORKSPACE"]}]))
        self.assertFalse(self.locked([{"solitaryBlockedBy": []}]))

    def test_a_hyprland_that_does_not_say(self):
        self.assertIsNone(self.locked([{"name": "eDP-1"}]))      # older: no such key
        self.assertIsNone(self.locked([]))
        self.assertIsNone(self.locked([{"solitaryBlockedBy": "LOCK"}]))     # not the shape it has

    def test_no_answer(self):
        with mock.patch.object(sessionlock, "monitors", side_effect=OSError("no")):
            self.assertIsNone(sessionlock.locked("/a/socket"))
        with mock.patch.object(sessionlock, "monitors", side_effect=ValueError("not json")):
            self.assertIsNone(sessionlock.locked("/a/socket"))


class Wire(unittest.TestCase):
    """The control socket itself: one request per connection, read to the end."""

    def test_monitors_are_asked_and_read_whole(self):
        h = FakeHyprland([{"name": "eDP-1", "solitaryBlockedBy": ["LOCK"]}] * 400)    # more than one recv
        self.addCleanup(h.close)
        self.assertEqual(400, len(sessionlock.monitors(h.path)))
        self.assertEqual([b"j/monitors"], h.requests)
        self.assertTrue(sessionlock.locked(h.path))
        h.reply = b"not json"
        self.assertIsNone(sessionlock.locked(h.path))

    def test_nothing_listening(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(sessionlock.locked(os.path.join(d, ".socket.sock")))


class Lockers(unittest.TestCase):
    def proc(self, **comms):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        for pid, comm in comms.items():
            os.makedirs(os.path.join(d.name, pid))
            with open(os.path.join(d.name, pid, "comm"), "w") as f:
                f.write(comm + "\n")
        os.makedirs(os.path.join(d.name, "self"))         # not a pid
        return d.name

    def test_a_locker_by_name(self):
        self.assertTrue(sessionlock.locker_running(proc=self.proc(**{"123": "vim", "124": "hyprlock"})))
        self.assertFalse(sessionlock.locker_running(proc=self.proc(**{"123": "vim"})))
        self.assertFalse(sessionlock.locker_running(proc=self.proc()))

    def test_another_users_process_is_not_looked_at(self):
        self.assertFalse(sessionlock.locker_running(proc=self.proc(**{"124": "hyprlock"}), uid=os.getuid() + 1))

    def test_no_proc_at_all(self):
        self.assertFalse(sessionlock.locker_running(proc="/nowhere/proc"))


class Probe(unittest.TestCase):
    def test_the_answer_is_kept_for_a_moment(self):
        answers, asked = [[{"solitaryBlockedBy": ["LOCK"]}]], []
        clock = [100.0]

        def monitors(path, timeout_s=None):
            asked.append(path)
            return answers[0]
        with mock.patch.object(sessionlock, "monitors", monitors):
            probe = sessionlock.SessionLock("/a/socket", keep_s=0.1, clock=lambda: clock[0]).probe
            self.assertTrue(probe())
            self.assertTrue(probe())
            self.assertEqual(1, len(asked))                  # kept, not asked again
            clock[0] += 0.2
            answers[0] = [{"solitaryBlockedBy": []}]
            self.assertFalse(probe())
            self.assertEqual(2, len(asked))

    def test_where_hyprland_does_not_say_the_lockers_are_looked_for_and_it_is_said_once(self):
        proc = tempfile.TemporaryDirectory()
        self.addCleanup(proc.cleanup)
        logged = []
        with mock.patch.object(sessionlock, "monitors", return_value=[{"name": "eDP-1"}]):
            lock = sessionlock.SessionLock("/a/socket", log=logged.append, keep_s=0, proc=proc.name)
            self.assertFalse(lock.probe())
            os.makedirs(os.path.join(proc.name, "77"))
            with open(os.path.join(proc.name, "77", "comm"), "w") as f:
                f.write("swaylock\n")
            self.assertTrue(lock.probe())
        self.assertEqual(1, len(logged), logged)
        self.assertIn("by name", logged[0])

    def test_no_hyprland_is_no_probe(self):
        self.assertIsNone(sessionlock.socket_path({}))
        self.assertIsNone(sessionlock.probe(environ={}))
        with tempfile.TemporaryDirectory() as runtime:
            env = {"HYPRLAND_INSTANCE_SIGNATURE": "sig", "XDG_RUNTIME_DIR": runtime}
            self.assertIsNone(sessionlock.socket_path(env))     # the signature alone, no socket
            os.makedirs(os.path.join(runtime, "hypr", "sig"))
            path = os.path.join(runtime, "hypr", "sig", ".socket.sock")
            with open(path, "w"):
                pass
            self.assertEqual(path, sessionlock.socket_path(env))
            self.assertEqual(sys.platform == "linux", sessionlock.available(env))


if __name__ == "__main__":
    unittest.main()
