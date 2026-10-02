"""Tests for what a running panel says of itself (panel.json) and what the command line asks of it
(panel.want)."""

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
                          "stats": []}, panelstate.read(self.d))
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

    def test_the_signal_is_never_sigusr1(self):
        # WebKit suspends its own threads with SIGUSR1 on Linux, and the GTK panel is a WebKit
        # process: sent there, SIGUSR1 hangs or crashes it.
        self.assertNotEqual(signal.SIGUSR1, panelstate.SIGNAL)

    def test_the_state_dir_is_the_one_the_cli_exports_else_the_xdg_one(self):
        with mock.patch.dict(os.environ, {"ZMKHUD_STATE": "/s/zmk", "XDG_STATE_HOME": "/x"}):
            self.assertEqual("/s/zmk", panelstate.default_dir())
        with mock.patch.dict(os.environ, {"ZMKHUD_STATE": "", "XDG_STATE_HOME": "/x"}):
            self.assertEqual(os.path.join("/x", "zmk-layer-hud"), panelstate.default_dir())


if __name__ == "__main__":
    unittest.main()
