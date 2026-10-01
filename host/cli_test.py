"""Tests for the command line: that every verb reaches the right thing, that the tree is found
through the symlink it is normally invoked by, and that the two verbs which pass their whole line
to another parser keep doing so."""

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

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
                    "version", "session", "heatmap", "show", "hide", "toggle"}
        self.assertEqual(expected, set(self.verbs))

    def test_the_session_verbs_need_no_venv(self):
        # They only touch files, and must work on a machine where the venv is not built yet.
        self.assertFalse({"session", "heatmap"} & cli.NEEDS_VENV)

    def test_showing_and_hiding_need_no_venv(self):
        # A signal and two small files: a bar widget runs them, and so does a keybinding.
        self.assertFalse({"show", "hide", "toggle"} & cli.NEEDS_VENV)

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

    def test_a_verb_that_needs_the_venv_says_so_without_a_traceback(self):
        # At login no one is watching, and a traceback would be all the log had to say.
        with mock.patch.object(cli, "VENV_PYTHON", os.path.join(ROOT, "no-such-venv", "python3")), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(1, cli.main(["keymap"]))
        self.assertIn("zmk-layer-hud keymap: no venv yet", err.getvalue())

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

    def test_start_hidden_and_in_the_foreground(self):
        args = self.parser.parse_args(["start", "--hidden", "--foreground"])
        self.assertEqual((True, True), (args.hidden, args.foreground))
        self.assertTrue(self.parser.parse_args(["restart", "--hidden"]).hidden)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.parser.parse_args(["restart", "--foreground"])     # `run` stops the old one itself


class HostScript(unittest.TestCase):
    """What start hands the platform's script: the verb, and whether to start hidden."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patches = [mock.patch.object(cli, "STATE", self.tmp.name),
                   mock.patch.object(cli, "definitions_missing", lambda: None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_start_hidden_tells_the_script(self):
        with mock.patch.object(cli.subprocess, "call", return_value=0) as call:
            cli.main(["start", "--hidden"])
        argv, env = call.call_args[0][0], call.call_args[1]["env"]
        self.assertEqual(["bash", cli.host_script(), "start"], argv)
        self.assertEqual(("1", "0"), (env["ZMKHUD_HIDDEN"], env["ZMKHUD_RESERVE"]))
        with mock.patch.object(cli.subprocess, "call", return_value=0) as call:
            cli.main(["start"])
        self.assertEqual("0", call.call_args[1]["env"]["ZMKHUD_HIDDEN"])

    def test_the_foreground_becomes_the_panel(self):
        with mock.patch.object(cli.os, "execvpe", side_effect=OSError(2, "gone")) as execvpe, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(1, cli.main(["start", "--hidden", "--foreground"]))
        name, argv, env = execvpe.call_args[0]
        self.assertEqual(("bash", ["bash", cli.host_script(), "run"]), (name, argv))
        self.assertEqual(("1", self.tmp.name), (env["ZMKHUD_HIDDEN"], env["ZMKHUD_STATE"]))
        self.assertIn("cannot run", err.getvalue())

    def test_restart_starts_hidden_but_stops_plainly(self):
        with mock.patch.object(cli.subprocess, "call", return_value=0) as call:
            cli.main(["restart", "--hidden"])
        (stop, start) = call.call_args_list
        self.assertEqual("stop", stop[0][0][-1])
        self.assertEqual(("start", "1"), (start[0][0][-1], start[1]["env"]["ZMKHUD_HIDDEN"]))


# A stand-in for a panel: says it is shown, and does what panel.want says each time SIGUSR2 comes.
FAKE_PANEL = textwrap.dedent('''
    import signal, sys, time
    sys.path.insert(0, sys.argv[1])
    import panelstate
    d = sys.argv[2]
    signal.signal(panelstate.SIGNAL, lambda *_: panelstate.write(d, panelstate.wanted(d)))
    panelstate.write(d, True)
    time.sleep(30)
''')


class ShowHide(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(cli, "STATE", self.tmp.name)
        p.start()
        self.addCleanup(p.stop)

    def start_panel(self):
        proc = subprocess.Popen([sys.executable, "-c", FAKE_PANEL, os.path.join(ROOT, "host"), self.tmp.name])
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        deadline = time.monotonic() + 10
        while not os.path.exists(os.path.join(self.tmp.name, "panel.json")):
            self.assertLess(time.monotonic(), deadline, "the stand-in never said it was there")
            time.sleep(0.02)
        return proc

    def run_cli(self, *argv, pids=()):
        with mock.patch.object(cli, "panel_pids", lambda: list(pids)), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = cli.main(list(argv))
        return code, out.getvalue() + err.getvalue()

    def test_hide_show_and_toggle_are_done_and_said(self):
        proc = self.start_panel()
        code, said = self.run_cli("hide", pids=[proc.pid])
        self.assertEqual((0, False), (code, cli.panelstate.read(self.tmp.name)["shown"]), said)
        self.assertIn("HUD hidden", said)
        self.assertEqual(0, self.run_cli("toggle", pids=[proc.pid])[0])
        self.assertTrue(cli.panelstate.read(self.tmp.name)["shown"])
        code, said = self.run_cli("show", pids=[proc.pid])
        self.assertEqual(0, code)
        self.assertIn("HUD shown", said)

    def test_nothing_running_is_said_and_a_stale_word_goes(self):
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        cli.panelstate.write(self.tmp.name, True, pid=gone.pid)
        code, said = self.run_cli("show")
        self.assertEqual(1, code)
        self.assertIn("not running", said)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "panel.json")))

    def test_a_panel_that_never_said_where_it_is_is_not_signalled(self):
        # Older than show and hide: SIGUSR2's default would end it.
        with mock.patch.object(cli.os, "kill") as kill:
            code, said = self.run_cli("hide", pids=[12345])
        self.assertEqual(1, code)
        self.assertIn("restart", said)
        kill.assert_not_called()


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

    def test_help_after_a_verb_is_its_help_not_a_repository(self):
        with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit):
            cli.main(["import", "help"])
        self.assertIn("usage:", out.getvalue())
        self.assertEqual([], self.seen)

    def test_import_pristine(self):
        self.assertEqual(["import", "--pristine"], self.run_cli(["import", "--pristine"]))

    def test_import_keeps_what_it_is_told(self):
        self.assertEqual(["import", "--pristine", "--keep-custom"], self.run_cli(["import", "--pristine", "--keep-custom"]))
        self.assertEqual(["import", "--pristine", "--keep-custom=1,3"],
                         self.run_cli(["import", "--pristine", "--keep-custom", "1,3"]))
        with self.assertRaises(cli.Fail):
            self.run_cli(["import", "--pristine", "--keep-custom", "github.com/you/keyboards"])

    def test_import_minimal(self):
        self.assertEqual(["import", "github.com/you/keyboards"],
                         self.run_cli(["import", "github.com/you/keyboards"]))

    def test_import_with_every_flag(self):
        self.assertEqual(
            ["import", "~/zmk-config", "--keyboard", "corne",
             "--config", "/tmp/c.yaml", "--quiet", "--no-fetch"],
            self.run_cli(["import", "~/zmk-config", "--keyboard", "corne",
                          "--config", "/tmp/c.yaml", "--quiet", "--no-fetch"]))

    def test_import_needs_no_repo(self):
        # A drawing alone, from the keymap-drawer YAML the config names.
        self.assertEqual(["import", "--config", "/tmp/c.yaml"], self.run_cli(["import", "--config", "/tmp/c.yaml"]))

    def test_sync_takes_no_source(self):
        self.assertEqual(["sync", "--quiet", "--no-fetch"], self.run_cli(["sync", "--quiet", "--no-fetch"]))


class ConfigLink(unittest.TestCase):
    """A config kept in a repo is linked with its definitions and its imported record: each is found
    beside the config's own path, not beside what it points at."""

    def test_the_config_its_definitions_and_its_record_are_linked(self):
        with tempfile.TemporaryDirectory() as d:
            repo, home = os.path.join(d, "repo"), os.path.join(d, "home")
            os.makedirs(repo)
            for name in ("board.yaml", "board.definitions.json", "board.imported.yaml"):
                with open(os.path.join(repo, name), "w", encoding="utf-8") as f:
                    f.write(name)
            with mock.patch.object(cli, "CONFIG_DIR", home), \
                    mock.patch.object(cli, "CONFIG", os.path.join(home, "config.yaml")), \
                    contextlib.redirect_stdout(io.StringIO()):
                cli.config_link(argparse.Namespace(file=os.path.join(repo, "board.yaml")))
            for name, target in (("config.yaml", "board.yaml"), ("config.definitions.json", "board.definitions.json"),
                                 ("config.imported.yaml", "board.imported.yaml")):
                self.assertEqual(os.path.join(repo, target), os.readlink(os.path.join(home, name)))


