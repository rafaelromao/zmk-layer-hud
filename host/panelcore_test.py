"""The panel (host/panelcore.py) through its interface: page messages and the command line's asks in,
what it tells its window and what panel.json then says out. A fake window stands in for AppKit's and
GTK's, which the two adapters (host/macos/panel.py, host/linux/panel.py) are."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import panelcore  # noqa: E402
import panelstate  # noqa: E402


class FakeWindow:
    def __init__(self):
        self.calls = []
        self.room = None            # how wide the HUD may be; None: the window cannot say

    def port(self):
        def rec(name):
            return lambda *args: self.calls.append((name,) + args)
        return panelcore.Window(show=rec("show"), front=rec("front"), resize=rec("resize"), room=lambda: self.room,
                                no_drag=rec("no_drag"), open=rec("open"), close=rec("close"))

    def named(self, name):
        return [c[1:] for c in self.calls if c[0] == name]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = self.tmp.name
        self.window, self.said = FakeWindow(), []
        self.panel = panelcore.Panel(self.window.port(), self.run_dir, said=self.said.append)
        self.panel.start()

    def tearDown(self):
        self.tmp.cleanup()

    def state(self):
        return panelstate.read(self.run_dir)


class PageMessages(Base):
    def test_hide(self):
        self.panel.page('{"kind": "hide"}')
        self.assertEqual([(False,)], self.window.named("show"))
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

    def test_size_goes_to_the_window(self):
        self.panel.page({"kind": "size", "width": 600, "height": 420})
        self.assertEqual([(600, 420)], self.window.named("resize"))

    def test_a_width_the_screen_has_no_room_for_is_narrowed(self):
        # The page asks for its keyboard at full size (hud.js panelWidth); a screen narrower than
        # that gets a HUD as wide as itself, and the page draws the board to fit.
        self.window.room = 800
        self.panel.page({"kind": "size", "width": 946, "height": 520})
        self.panel.page({"kind": "size", "width": 700, "height": 420})      # fits: as asked
        self.panel.page({"kind": "size", "height": 420})                    # the width it has: no room to ask
        self.assertEqual([(800, 520), (700, 420), (None, 420)], self.window.named("resize"))
        # Room that is no width for a HUD (a window with no screen) narrows nothing.
        for room in (0, 40, -5, None, "wide", True):
            self.window.room = room
            self.panel.page({"kind": "size", "width": 946, "height": 520})
        self.assertEqual([(946, 520)] * 6, self.window.named("resize")[3:])

    def test_a_size_no_layout_has_is_not_taken(self):
        # The page's layout, 100..4000 wide and 50..4000 tall: a page mid-load or gone wrong says 0, or
        # nonsense, and the panel would shrink to nothing or grow off every screen.
        for w, h in [(600, 20), (600, 9000), (50, 420), (5000, 420), (600, None), ("wide", 420), (True, 420), (600, "tall")]:
            self.panel.page({"kind": "size", "width": w, "height": h})
        self.panel.page({"kind": "size", "height": 420})            # the width it has
        self.assertEqual([(None, 420)], self.window.named("resize"))

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

    def test_nothing_asked_is_nothing(self):
        self.panel.wake()
        self.assertEqual([], self.window.calls)


class StartAndStop(Base):
    def test_panel_json_is_there_while_it_runs(self):
        self.assertTrue(self.state()["shown"])
        self.panel.stop()
        self.assertIsNone(self.state())


class Adapters(unittest.TestCase):
    def test_both_panels_compile(self):
        # Neither imports here (pyobjc, GTK): compiled, at least, as CI does for Linux only.
        host = os.path.dirname(os.path.abspath(__file__))
        for path in ("macos/panel.py", "linux/panel.py"):
            with open(os.path.join(host, path), encoding="utf-8") as f:
                compile(f.read(), path, "exec")


if __name__ == "__main__":
    unittest.main()
