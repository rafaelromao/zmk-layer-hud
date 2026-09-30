"""Tests for the command line: that every verb reaches the right thing, that the tree is found
through the symlink it is normally invoked by, and that the two verbs which pass their whole line
to another parser keep doing so."""

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cli  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIM = os.path.join(ROOT, "bin", "zmk-layer-hud")


def verbs(parser):
    """Every subcommand the parser knows, in the order they were added."""
    for action in parser._subparsers._group_actions:
        if hasattr(action, "choices"):
            return dict(action.choices)
    return {}


class Parser(unittest.TestCase):
    def setUp(self):
        self.parser = build = cli.build_parser()
        self.verbs = verbs(build)

    def test_every_verb_has_a_handler(self):
        # A verb wired to nothing fails at the moment someone types it, which is the worst
        # moment; a missing `func` is cheap to notice here instead.
        for name, sub in self.verbs.items():
            if name in cli.PASSTHROUGH:
                continue  # split off before argparse runs; they never reach a `func`
            self.assertTrue(callable(sub.get_default("func")), f"{name} has no handler")

    def test_the_documented_verbs_are_all_there(self):
        expected = {"start", "stop", "restart", "status", "log", "doctor", "setup", "update",
                    "uninstall", "import", "sync", "keymap", "config", "demo", "poke", "feed",
                    "version", "session", "heatmap"}
        self.assertEqual(expected, set(self.verbs))

    def test_the_session_verbs_need_no_venv(self):
        # They only touch files, and must work on a machine where the venv is not built yet.
        self.assertFalse({"session", "heatmap"} & cli.NEEDS_VENV)

    def test_demo_plays_a_script_on_a_socket_of_its_own(self):
        args = self.parser.parse_args(["demo", "--play", "docs/demo-type.json", "--loop", "--no-browser"])
        self.assertEqual(("docs/demo-type.json", True, True, 8767), (args.play, args.loop, args.no_browser, args.ws_port))
        page, ws = cli.demo_urls(8765, 8767)
        self.assertEqual("ws://127.0.0.1:8767", ws)
        self.assertEqual("http://127.0.0.1:8765/index.html?ws=ws://127.0.0.1:8767", page)

    def test_session_takes_an_action_and_a_name(self):
        args = self.parser.parse_args(["session", "save", "week1"])
        self.assertEqual(("save", "week1"), (args.action, args.name))
        self.assertEqual("status", self.parser.parse_args(["session"]).action)
        self.assertIsNone(self.parser.parse_args(["heatmap"]).mode)
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["heatmap", "sometimes"])

    def test_needs_venv_names_real_verbs(self):
        # NEEDS_VENV is consulted by name before the parser runs, so a typo there would silently
        # stop a verb from re-execing into the venv.
        self.assertTrue(cli.NEEDS_VENV <= set(self.verbs) | set(cli.PASSTHROUGH))

    def test_passthrough_verbs_are_listed_in_help(self):
        # They are split off before argparse sees them, but they must still appear in `--help`.
        for name in cli.PASSTHROUGH:
            self.assertIn(name, self.verbs)

    def test_start_takes_reserve(self):
        args = self.parser.parse_args(["start", "--reserve"])
        self.assertTrue(args.reserve)
        self.assertFalse(self.parser.parse_args(["start"]).reserve)


