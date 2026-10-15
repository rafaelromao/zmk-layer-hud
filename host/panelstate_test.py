"""Tests for what a running panel says of itself (panel.json) and what the command line asks of it
(panel.want)."""

import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import panelstate  # noqa: E402


class PrivateFile(unittest.TestCase):
    """A log opened for this user alone, even one a run from before left readable to all."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = os.path.join(self.tmp.name, "state")
        self.path = os.path.join(self.d, "x.log")

    def mode(self, p):
        return stat.S_IMODE(os.stat(p).st_mode)

    def test_a_log_from_before_is_made_this_users_alone_again(self):
        os.makedirs(self.d, mode=0o755)
        with open(self.path, "w") as f:
            f.write("old\n")
        os.chmod(self.path, 0o644)
        with panelstate.open_private(self.path) as f:
            f.write("new\n")
        self.assertEqual((0o600, 0o700), (self.mode(self.path), self.mode(self.d)))
        with open(self.path) as f:
            self.assertEqual("new\n", f.read())                 # truncated, as a fresh log is

    def test_a_panels_own_file_is_written_whole_and_this_users_alone(self):
        # Where the panel was put on screen (state.json), written by both panels.
        path = os.path.join(self.d, "state.json")
        panelstate.write_json(path, {"frame": [1, 2, 3, 4], "linux": {"top": 8}})
        self.assertEqual(0o600, self.mode(path))
        with open(path) as f:
            self.assertEqual({"frame": [1, 2, 3, 4], "linux": {"top": 8}}, json.load(f))

    def test_or_kept_and_appended_to(self):
        with panelstate.open_private(self.path, truncate=False) as f:   # the directory made too
            f.write("one\n")
        os.chmod(self.path, 0o644)
        with panelstate.open_private(self.path, truncate=False) as f:
            f.write("two\n")
        self.assertEqual(0o600, self.mode(self.path))
        with open(self.path) as f:
            self.assertEqual("one\ntwo\n", f.read())


class Token(unittest.TestCase):
    """The socket's token for the run, kept where `poke` finds it and nobody else does."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = os.path.join(self.tmp.name, "state")      # not there yet, as on a first run

    def tearDown(self):
        self.tmp.cleanup()

    def test_written_for_this_user_alone_and_read_back(self):
        panelstate.write_token(self.d, "abc123")
        self.assertEqual("abc123", panelstate.read_token(self.d))
        self.assertEqual(0o600, stat.S_IMODE(os.stat(os.path.join(self.d, "token")).st_mode))
        self.assertEqual(0o700, stat.S_IMODE(os.stat(self.d).st_mode))
        self.assertEqual(["token"], os.listdir(self.d))     # no temporary file left beside it

    def test_an_existing_directory_is_closed_to_others_too(self):
        os.makedirs(self.d, mode=0o755)
        panelstate.write_token(self.d, "abc123")
        self.assertEqual(0o700, stat.S_IMODE(os.stat(self.d).st_mode))

    def test_none_while_nothing_serves(self):
        self.assertIsNone(panelstate.read_token(self.d))
        os.makedirs(self.d)
        with open(os.path.join(self.d, "token"), "w") as f:
            f.write("\n")
        self.assertIsNone(panelstate.read_token(self.d))

    def test_removed_on_the_way_out_unless_a_newer_run_wrote_its_own(self):
        panelstate.write_token(self.d, "old")
        panelstate.write_token(self.d, "new")           # the restart's feed, up before the old one is gone
        panelstate.remove_token(self.d, "old")
        self.assertEqual("new", panelstate.read_token(self.d))
        panelstate.remove_token(self.d, "new")
        self.assertIsNone(panelstate.read_token(self.d))
        panelstate.remove_token(self.d, "new")          # twice is fine


