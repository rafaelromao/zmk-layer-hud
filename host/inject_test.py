"""host/inject.py: report snapshots from the touch keyboard's page, turned into keys pressed and
released -- in the right order, from more than one source, and let go when a page goes quiet."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import inject  # noqa: E402

A, B, SPACE, POWER, LSHIFT = 0x04, 0x05, 0x2C, 0x66, 0xE1


def hid(mods=0, keys=(), seq=None, **extra):
    m = {"kind": "hid", "v": 1, "mods": mods, "keys": list(keys)}
    if seq is not None:
        m["seq"] = seq
    m.update(extra)
    return m


class Backend:
    def __init__(self):
        self.sent = []

    def send(self, changes):
        self.sent += changes


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class Parse(unittest.TestCase):
    def test_a_snapshot(self):
        self.assertEqual(inject.Report(2, (A, B), ()), inject.parse(hid(2, [A, B])))
        self.assertEqual(inject.parse(hid(2, [A])), inject.parse(json.dumps(hid(2, [A]))))

    def test_modifiers_in_the_keys_are_modifiers(self):
        self.assertEqual(inject.Report(0x02 | 0x08, (A,), ()), inject.parse(hid(0, [LSHIFT, 0xE3, A])))

    def test_power_and_repeats_are_dropped(self):
        self.assertEqual((A,), inject.parse(hid(0, [POWER, A, A])).keys)

    def test_release_lets_everything_go(self):
        self.assertEqual(inject.EMPTY, inject.parse({"kind": "hid", "v": 1, "release": True}))

    def test_what_a_keyboard_could_not_send_is_refused(self):
        for bad in (hid(256, []), hid(0, [300]), hid(0, list(range(4, 15))), hid(0, ["a"]), hid(True, []),
                    {"kind": "key"}, {"kind": "hid", "v": 2}, "x" * 600, "not json", hid(0, [], consumer=[0x1000])):
            with self.assertRaises(inject.Bad, msg=repr(bad)[:60]):
                inject.parse(bad)


class Changes(unittest.TestCase):
    def test_keys_up_then_mods_up_then_mods_down_then_keys_down(self):
        before, after = inject.Report(0x02, (A,), ()), inject.Report(0x01, (B,), ())
        self.assertEqual([(7, A, False), (7, 0xE1, False), (7, 0xE0, True), (7, B, True)],
                         inject.changes(before, after))

    def test_nothing_changed(self):
        r = inject.Report(2, (A,), (0xE9,))
        self.assertEqual([], inject.changes(r, r))

    def test_consumer_keys(self):
        self.assertEqual([(12, 0xE9, True)], inject.changes(inject.EMPTY, inject.Report(0, (), (0xE9,))))


class Typist(unittest.TestCase):
    def setUp(self):
        self.backend, self.clock = Backend(), Clock()
        self.t = inject.Typist(self.backend, clock=self.clock)

    def test_a_tap(self):
        self.t.apply("panel", hid(0, [A], seq=1))
        self.t.apply("panel", hid(0, [], seq=2))
        self.assertEqual([(7, A, True), (7, A, False)], self.backend.sent)

    def test_the_same_snapshot_again_presses_nothing(self):
        self.t.apply("panel", hid(2, [A], seq=1))
        self.t.apply("panel", hid(2, [A], seq=2))
        self.assertEqual([(7, 0xE1, True), (7, A, True)], self.backend.sent)

    def test_an_older_snapshot_is_dropped(self):
        self.t.apply("panel", hid(0, [A], seq=5))
        self.assertFalse(self.t.apply("panel", hid(0, [], seq=4)))
        self.assertTrue(self.t.holding())

    def test_two_sources_are_their_union(self):
        self.t.apply("panel", hid(0, [A]))
        self.t.apply("remote", hid(2, [B]))
        self.t.apply("panel", hid(0, []))
        self.assertEqual(inject.Report(2, (B,), ()), self.t.pressed)
        self.assertNotIn((7, B, False), self.backend.sent)

    def test_a_silent_source_is_let_go_and_a_heartbeat_keeps_it(self):
        self.t.apply("panel", hid(0, [A], seq=1))
        self.clock.now += 1.0
        self.t.apply("panel", hid(0, [A], seq=2))     # said again, as the page does every 500 ms
        self.clock.now += 1.0
        self.assertEqual([], self.t.tick())
        self.clock.now += inject.STALE_S + 0.1
        self.assertEqual(["panel"], self.t.tick())
        self.assertEqual((7, A, False), self.backend.sent[-1])
        self.assertFalse(self.t.holding())

    def test_release_one_or_all(self):
        self.t.apply("panel", hid(0, [A]))
        self.t.apply("remote", hid(0, [B]))
        self.t.release("remote")
        self.assertEqual((A,), self.t.pressed.keys)
        self.t.release()
        self.assertEqual(inject.EMPTY, self.t.pressed)

    def test_too_many_messages_a_second_are_dropped(self):
        t = inject.Typist(self.backend, clock=self.clock, max_rate=3)
        took = [t.apply("panel", hid(0, [A] if i % 2 else [])) for i in range(5)]
        self.assertEqual([True, True, True, False, False], took)
        self.clock.now += 1.1
        self.assertTrue(t.apply("panel", hid(0, [])))

    def test_the_backends_own_tick(self):
        ticks = []
        backend = Backend()
        backend.tick = lambda: ticks.append(1)
        inject.Typist(backend, clock=self.clock).tick()
        self.assertEqual([1], ticks)


class Later:
    def __init__(self):
        self.due = []

    def __call__(self, seconds, fn):
        self.due.append((seconds, fn))

    def run(self):
        due, self.due = self.due, []
        for _, fn in due:
            fn()


class Typing(unittest.TestCase):
    def setUp(self):
        self.told, self.later, self.made = [], Later(), []
        self.refuse = None

    def make(self):
        if self.refuse:
            raise inject.Unavailable(self.refuse)
        b = Backend()
        b.closed = False
        b.close = lambda: setattr(b, "closed", True)
        self.made.append(b)
        return b

    def typing(self):
        return inject.Typing(self.make, self.told.append, self.later, pages=[7])

    def test_started_it_tells_the_page_it_may_type(self):
        t = self.typing()
        t.start()
        self.assertEqual([{"kind": "typing", "state": "ok", "fix": inject.Typing.FIX},
                          {"kind": "touch", "on": True, "sink": {"output": "hid", "repeat": "os", "pages": [7]}}],
                         self.told)
        self.assertTrue(t.hid(hid(0, [A])))
        self.assertEqual([(7, A, True)], self.made[0].sent)
        self.assertFalse(t.hid("garbage"))

    def test_refused_it_says_why_and_tries_again(self):
        self.refuse = "denied"
        t = self.typing()
        t.start()
        self.assertEqual("denied", t.state)
        self.assertEqual({"kind": "touch", "on": False}, self.told[-1])
        self.assertFalse(t.hid(hid(0, [A])))
        self.refuse = None
        self.later.run()                       # the retry, now granted
        self.assertEqual("ok", t.state)
        self.assertTrue(self.told[-1]["on"])

    def test_stopped_it_lets_go_closes_and_says_so(self):
        t = self.typing()
        t.start()
        t.hid(hid(0, [A]))
        t.stop()
        self.assertEqual((7, A, False), self.made[0].sent[-1])
        self.assertTrue(self.made[0].closed)
        self.assertEqual({"kind": "touch", "on": False}, self.told[-1])
        self.later.run()                       # a tick from before the stop does nothing
        self.assertEqual(1, len(self.made))


if __name__ == "__main__":
    unittest.main()