class DefinitionsMissing(unittest.TestCase):
    """start's refusal names the step the config is missing, and no more."""

    def missing(self, text):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.yaml")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            with mock.patch.dict(os.environ, {"ZMKHUD_CONFIG": path}):
                return cli.definitions_missing()

    def test_a_config_naming_its_keymap_is_told_to_import(self):
        self.assertTrue(self.missing("keymap: ~/k/keymap-drawer.yaml\n").endswith("run `zmk-layer-hud import`"))

    def test_a_config_naming_nothing_is_told_where_from(self):
        self.assertIn("github.com/you/zmk-config", self.missing("hud: {width: 500}\n"))


class Reference(unittest.TestCase):
    """docs/cli.md is what the parsers say, every verb of it."""

    def test_the_reference_is_current(self):
        import clidocs
        with open(clidocs.PATH, encoding="utf-8") as f:
            self.assertEqual(clidocs.render(), f.read(), "docs/cli.md is behind: make cli-docs")

    def test_every_verb_is_in_it(self):
        import clidocs
        text = clidocs.render()
        for verb in verbs(cli.build_parser()):
            self.assertIn(f"\n## {verb}\n", text)
            self.assertIn(f"usage: zmk-layer-hud {verb}", text)


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
                self.assertIn("rect.key.hs6 { fill: #fc4e2a; }", f.read())   # the config's light keys
            # Dark keys chosen on this machine: the export is drawn in them too.
            session.set_pref(os.path.join(tmp, "sessions"), "theme", "dark")
            run = subprocess.run(["sh", SHIM, "session", "export", "-o", out, "--mode", "physical",
                                  "--config", os.path.join(ROOT, "config", "example-3x5.yaml")],
                                 capture_output=True, text=True, env=env)
            self.assertEqual(0, run.returncode, run.stderr)
            with open(out, encoding="utf-8") as f:
                self.assertIn("rect.key.hs6 { fill: #6366f1; }", f.read())

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