class PanelState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        self.path = os.path.join(self.d, "panel.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_what_a_live_panel_says_is_read_back(self):
        panelstate.write(self.d, False)
        self.assertEqual({"pid": os.getpid(), "shown": False, "wpm": 0, "avg_wpm": None, "top_wpm": None, "session": None,
                          "stats": [], "typing": None, "dock": False, "dock_note": None, "dock_at": None},
                         panelstate.read(self.d))
        panelstate.write(self.d, True, wpm=63)
        self.assertEqual(63, panelstate.read(self.d)["wpm"])
        self.assertEqual(["panel.json"], os.listdir(self.d))       # no temporary file left beside it
        self.assertEqual(0o600, stat.S_IMODE(os.stat(self.path).st_mode))   # and this user's alone

    def test_what_the_pages_stats_column_shows_is_kept_with_it(self):
        said = panelstate.stats_of({"kind": "stats", "avg": 52, "top": None, "session": "colemak-1",
                                    "rows": [["wpm", "46"], ["avg wpm", "52"], ["top wpm", "—"], ["ALPHA 1", "88%"]]})
        self.assertEqual({"avg_wpm": 52, "top_wpm": None, "session": "colemak-1",
                          "stats": [["wpm", "46"], ["avg wpm", "52"], ["top wpm", "—"], ["ALPHA 1", "88%"]]}, said)
        panelstate.write(self.d, True, wpm=46, stats=said)
        st = panelstate.read(self.d)
        self.assertEqual((46, 52, None, said["stats"]), (st["wpm"], st["avg_wpm"], st["top_wpm"], st["stats"]))
        # What is not a stats message is none, and a file with rows that are not rows reads as no rows.
        for bad in (None, {}, {"rows": "wpm"}, {"rows": [["wpm"]]}, {"rows": [["wpm", 46]]},
                    {"rows": [["x" * 65, "1"]]}, {"rows": [["a", "b"]] * 25}):
            self.assertIsNone(panelstate.stats_of(bad), bad)
        self.assertEqual(None, panelstate.stats_of({"rows": [], "avg": True})["avg_wpm"])     # a bool is no speed
        self.assertEqual(None, panelstate.stats_of({"rows": [], "session": ""})["session"])
        panelstate.write(self.d, True, stats={"avg_wpm": 50, "top_wpm": 70, "stats": [["wpm", 46]]})
        self.assertEqual((None, None, []), tuple(panelstate.read(self.d)[k] for k in ("avg_wpm", "top_wpm", "stats")))

    def test_the_icons_show_the_wpm_chosen_for_them(self):
        self.assertEqual("current", panelstate.wpm_choice(self.d))         # until one is chosen
        panelstate.set_wpm_choice(self.d, "top")
        self.assertEqual("top", panelstate.wpm_choice(self.d))
        with self.assertRaises(ValueError):
            panelstate.set_wpm_choice(self.d, "best")
        st = {"wpm": 46, "avg_wpm": 52, "top_wpm": None}
        self.assertEqual((46, 52, None), tuple(panelstate.wpm_shown(st, c) for c in panelstate.WPM_CHOICES))
        self.assertIsNone(panelstate.wpm_shown(None, "current"))
        with open(os.path.join(self.d, "menubar.json"), "w", encoding="utf-8") as f:
            f.write('{"wpm": "fastest"}')
        self.assertEqual("current", panelstate.wpm_choice(self.d))

    def test_a_panel_that_is_gone_says_nothing(self):
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        panelstate.write(self.d, True, pid=gone.pid)
        self.assertIsNone(panelstate.read(self.d))

    def test_a_file_that_is_not_one_says_nothing(self):
        self.assertIsNone(panelstate.read(self.d))                  # none at all
        for text in ("", "[]", "{", '{"pid": "%d", "shown": true}' % os.getpid(),
                     '{"pid": true, "shown": true}', '{"pid": %d}' % os.getpid(),
                     '{"pid": %d, "shown": 1}' % os.getpid()):
            with open(self.path, "w", encoding="utf-8") as f:
                f.write(text)
            self.assertIsNone(panelstate.read(self.d), text)

    def test_a_panel_takes_back_only_its_own_word(self):
        # A restart's new panel can have written the file before the old one is done going.
        panelstate.write(self.d, True, pid=os.getpid() + 100000)
        panelstate.remove(self.d)
        self.assertTrue(os.path.exists(self.path))
        panelstate.write(self.d, True)
        panelstate.remove(self.d)
        self.assertFalse(os.path.exists(self.path))
        panelstate.discard(self.d)                                  # nothing there: no complaint

    def test_what_is_asked_is_what_is_wanted(self):
        self.assertIsNone(panelstate.wanted(self.d))
        panelstate.ask(self.d, False)
        self.assertIs(False, panelstate.wanted(self.d))
        panelstate.ask(self.d, True)
        self.assertIs(True, panelstate.wanted(self.d))

    def test_the_touch_keyboards_state_is_kept_checked(self):
        panelstate.write(self.d, True, extra={"typing": "denied", "dock": True, "other": 1})
        st = panelstate.read(self.d)
        self.assertEqual(("denied", True), (st["typing"], st["dock"]))
        self.assertNotIn("other", st)
        panelstate.write(self.d, True, extra={"typing": "sideways", "dock": "yes"})
        st = panelstate.read(self.d)
        self.assertEqual((None, False), (st["typing"], st["dock"]))
        # Why it is not docked: a sentence, not a page of anything.
        panelstate.write(self.d, True, extra={"dock": False, "dock_note": "too big", "dock_at": 1760000000.5})
        self.assertEqual(("too big", 1760000000.5), (panelstate.read(self.d)["dock_note"], panelstate.read(self.d)["dock_at"]))
        for bad in ("", 5, "x" * (panelstate.NOTE_MAX + 1)):
            panelstate.write(self.d, True, extra={"dock_note": bad})
            self.assertIsNone(panelstate.read(self.d)["dock_note"], msg=repr(bad))

    def test_a_request_is_taken_once(self):
        self.assertIsNone(panelstate.take_want(self.d, "dock"))
        panelstate.request(self.d, "dock", True)
        panelstate.request(self.d, "typing", "request")
        self.assertIs(True, panelstate.take_want(self.d, "dock"))
        self.assertIsNone(panelstate.take_want(self.d, "dock"))     # once
        self.assertEqual("request", panelstate.take_want(self.d, "typing"))
        with self.assertRaises(ValueError):
            panelstate.request(self.d, "shown", True)

    def test_the_signal_is_never_sigusr1(self):
        # WebKit suspends its own threads with SIGUSR1 on Linux, and the GTK panel is a WebKit
        # process: sent there, SIGUSR1 hangs or crashes it.
        self.assertNotEqual(signal.SIGUSR1, panelstate.SIGNAL)

    def test_the_state_dir_is_the_one_the_cli_exports_else_the_xdg_one(self):
        with mock.patch.dict(os.environ, {"ZMKHUD_STATE": "/s/zmk", "XDG_STATE_HOME": "/x"}):
            self.assertEqual("/s/zmk", panelstate.default_dir())
        with mock.patch.dict(os.environ, {"ZMKHUD_STATE": "", "XDG_STATE_HOME": "/x"}):
            self.assertEqual(os.path.join("/x", "zmk-layer-hud"), panelstate.default_dir())


class Hud(unittest.TestCase):
    """The HUD read once (panelstate.hud), with the processes the test says are running."""

    ROOT = "/opt/zmk-layer-hud"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = self.tmp.name
        self.panels, self.feeds = {}, {}

    def procs(self, pattern):
        return self.feeds if "hudfeed" in pattern else self.panels

    def hud(self, root=ROOT):
        return panelstate.hud(self.d, root, procs=self.procs)

    def panel(self, pid, tree=ROOT, os_="linux"):
        self.panels[pid] = f"/usr/bin/python3 {tree}/host/{os_}/panel.py"

    def test_nothing_running(self):
        h = self.hud()
        self.assertEqual((False, False, False, None, "stopped"), (h["running"], h["said"], h["ours"], h["pid"], h["feed"]))
        self.assertIsNone(h["shown"])

    def test_a_panel_of_this_copy_that_said_so(self):
        self.panel(os.getpid())
        self.feeds[99] = f"python3 {self.ROOT}/host/hudfeed.py"
        panelstate.write(self.d, False, wpm=40, extra={"typing": "ok"})
        h = self.hud()
        self.assertEqual((True, True, True, os.getpid()), (h["running"], h["said"], h["ours"], h["pid"]))
        self.assertEqual((False, 40, "ok", "running"), (h["shown"], h["wpm"], h["typing"], h["feed"]))

    def test_a_pid_handed_out_again_is_not_a_panel(self):
        # panel.json names a live process -- this test's -- that is no HUD panel.
        panelstate.write(self.d, True)
        h = self.hud()
        self.assertEqual((False, False, None), (h["running"], h["said"], h["shown"]))

    def test_a_panel_from_another_copy_is_running_but_not_ours(self):
        self.panel(os.getpid(), tree="/home/me/src/zmk-layer-hud")
        panelstate.write(self.d, True)
        h = self.hud()
        self.assertEqual((True, True, False), (h["running"], h["said"], h["ours"]))
        self.assertEqual(os.path.realpath("/home/me/src/zmk-layer-hud"), h["where"])

    def test_a_panel_of_this_copy_that_has_said_nothing_yet(self):
        self.panel(4242)
        h = self.hud()
        self.assertEqual((True, False, True, 4242), (h["running"], h["said"], h["ours"], h["pid"]))

    def test_the_macos_panel_runs_the_feed(self):
        self.panel(os.getpid(), os_="macos")
        panelstate.write(self.d, True)
        with mock.patch.object(sys, "platform", "darwin"):
            self.assertEqual("in the panel", self.hud()["feed"])

    def test_the_feed_run_on_its_own(self):
        self.feeds[7] = "/usr/bin/python3 /home/me/.local/bin/zmk-layer-hud feed --raw"
        self.assertEqual("running", self.hud()["feed"])


if __name__ == "__main__":
    unittest.main()
