"""Tests for the command line: that every verb reaches the right thing, that the tree is found
through the symlink it is normally invoked by, and that the two verbs which pass their whole line
to another parser keep doing so."""

import json
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
        import session   # the verb names the modes without importing it; they must be the same
        for mode in session.HEATMAP_MODES:
            self.assertEqual(mode, self.parser.parse_args(["heatmap", mode]).mode)
        args = self.parser.parse_args(["session", "rename-layer", "sym", "symbols", "--all"])
        self.assertEqual(("rename-layer", "sym", "symbols", True), (args.action, args.name, args.other, args.all))
        # Drawing the heatmap needs keymap-drawer, and only that part of `session` goes to the venv.
        args = self.parser.parse_args(["session", "export", "week1", "--mode", "physical", "-o", "w.svg"])
        self.assertEqual(("export", "week1", "physical", "w.svg"), (args.action, args.name, args.mode, args.output))
        self.assertTrue(cli.needs_venv(args))
        self.assertFalse(cli.needs_venv(self.parser.parse_args(["session", "list"])))

    def test_two_sessions_side_by_side(self):
        import session
        a, b = session.empty("week1", True), session.empty("week2", True)
        a["presses"] = {"alpha": {"0": 800}, "sym": {"1": 200}}
        a["totals"].update(chars=900, deleted=90, active_ms=600000, active_net=810, peak_wpm=70)
        b["presses"] = {"alpha": {"0": 900}, "sym": {"1": 300}, "nav": {"2": 300}}
        b["combos"] = {"alpha": {"0,1": 150}}
        b["totals"].update(chars=1200, deleted=60, active_ms=600000, active_net=1140, peak_wpm=80,
                           sfb=30, bigrams=1000)
        rows = {r[0].strip(): r[1:] for r in cli.compare_rows(a, b, session)}
        self.assertEqual(("1,000", "1,500", "+50%"), rows["keys"])
        self.assertEqual(("0%", "11%", "+11 pts"), rows["combos"])          # 150 of 1350 keystrokes
        self.assertEqual(("90%", "95%", "+5 pts"), rows["accurate"])
        self.assertEqual(("16", "23", "+7"), rows["wpm"])
        self.assertEqual(("—", "3.0%", ""), rows["same finger"])
        self.assertEqual(("80%", "60%", "-20 pts"), rows["alpha"])
        self.assertEqual(("0%", "20%", "+20 pts"), rows["nav"])
        names = [r[0].strip() for r in cli.compare_rows(a, b, session)]
        self.assertLess(names.index("alpha"), names.index("sym"))           # B's most used layer first

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
            # Counts on a layer the keymap has since renamed: said, and moved over.
            path = os.path.join(tmp, "sessions", "week1.json")
            with open(path, encoding="utf-8") as f:
                s = json.load(f)
            s.update(layers=["base", "symbols"], presses={"base": {"1": 4}, "sym": {"2": 3}})
            with open(path, "w", encoding="utf-8") as f:
                json.dump(s, f)
            self.assertIn("sym: 3 keys", run("session").stdout)
            self.assertEqual(1, run("session", "save", "week1", "extra").returncode)
            moved = run("session", "rename-layer", "sym", "symbols")
            self.assertEqual(0, moved.returncode, moved.stderr)
            self.assertNotIn("not shown", run("session").stdout)
            # Two sessions side by side: week1 against the active one, then against a named one.
            self.assertEqual(0, run("session", "new", "week3").returncode)
            both = run("session", "compare", "week1")
            self.assertEqual(0, both.returncode, both.stderr)
            self.assertIn("week1", both.stdout.splitlines()[0])
            self.assertIn("week3", both.stdout.splitlines()[0])
            self.assertIn("symbols", both.stdout)
            self.assertEqual(1, run("session", "compare", "week3").returncode)   # itself
            self.assertEqual(1, run("session", "compare", "week1", "nope").returncode)

    def test_a_sessions_days(self):
        import datetime
        import session
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "ZMKHUD_STATE": tmp}
            run = lambda *a: subprocess.run(["sh", SHIM] + list(a), capture_output=True, text=True, env=env)
            self.assertIn("no day recorded yet", run("session", "history").stdout)
            _, s = session.status(os.path.join(tmp, "sessions"))
            session.add_counts(os.path.join(tmp, "sessions"), s["id"], s["gen"],
                               {"presses": {"base": {"0": 1200}}, "combos": {}, "chars": 1000, "active_ms": 600000,
                                "active_net": 950})
            out = run("session", "history")
            self.assertEqual(0, out.returncode, out.stderr)
            self.assertIn(f"{datetime.date.today().isoformat()} ", out.stdout)
            self.assertIn("1,200 keys", out.stdout)
            self.assertIn("19 wpm", out.stdout)                   # 950 characters, 190 words, in 10 minutes
            self.assertIn("every session, by day", run("session", "history", "--all").stdout)

    @unittest.skipUnless(os.path.exists(os.path.join(ROOT, ".venv", "bin", "python3")), "export runs in the venv")
    def test_a_sessions_heatmap_is_drawn_to_a_file(self):
        import session
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "ZMKHUD_STATE": tmp}
            _, s = session.status(os.path.join(tmp, "sessions"))
            session.add_counts(os.path.join(tmp, "sessions"), s["id"], s["gen"],
                               {"presses": {"DEF": {"0": 40, "1": 4}}, "combos": {}})
            out = os.path.join(tmp, "heat.svg")
            run = subprocess.run(["sh", SHIM, "session", "export", "-o", out, "--mode", "physical",
                                  "--config", os.path.join(ROOT, "config", "example-3x5.yaml")],
                                 capture_output=True, text=True, env=env)
            self.assertEqual(0, run.returncode, run.stderr)
            with open(out, encoding="utf-8") as f:
                self.assertIn("rect.key.hs6", f.read())

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