class SyncArgv(unittest.TestCase):
    """import/sync keep their own parser, and its messages already say `zmk-layer-hud import`.
    The argv is reassembled rather than re-declared, so check it is assembled faithfully."""

    def setUp(self):
        self.seen = []
        sys.path.insert(0, os.path.join(ROOT, "host"))
        import sync as sync_mod
        self.sync_mod = sync_mod
        self.real = sync_mod.main
        sync_mod.main = lambda argv: self.seen.append(list(argv)) or 0

    def tearDown(self):
        self.sync_mod.main = self.real

    def run_cli(self, argv):
        parser = cli.build_parser()
        args = parser.parse_args(argv)
        args.func(args)
        return self.seen[-1]

    def test_import_minimal(self):
        self.assertEqual(["import", "github.com/you/keyboards"],
                         self.run_cli(["import", "github.com/you/keyboards"]))

    def test_import_with_every_flag(self):
        self.assertEqual(
            ["import", "~/zmk-config", "--keyboard", "corne",
             "--config", "/tmp/c.yaml", "--quiet"],
            self.run_cli(["import", "~/zmk-config", "--keyboard", "corne",
                          "--config", "/tmp/c.yaml", "--quiet"]))

    def test_sync_takes_no_source(self):
        self.assertEqual(["sync", "--quiet"], self.run_cli(["sync", "--quiet"]))


class State(unittest.TestCase):
    def test_state_is_outside_the_tree(self):
        # `update` replaces the tree wholesale, so the logs cannot live inside it.
        self.assertFalse(os.path.abspath(cli.STATE).startswith(os.path.abspath(cli.ROOT) + os.sep))

    def test_status_reads_the_last_word_on_each_channel(self):
        text = ("hudfeed: cannot read what is typed on Diamond (/dev/hidraw0): denied\n"
                "hudfeed: reading what is typed on Diamond (/dev/hidraw0)\n")
        self.assertEqual("hudfeed: reading what is typed on Diamond (/dev/hidraw0)",
                         cli.last_matching(text, "reading what is typed on"))
        self.assertEqual("", cli.last_matching(text, "no HID keyboard"))


class Shim(unittest.TestCase):
    """The shim is normally reached through ~/.local/bin/zmk-layer-hud, so $0 is that symlink and
    not the file. Finding the tree anyway is the one thing it must not get wrong."""

    def test_tree_is_found_through_a_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = os.path.join(tmp, "zmk-layer-hud")
            os.symlink(SHIM, link)
            out = subprocess.run(["sh", link, "version"], capture_output=True, text=True,
                                 cwd=tmp, env={**os.environ, "ZMKHUD_ROOT": "", "PATH": os.environ["PATH"]})
            self.assertEqual(0, out.returncode, out.stderr)
            self.assertIn(f"tree   {ROOT}", out.stdout)

    def test_sessions_are_named_listed_and_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "ZMKHUD_STATE": tmp}
            run = lambda *a: subprocess.run(["sh", SHIM] + list(a), capture_output=True, text=True, env=env)
            out = run("session")
            self.assertEqual(0, out.returncode, out.stderr)
            self.assertIn("not named yet", out.stdout)
            self.assertEqual(0, run("session", "save", "week1").returncode)
            self.assertEqual(0, run("session", "new", "week2").returncode)
            listing = run("session", "list").stdout
            self.assertIn("* week2", listing)
            self.assertIn("week1", listing)
            self.assertEqual(0, run("session", "load", "week1").returncode)
            self.assertTrue(run("session").stdout.startswith("week1"))
            self.assertEqual(0, run("heatmap", "session").returncode)
            self.assertEqual("session", run("heatmap").stdout.strip())
            bad = run("session", "delete", "week1", "--yes")
            self.assertEqual(1, bad.returncode)          # the active one
            self.assertIn("active session", bad.stderr)
            self.assertEqual(0, run("session", "delete", "week2", "--yes").returncode)
            self.assertNotIn("week2", run("session", "list").stdout)

    def test_tree_is_found_through_a_chain_of_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = os.path.join(tmp, "one")
            second = os.path.join(tmp, "two")
            os.symlink(SHIM, first)
            os.symlink(first, second)
            out = subprocess.run(["sh", second, "version"], capture_output=True, text=True,
                                 cwd=tmp, env={**os.environ, "ZMKHUD_ROOT": ""})
            self.assertEqual(0, out.returncode, out.stderr)
            self.assertIn(f"tree   {ROOT}", out.stdout)


if __name__ == "__main__":
    unittest.main()
