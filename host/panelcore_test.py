"""The panel (host/panelcore.py) through its interface: page messages and the command line's asks in,
what it tells its window and what panel.json then says out. A fake window stands in for AppKit's and
GTK's, which the two adapters (host/macos/panel.py, host/linux/panel.py) are."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import panelcore  # noqa: E402
import panelstate  # noqa: E402

SETTINGS = {"touch": {"enabled": 1}, "dock": {"screen": "", "height_pct": 60, "start": 0, "split": 1}}
REFUSAL = {"kind": "dock", "fits": False, "key_mm": 19, "need_mm": [300, 100], "room_mm": [200, 150]}
DOCK_MSG = {"kind": "dock", "on": True, "split": True, "max_height": 600, "px_per_mm": 3.8, "width": 1600}


class FakeWindow:
    def __init__(self, dockable=True):
        self.calls, self.dockable = [], dockable

    def port(self):
        def rec(name, answer=None):
            def call(*args):
                self.calls.append((name,) + args)
                return answer() if answer else None
            return call
        return panelcore.Window(
            show=rec("show"), front=rec("front"), resize=rec("resize"), no_drag=rec("no_drag"),
            open=rec("open"), tell=rec("tell"), typing_access=rec("typing_access"), close=rec("close"), let_go=rec("let_go"),
            dock=rec("dock", lambda: dict(DOCK_MSG) if self.dockable and self.calls[-1][1] else None))

    def named(self, name):
        return [c[1:] for c in self.calls if c[0] == name]


class FakeTyping:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a: self.calls.append((name,) + a)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = self.tmp.name
        self.window, self.typing, self.said, self.logged = FakeWindow(), FakeTyping(), [], []
        self.settings = json.loads(json.dumps(SETTINGS))
        self.panel = panelcore.Panel(self.window.port(), self.run_dir, self.typing, lambda: self.settings,
                                     said=self.said.append, log=self.logged.append, clock=lambda: 1000.0)
        self.panel.start()

    def tearDown(self):
        self.tmp.cleanup()

    def state(self):
        return panelstate.read(self.run_dir)


class PageMessages(Base):
    def test_hide(self):
        self.panel.page('{"kind": "hide"}')
        self.assertEqual([(False,)], self.window.named("show"))
        self.assertIn(("release",), self.typing.calls)          # a hidden HUD holds no key down
        self.assertFalse(self.state()["shown"])

    def test_the_site(self):
        self.panel.page({"kind": "site"})
        self.assertEqual([(panelstate.SITE,)], self.window.named("open"))

    def test_wpm_and_stats_reach_panel_json(self):
        self.panel.page({"kind": "wpm", "wpm": 72})
        self.panel.page({"kind": "wpm", "wpm": "fast"})
        self.panel.page({"kind": "stats", "avg": 60, "top": 90, "session": "work", "rows": [["WPM", "72"]]})
        st = self.state()
        self.assertEqual((72, 60, 90, "work", [["WPM", "72"]]), (st["wpm"], st["avg_wpm"], st["top_wpm"], st["session"], st["stats"]))

    def test_no_drag_rects_are_checked(self):
        self.panel.page({"kind": "nodrag", "rects": [[1, 2, 3, 4], [1, 2, 3], "x", [1, 2, 3, "4"], [0, 0.5, 10, 20]]})
        self.assertEqual([([(1.0, 2.0, 3.0, 4.0), (0.0, 0.5, 10.0, 20.0)],)], self.window.named("no_drag"))
        self.panel.page({"kind": "nodrag", "rects": "all"})
        self.assertEqual(1, len(self.window.named("no_drag")))

    def test_hiding_lets_the_page_go_of_its_keys_and_takes_none_while_hidden(self):
        # The page sends its report again every 500 ms: a hidden HUD whose page still held a locked
        # modifier would press it again, unseen, system-wide (touch-keyboard 86d97d9).
        msg = {"kind": "hid", "page": 7, "id": 4, "down": True}
        self.panel.page({"kind": "hide"})
        self.assertEqual([()], self.window.named("let_go"))
        self.assertLess(self.typing.calls.index(("release",)), len(self.typing.calls))
        self.panel.page(msg)
        self.assertNotIn(("hid", msg), self.typing.calls)
        self.panel.set_shown(True)
        self.panel.page(msg)
        self.assertIn(("hid", msg), self.typing.calls)

    def test_the_touch_keyboards_keys_only_from_the_page(self):
        msg = {"kind": "hid", "page": 7, "id": 4, "down": True}
        self.panel.page(msg, from_page=False)
        self.panel.page(msg)
        self.assertEqual([("hid", msg)], [c for c in self.typing.calls if c[0] == "hid"])

    def test_size_goes_to_the_window(self):
        self.panel.page({"kind": "size", "width": 600, "height": 420})
        self.assertEqual([(600, 420)], self.window.named("resize"))

    def test_a_size_no_layout_has_is_not_taken(self):
        # The page's layout, 100..4000 wide and 50..4000 tall: a page mid-load or gone wrong says 0, or
        # nonsense, and the panel would shrink to nothing or grow off every screen.
        for w, h in [(600, 20), (600, 9000), (50, 420), (5000, 420), (600, None), ("wide", 420), (True, 420), (600, "tall")]:
            self.panel.page({"kind": "size", "width": w, "height": h})
        self.panel.page({"kind": "size", "height": 420})            # the width it has
        self.assertEqual([(None, 420)], self.window.named("resize"))

    def test_docked_and_minimized_it_is_a_bar(self):
        self.panel.page({"kind": "size", "width": 600, "height": 30})
        self.panel.set_dock(True)
        self.panel.page({"kind": "size", "width": 600, "height": 30})
        self.panel.page({"kind": "size", "width": 600, "height": 29})
        self.assertEqual([(600, 30)], self.window.named("resize"))

    def test_close(self):
        self.panel.page("close")
        self.assertEqual([()], self.window.named("close"))

    def test_what_is_the_sessions_goes_to_it(self):
        self.panel.page({"kind": "tally", "seq": 1})
        self.panel.page({"kind": "heatmap", "mode": "off"})
        self.assertEqual(["tally", "heatmap"], [m["kind"] for m in self.said])

    def test_what_is_not_a_message_is_nothing(self):
        for body in ("", "{", "[1]", "null", '{"kind": "nonsense"}', 5):
            self.panel.page(body)
        self.assertEqual([], self.window.calls)
        self.assertEqual(1, len(self.said))                     # the kind it does not know, offered to the session


class Docking(Base):
    def test_attach_docks_and_types(self):
        self.panel.page({"kind": "attach", "on": True})
        self.assertTrue(self.panel.docked)
        self.assertEqual([(True,)], self.window.named("dock"))
        self.assertEqual([(DOCK_MSG,)], self.window.named("tell"))
        self.assertIn(("start",), self.typing.calls)
        st = self.state()
        self.assertEqual((True, 1000.0), (st["dock"], st["dock_at"]))

    def test_docked_already_is_nothing(self):
        self.panel.set_dock(True)
        self.panel.page({"kind": "attach", "on": True})
        self.assertEqual(1, len(self.window.named("dock")))

    def test_without_the_touch_keyboard_it_docks_as_a_hud(self):
        self.settings["touch"]["enabled"] = 0
        self.panel.set_dock(True)
        self.assertNotIn(("start",), self.typing.calls)

    def test_nowhere_to_dock_leaves_it_floating(self):
        self.window.dockable = False
        self.panel.set_dock(True)
        self.assertFalse(self.panel.docked)
        self.assertEqual([], self.window.named("tell"))
        self.assertFalse(self.state()["dock"])

    def test_detach_floats_again_and_stops_typing(self):
        self.panel.set_dock(True)
        self.panel.page({"kind": "attach", "on": False})
        self.assertFalse(self.panel.docked)
        self.assertEqual([(True,), (False,)], self.window.named("dock"))
        self.assertEqual((({"kind": "dock", "on": False}),), self.window.named("tell")[-1])
        self.assertIn(("stop",), self.typing.calls)

    def test_attach_wants_a_yes_or_no(self):
        self.panel.page({"kind": "attach", "on": "yes"})
        self.assertEqual([], self.window.named("dock"))

    def test_a_board_too_big_floats_and_says_why(self):
        self.panel.set_dock(True)
        self.panel.page(REFUSAL)
        self.assertFalse(self.panel.docked)
        self.assertIn("300 x 100 mm", self.state()["dock_note"])
        self.assertTrue(self.logged)

    def test_a_refusal_while_floating_is_nothing(self):
        self.panel.page(REFUSAL)
        self.assertEqual([], self.window.named("dock"))


class Typing(Base):
    def test_the_typing_state_is_told_and_kept(self):
        self.panel.tell({"kind": "typing", "state": "denied"})
        self.assertEqual([({"kind": "typing", "state": "denied"},)], self.window.named("tell"))
        self.assertEqual("denied", self.state()["typing"])


class CommandLine(Base):
    def test_show_and_hide(self):
        panelstate.ask(self.run_dir, False)
        self.panel.wake()
        self.assertFalse(self.state()["shown"])
        panelstate.ask(self.run_dir, True)
        self.panel.wake()
        self.assertEqual([(False,), (True,)], self.window.named("show"))
        self.assertEqual([()], self.window.named("front"))     # asked to show: brought back, whatever it was
        self.assertTrue(self.state()["shown"])

    def test_dock_is_taken_once(self):
        panelstate.request(self.run_dir, "dock", True)
        self.panel.wake()
        self.panel.wake()
        self.assertEqual([(True,)], self.window.named("dock"))

    def test_typing_setup(self):
        panelstate.request(self.run_dir, "typing", "request")
        self.panel.wake()
        self.panel.wake()
        self.assertEqual([()], self.window.named("typing_access"))

    def test_nothing_asked_is_nothing(self):
        self.panel.wake()
        self.assertEqual([], self.window.calls)


class StartAndStop(Base):
    def test_panel_json_is_there_while_it_runs(self):
        self.assertTrue(self.state()["shown"])
        self.panel.stop()
        self.assertIsNone(self.state())

    def test_starts_docked(self):
        self.assertFalse(panelcore.starts_docked(False, SETTINGS, {}))
        self.assertTrue(panelcore.starts_docked(True, SETTINGS, {}))
        self.assertTrue(panelcore.starts_docked(False, SETTINGS, {"ZMKHUD_DOCK": "1"}))
        self.assertTrue(panelcore.starts_docked(False, dict(SETTINGS, dock=dict(SETTINGS["dock"], start=1)), {}))


class Adapters(unittest.TestCase):
    def test_both_panels_compile(self):
        # Neither imports here (pyobjc, GTK): compiled, at least, as CI does for Linux only.
        host = os.path.dirname(os.path.abspath(__file__))
        for path in ("macos/panel.py", "linux/panel.py"):
            with open(os.path.join(host, path), encoding="utf-8") as f:
                compile(f.read(), path, "exec")


if __name__ == "__main__":
    unittest.main()
