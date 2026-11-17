"""Tests for the command line: that every verb reaches the right thing, that the tree is found
through the symlink it is normally invoked by, and that the two verbs which pass their whole line
to another parser keep doing so."""

import argparse
import contextlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cli  # noqa: E402

try:
    import tomllib
except ImportError:             # before Python 3.11; 3.10 is supported, and has none
    tomllib = None

ROOT =os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
                    "version", "session", "heatmap", "show", "hide", "toggle", "menubar", "autostart", "power"}
        self.assertEqual(expected, set(self.verbs))

    def test_the_session_verbs_need_no_venv(self):
        # They only touch files, and must work on a machine where the venv is not built yet.
        self.assertFalse({"session", "heatmap"} & cli.NEEDS_VENV)

    def test_the_icons_wpm_is_picked_on_the_command_line(self):
        args = self.parser.parse_args(["menubar", "wpm", "average"])
        self.assertEqual(("wpm", "average"), (args.action, args.which))
        self.assertEqual(("status", None), (self.parser.parse_args(["menubar"]).action, self.parser.parse_args(["menubar"]).which))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.parser.parse_args(["menubar", "wpm", "fastest"])
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(cli, "STATE", tmp):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(0, cli.main(["menubar", "wpm"]))
                self.assertEqual(0, cli.main(["menubar", "wpm", "top"]))
                self.assertEqual(0, cli.main(["menubar", "wpm"]))
            self.assertEqual("top", cli.panelstate.wpm_choice(tmp))
            self.assertEqual(["the icon shows the live WPM", "the icon shows the session's top WPM from now on",
                              "the icon shows the session's top WPM"], out.getvalue().splitlines())
            # A choice after any other action is a mistake, said before that action does anything.
            with mock.patch.object(cli, "macos_menubar") as mac, contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(1, cli.main(["menubar", "enable", "top"]))
            mac.assert_not_called()
            self.assertIn("menubar wpm top", err.getvalue())

    def test_showing_and_hiding_need_no_venv(self):
        # A signal and two small files: a bar widget runs them, and so does a keybinding.
        self.assertFalse({"show", "hide", "toggle", "menubar", "autostart"} & cli.NEEDS_VENV)

    def test_demo_plays_a_script_on_a_socket_of_its_own(self):
        args = self.parser.parse_args(["demo", "--play", "docs/demo-type.json", "--loop", "--no-browser"])
        self.assertEqual(("docs/demo-type.json", True, True, 8767), (args.play, args.loop, args.no_browser, args.ws_port))
        page, ws = cli.demo_urls(8765, 8767, "tok")
        self.assertEqual("ws://127.0.0.1:8767/tok", ws)     # the token in the path: the page carries it
        self.assertEqual("http://127.0.0.1:8765/index.html?ws=ws://127.0.0.1:8767/tok", page)
        # ...and poke takes it from the environment, which `ps` does not show, not from its command line.
        hint = cli.demo_hint("tok", 8767)
        self.assertEqual("ZMKHUD_TOKEN=tok ZMKHUD_PORT=8767 zmk-layer-hud poke --type hello", hint)
        self.assertNotIn("--url", hint)

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

    def test_a_sessions_every_stat_is_printed(self):
        import session
        s = session.empty("week1", True)
        s["presses"] = {"alpha": {"0": 9000}, "sym": {"1": 999}, "nav": {"2": 1}}
        s["fingers"] = {"0": "lp", "1": "ri"}                  # 2 has no finger: in no hand's share
        s["timed"], s["ms"], s["legends"] = {"alpha": {"0": 5}}, {"alpha": {"0": 1500}}, {"alpha": {"0": "e"}}
        s["totals"].update(chars=900, deleted=90, active_ms=600000, active_net=810, peak_wpm=70)
        lines = cli.session_report(s, session)
        text = "\n".join(lines)
        self.assertIn("90% accurate", text)
        self.assertIn("hands 90% left, 10% right", text)
        self.assertIn("slowest key e on alpha, 300 ms", text)
        table = [line.split() for line in lines[lines.index("layers, of the keys:") + 1:]]
        self.assertEqual([["alpha", "9,000", "keys", "90.0%"], ["sym", "999", "keys", "10.0%"],
                          ["nav", "1", "keys", "<0.1%"]], table)     # the most used first; a key is not 0.0%
        b = json.loads(json.dumps(s))
        b["fingers"] = {}
        rows = {r[0].strip(): r[1:] for r in cli.compare_rows(s, b, session)}
        self.assertEqual(("90%", "—", ""), rows["left hand"])
        self.assertEqual(("e 300 ms", "e 300 ms", ""), rows["slowest key"])

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
        # start and restart go into the venv first (NEEDS_VENV), which a checkout without one --
        # CI's -- does not have: what is tested here is what comes after.
        patches = [mock.patch.object(cli, "STATE", self.tmp.name),
                   mock.patch.object(cli, "definitions_missing", lambda: None),
                   mock.patch.object(cli, "reexec_into_venv", lambda: None)]
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
    # A panel with no window: the real one (host/panelcore.py), answering the CLI's signal as both
    # platforms' panels do, its window doing nothing.
    import signal, sys, time
    sys.path.insert(0, sys.argv[1])
    import panelcore, panelstate
    d = sys.argv[2]
    nothing = lambda *a: None
    window = panelcore.Window(show=nothing, front=nothing, resize=nothing, room=nothing, no_drag=nothing, open=nothing,
                              close=nothing)
    panel = panelcore.Panel(window, d)
    signal.signal(panelstate.SIGNAL, lambda *_: panel.wake())
    panel.start()
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

    def run_cli(self, *argv, pids=(), tree=ROOT):
        # The processes running: `pids` as HUD panels run from `tree` (this copy, unless the test
        # says another), and no feed.
        procs = lambda pattern: ({p: f"/usr/bin/python3 {tree}/host/linux/panel.py" for p in pids}   # noqa: E731
                                 if "panel" in pattern else {})
        with mock.patch.object(cli, "processes", procs), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = cli.main(list(argv))
        return code, out.getvalue() + err.getvalue()

    def test_a_hud_from_another_copy_is_named_and_left_as_it_is(self):
        # A HUD run from another clone writes to the same $STATE: `hide` here cannot stop or start
        # it, but must not call it gone and throw away the word it keeps on itself either.
        proc = self.start_panel()
        code, said = self.run_cli("hide", pids=[proc.pid], tree="/opt/other-copy")
        self.assertEqual(1, code, said)
        self.assertIn("a HUD from /opt/other-copy is running", said)
        self.assertTrue(cli.panelstate.read(self.tmp.name)["shown"])          # still there, still its own

    def test_power_starts_no_second_hud_beside_another_copys(self):
        proc = self.start_panel()
        with mock.patch.object(cli, "reexec_into_venv"), mock.patch.object(cli, "cmd_start") as start, \
                mock.patch.object(cli, "cmd_stop") as stop:
            code, said = self.run_cli("power", pids=[proc.pid], tree="/opt/other-copy")
        self.assertEqual(1, code, said)
        start.assert_not_called()
        stop.assert_not_called()
        self.assertIn("a HUD from /opt/other-copy is running", said)

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

    def test_power_stops_a_running_hud_and_starts_one_that_is_not(self):
        with mock.patch.object(cli, "reexec_into_venv"), mock.patch.object(cli, "cmd_stop", return_value=0) as stop, \
                mock.patch.object(cli, "cmd_start", return_value=0) as start:
            self.assertEqual(0, self.run_cli("power", pids=[12345])[0])
            stop.assert_called_once()
            start.assert_not_called()
            self.assertEqual(0, self.run_cli("power")[0])
            start.assert_called_once()
            args = start.call_args[0][0]
            self.assertEqual((False, False, False), (args.reserve, args.hidden, args.foreground))

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


class Status(unittest.TestCase):
    """`status`, read through panelstate.hud, with the processes the test says are running."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for target, value in (("STATE", self.tmp.name), ("login_line", lambda: "off"), ("hypr_surfaces", lambda: None)):
            p = mock.patch.object(cli, target, value)
            p.start()
            self.addCleanup(p.stop)
        env = mock.patch.dict(os.environ, {"ZMKHUD_STATE": self.tmp.name})   # the sessions too, never the user's
        env.start()
        self.addCleanup(env.stop)

    def status(self, procs, system="Linux"):
        with mock.patch.object(cli, "processes", lambda pattern: procs.get("panel" if "panel" in pattern else "feed", {})), \
                mock.patch.object(cli.platform, "system", lambda: system), \
                mock.patch.object(sys, "platform", "darwin" if system == "Darwin" else "linux"), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            cli.cmd_status(None)
        return out.getvalue()

    def test_the_macos_feed_is_said_to_run_in_the_panel(self):
        cli.panelstate.write(self.tmp.name, True)
        said = self.status({"panel": {os.getpid(): f"python3 {ROOT}/host/macos/panel.py"}}, system="Darwin")
        self.assertIn("panel: running, shown", said)
        self.assertIn("feed:  in the panel", said)

    def test_json_is_the_huds_state_in_one_object(self):
        cli.panelstate.write(self.tmp.name, False, wpm=55)
        procs = {"panel": {os.getpid(): f"python3 {ROOT}/host/linux/panel.py"}}
        with mock.patch.object(cli, "processes", lambda pattern: procs.get("panel" if "panel" in pattern else "feed", {})), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(0, cli.main(["status", "--json"]))
        h = json.loads(out.getvalue())
        self.assertEqual((True, True, True, False, 55, "stopped"),
                         (h["running"], h["said"], h["ours"], h["shown"], h["wpm"], h["feed"]))

    def test_a_feed_run_on_its_own_is_running(self):
        said = self.status({"feed": {77: "/usr/bin/python3 /home/me/.local/bin/zmk-layer-hud feed"}})
        self.assertIn("panel: stopped", said)
        self.assertIn("feed:  running", said)


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


SHELL_JSON = """{
  "version": 1,
  "bar": {
    "position": "top",
    "layout": {
      "left": [
        {
          "id": "omarchy.menu"
        }
      ],
      "right": [
        {
          "id": "omarchy.tray"
        },
        {
          "id": "omarchy.network"
        }
      ]
    }
  },
  "plugins": []
}
"""


class OmarchyPlugin(unittest.TestCase):
    """The bar widget Omarchy loads, and `menubar` putting it where Omarchy looks and in the bar."""

    SRC = os.path.join(ROOT, "host", "linux", "omarchy")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.shell = os.path.join(self.tmp.name, "shell.json")
        self.plugins = os.path.join(self.tmp.name, "plugins")
        # Noctalia is here in these tests (which= says so), so its plugin dir and its shell must be
        # fakes too: a `menubar disable` here once took the real HUD out of the real bar. Its bar is
        # asked about (disable cleans wherever it is), and the fake's has none of ours in it.
        self.noctalia = FakeNoctalia()
        for p in (mock.patch.object(cli, "OMARCHY_PLUGINS", self.plugins),
                  mock.patch.object(cli, "SHELL_JSON", self.shell),
                  mock.patch.object(cli, "NOCTALIA_PLUGINS", os.path.join(self.tmp.name, "noctalia")),
                  mock.patch.object(cli, "noctalia", self.noctalia),
                  mock.patch.object(cli.platform, "system", return_value="Linux")):
            p.start()
            self.addCleanup(p.stop)

    def bar(self):
        with open(self.shell, encoding="utf-8") as f:
            return json.load(f)

    def run_cli(self, *argv, which=lambda n: "/usr/bin/" + n):
        with mock.patch.object(cli.shutil, "which", side_effect=which), \
                mock.patch.object(cli.subprocess, "call", return_value=0) as call, \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = cli.main(list(argv))
        return code, out.getvalue() + err.getvalue(), [c[0][0] for c in call.call_args_list]

    def test_the_manifest_is_one_omarchy_can_load(self):
        with open(os.path.join(self.SRC, "manifest.json"), encoding="utf-8") as f:
            m = json.load(f)
        # Omarchy takes a third-party id as <author>.<name>.
        self.assertEqual(cli.OMARCHY_ID, m["id"])
        self.assertEqual(2, len(m["id"].split(".")))
        self.assertEqual(["bar-widget"], m["kinds"])
        with open(os.path.join(self.SRC, m["entryPoints"]["barWidget"]), encoding="utf-8") as f:
            qml = f.read()
        self.assertIn("BarWidget {", qml)
        self.assertIn(f'moduleName: "{cli.OMARCHY_ID}"', qml)
        self.assertNotIn("id: state", qml)                # every Item has a `state` of its own

    def test_the_bar_reads_another_copys_hud_as_the_macos_icon_does(self):
        # panelstate.hud's "icon", through `status --json`: the word both icons take it by.
        with open(os.path.join(self.SRC, "BarWidget.qml"), encoding="utf-8") as f:
            qml = f.read()
        self.assertIn('h.icon === "another copy"', qml)

    def test_without_omarchy_nothing_is_copied(self):
        code, said, _ = self.run_cli("menubar", "enable", which=lambda n: None)
        self.assertEqual(1, code)
        self.assertIn("toggle", said)
        self.assertFalse(os.path.exists(self.plugins))

    def test_enable_writes_it_in_and_puts_it_first_in_the_bar(self):
        with open(self.shell, "w", encoding="utf-8") as f:
            f.write(SHELL_JSON)
        code, said, calls = self.run_cli("menubar", "enable")
        self.assertEqual(0, code, said)
        with open(os.path.join(self.plugins, cli.OMARCHY_ID, "BarWidget.qml"), encoding="utf-8") as f:
            qml = f.read()
        self.assertNotIn("__ZMK_LAYER_HUD_", qml)
        self.assertIn(f'"{os.path.join(ROOT, "bin", "zmk-layer-hud")}"', qml)
        self.assertIn(f'"{cli.STATE}"', qml)
        self.assertEqual({"id": cli.OMARCHY_ID}, self.bar()["bar"]["layout"]["right"][0])
        with open(self.shell, encoding="utf-8") as f:
            text = f.read()
        self.assertIn('      "right": [\n        {\n          "id": "rafaelromao.zmk-layer-hud"\n        },\n', text)
        self.assertTrue(os.path.isfile(self.shell + ".bak-zmk-layer-hud"))
        self.assertIn(["omarchy-shell", "shell", "rescanPlugins"], calls)
        # Again: nothing added twice.
        self.run_cli("menubar", "enable")
        self.assertEqual(1, sum(e.get("id") == cli.OMARCHY_ID for e in self.bar()["bar"]["layout"]["right"]))
        code, said, _ = self.run_cli("menubar", "disable")
        self.assertEqual(0, code)
        with open(self.shell, encoding="utf-8") as f:
            self.assertEqual(SHELL_JSON, f.read())
        self.assertFalse(os.path.exists(os.path.join(self.plugins, cli.OMARCHY_ID)))

    def test_disable_takes_an_entry_out_of_the_bar_when_its_folder_is_gone(self):
        # A plugin folder deleted by hand leaves its entry in shell.json: `disable` still cleans it,
        # as it did before it looked at the folder.
        with open(self.shell, "w", encoding="utf-8") as f:
            f.write(SHELL_JSON)
        self.run_cli("menubar", "enable")
        self.assertTrue(cli.bar_has(self.bar(), cli.OMARCHY_ID))
        shutil.rmtree(os.path.join(self.plugins, cli.OMARCHY_ID))
        code, said, _ = self.run_cli("menubar", "disable")
        self.assertEqual(0, code, said)
        self.assertFalse(cli.bar_has(self.bar(), cli.OMARCHY_ID))
        self.assertIn("Omarchy's bar", said)
        self.assertEqual([], [c for c in self.noctalia.calls if c[:2] == ("msg", "config-set")])

    def test_disable_on_a_machine_without_omarchy_touches_nothing(self):
        code, said, _ = self.run_cli("menubar", "disable", which=lambda n: None)
        self.assertEqual(0, code, said)
        self.assertIn("not installed", said)
        self.assertFalse(os.path.exists(self.shell))

    def test_the_old_id_is_taken_out(self):
        cfg = json.loads(SHELL_JSON)
        cfg["bar"]["layout"]["right"].append({"id": cli.APP_ID})
        cfg["plugins"].append({"id": cli.APP_ID})
        with open(self.shell, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        os.makedirs(os.path.join(self.plugins, cli.APP_ID))
        self.assertEqual(0, self.run_cli("menubar", "enable")[0])
        self.assertFalse(cli.bar_has(self.bar(), cli.APP_ID))
        self.assertTrue(cli.bar_has(self.bar(), cli.OMARCHY_ID))
        self.assertFalse(os.path.exists(os.path.join(self.plugins, cli.APP_ID)))

    def test_without_a_shell_json_it_writes_none(self):
        # The shell does not merge a partial file into its default: one with only this would
        # take every other widget away.
        code, said, _ = self.run_cli("menubar", "enable")
        self.assertEqual(0, code)
        self.assertFalse(os.path.exists(self.shell))
        self.assertIn(f"omarchy plugin enable {cli.OMARCHY_ID}", said)

    def test_on_macos_disable_keeps_the_icon_away(self):
        off = os.path.join(self.tmp.name, "menubar-off")
        with mock.patch.object(cli.platform, "system", return_value="Darwin"), \
                mock.patch.object(cli, "MENUBAR_OFF", off), mock.patch.object(cli, "pgrep", return_value=True):
            self.assertEqual(0, self.run_cli("menubar", "disable")[0])
            self.assertTrue(os.path.exists(off))         # start.sh looks for it
            self.assertIn("off", self.run_cli("menubar")[1])
            self.assertEqual(0, self.run_cli("menubar", "enable")[0])
            self.assertFalse(os.path.exists(off))


class FakeNoctalia:
    """The noctalia CLI as the tests see it: a small config tree, a plugin list, and every call
    kept. Enough of `msg config-get/config-set` and `msg plugins` for the bar code to be exercised
    against the same answers the running shell gives."""

    def __init__(self, quote="'"):
        self.cfg = {"bar": {"order": ["main"],
                            "main": {"enabled": True, "start": [], "center": [], "end": ["tray"]}},
                    "widget": {}}
        self.enabled = set()
        self.calls = []
        self.quote = quote          # how config-get quotes a string; its docs do not say which
        self.refuse = []            # predicates on argv: a call each of them matches fails
        self.empty = set()          # paths config-get answers with nothing at all, and exit 0

    def __call__(self, *argv):
        self.calls.append(argv)
        what = argv[1] if argv and argv[0] == "msg" else None
        if any(refused(argv) for refused in self.refuse):
            return 1, "", "error: refused"
        if what == "config-reload":
            return 0, "", ""
        if what == "config-get":
            if argv[2] in self.empty:
                return 0, "", ""
            value = self.get(argv[2])
            if value is None:
                return 1, "", f"error: path not found: {argv[2]}"
            return 0, f"{argv[2].rsplit('.', 1)[-1]} = {self.toml(value)}", ""
        if what == "config-set":
            self.set(argv[2], self.parse(argv[3]))
            return 0, "", ""
        if what == "plugins" and argv[2] in ("enable", "disable"):
            (self.enabled.add if argv[2] == "enable" else self.enabled.discard)(argv[3])
            return 0, "", ""
        if what == "plugins" and argv[2] == "list":
            rows = [f"{name} [local] 1.0.0 {'enabled' if name in self.enabled else 'disabled'}"
                    for name in sorted(self.enabled)]
            return 0, ("\n".join(rows) + "\n") if rows else "(no plugins)\n", ""
        return 1, "", f"unexpected: {argv!r}"

    def get(self, path):
        node = self.cfg
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node

    def set(self, path, value):
        parts = path.split(".")
        node = self.cfg
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def toml(self, value):
        if isinstance(value, bool):
            return "true" if value else "false"
        q = self.quote
        if isinstance(value, list):
            return "[ " + ", ".join(q + v + q for v in value) + " ]"
        return q + str(value) + q

    def parse(self, value):
        if value.startswith("["):
            return re.findall(r"""['"]([^'"]*)['"]""", value)
        return value[1:-1] if value[:1] in ("'", '"') and value[-1:] == value[:1] else value


class NoctaliaPlugin(unittest.TestCase):
    """The bar widget Noctalia loads (a Luau plugin, not QML), and `menubar` putting it where
    Noctalia looks for plugins and in the bar."""

    SRC = os.path.join(ROOT, "host", "linux", "noctalia")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plugins = os.path.join(self.tmp.name, "plugins")
        self.shell = os.path.join(self.tmp.name, "shell.json")   # never written: this is not Omarchy's bar
        self.fake = FakeNoctalia()
        # Both bars' plugins live under the temp dir, so a bar of the real machine never shows up here.
        for p in (mock.patch.object(cli, "NOCTALIA_PLUGINS", self.plugins),
                  mock.patch.object(cli, "OMARCHY_PLUGINS", os.path.join(self.tmp.name, "omarchy")),
                  mock.patch.object(cli, "SHELL_JSON", self.shell),
                  mock.patch.object(cli.platform, "system", return_value="Linux"),
                  mock.patch.object(cli, "noctalia", self.fake),
                  # Noctalia's own variable comes before XDG's, so it is emptied (which is unset): a
                  # machine that sets it must not have its real settings.toml read here.
                  mock.patch.dict(os.environ, {"XDG_STATE_HOME": os.path.join(self.tmp.name, "state"),
                                               "NOCTALIA_STATE_HOME": ""})):
            p.start()
            self.addCleanup(p.stop)

    def dir(self):
        # The id's tail: the name Noctalia rebuilds the manifest path from when enabling.
        return os.path.join(self.plugins, "layer-hud")

    def run_cli(self, *argv, which=lambda n: None if n.startswith("omarchy") else "/usr/bin/" + n):
        with mock.patch.object(cli.shutil, "which", side_effect=which), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = cli.main(list(argv))
        return code, out.getvalue() + err.getvalue()

    def test_the_manifest_is_one_noctalia_can_load(self):
        with open(os.path.join(self.SRC, "plugin.toml"), encoding="utf-8") as f:
            manifest = f.read()
        self.assertIn(f'id          = "{cli.NOCTALIA_ID}"', manifest)
        # Everything the widget uses is in the oldest API level, so it runs on any Noctalia v5.
        self.assertIn("plugin_api  = 3", manifest)
        self.assertIn(f'id    = "{cli.NOCTALIA_ENTRY}"', manifest)
        self.assertIn('entry = "widget.luau"', manifest)
        with open(os.path.join(self.SRC, "widget.luau"), encoding="utf-8") as f:
            script = f.read()
        for mark in ("__ZMK_LAYER_HUD_COMMAND__", "__ZMK_LAYER_HUD_STATE__", "__ZMK_LAYER_HUD_START_WITH__"):
            self.assertIn(mark, script)              # the marks `menubar enable` writes in
        for fn in ("function update()", "function onClick()", "function onRightClick()"):
            self.assertIn(fn, script)
        # The word on a HUD from another copy, as the macOS icon and the Omarchy widget take it by.
        self.assertIn('h.icon == "another copy"', script)

    def test_enable_copies_it_where_noctalia_looks_and_writes_the_marks_in(self):
        code, said = self.run_cli("menubar", "enable")
        self.assertEqual(0, code, said)
        with open(os.path.join(self.dir(), "widget.luau"), encoding="utf-8") as f:
            script = f.read()
        self.assertNotIn("__ZMK_LAYER_HUD_", script)
        self.assertIn(os.path.join(ROOT, "bin", "zmk-layer-hud"), script)
        self.assertIn(cli.STATE, script)
        self.assertIn(("msg", "config-reload"), self.fake.calls)
        self.assertIn(("msg", "plugins", "enable", cli.NOCTALIA_ID), self.fake.calls)
        self.assertIn(("msg", "config-set", f"widget.{cli.NOCTALIA_WIDGET}.type",
                       f"'{cli.NOCTALIA_ID}:{cli.NOCTALIA_ENTRY}'"), self.fake.calls)
        self.assertEqual(["tray", cli.NOCTALIA_WIDGET], self.fake.cfg["bar"]["main"]["end"])
        # Only `end`: start and center are other widgets' places.
        self.assertEqual([], self.fake.cfg["bar"]["main"]["start"])
        self.assertEqual([], self.fake.cfg["bar"]["main"]["center"])
        self.assertIn("Noctalia's bar", said)

    def test_enable_again_puts_it_in_only_once(self):
        self.run_cli("menubar", "enable")
        code, said = self.run_cli("menubar", "enable")
        self.assertEqual(0, code, said)
        self.assertEqual(1, self.fake.cfg["bar"]["main"]["end"].count(cli.NOCTALIA_WIDGET))

    def test_disable_takes_it_out_again(self):
        self.run_cli("menubar", "enable")
        # Even from a side it should never have been in: disable leaves no copy of it anywhere.
        self.fake.cfg["bar"]["main"]["start"] = [cli.NOCTALIA_WIDGET]
        code, said = self.run_cli("menubar", "disable")
        self.assertEqual(0, code, said)
        self.assertEqual(["tray"], self.fake.cfg["bar"]["main"]["end"])
        self.assertEqual([], self.fake.cfg["bar"]["main"]["start"])
        self.assertIn(("msg", "plugins", "disable", cli.NOCTALIA_ID), self.fake.calls)
        self.assertFalse(os.path.exists(self.dir()))
        self.assertIn("Noctalia's bar", said)

    def test_status_says_what_state_it_is_in(self):
        code, said = self.run_cli("menubar")
        self.assertEqual(0, code)
        self.assertIn("not installed", said)
        self.run_cli("menubar", "enable")
        code, said = self.run_cli("menubar")
        self.assertEqual(0, code)
        self.assertIn(f"in {self.dir()}", said)
        self.assertNotIn("but not in the bar", said)
        # Installed but out of the bar: said so.
        self.fake.cfg["bar"]["main"]["end"] = ["tray"]
        code, said = self.run_cli("menubar")
        self.assertEqual(0, code)
        self.assertIn("but not in the bar", said)

    # Without tomllib the saved settings are not read and nothing is said of them, which
    # test_status_claims_nothing_about_saved_settings_it_cannot_read holds on every Python.
    @unittest.skipUnless(tomllib, "the saved settings are read with tomllib (Python 3.11)")
    def test_status_says_when_the_bar_has_it_but_the_saved_settings_do_not(self):
        self.run_cli("menubar", "enable")
        state = os.path.join(self.tmp.name, "state", "noctalia")
        os.makedirs(state)
        settings = os.path.join(state, "settings.toml")
        with open(settings, "w", encoding="utf-8") as f:
            f.write('[bar.main]\nend = [ "tray", "zmk-hud" ]\n')
        code, said = self.run_cli("menubar")
        self.assertNotIn("saved settings", said)
        with open(settings, "w", encoding="utf-8") as f:
            f.write('[bar.main]\nend = [ "tray" ]\n')
        code, said = self.run_cli("menubar")
        self.assertEqual(0, code)
        self.assertIn("not in Noctalia's saved settings", said)
        self.assertNotIn("but not in the bar", said)

    def test_status_claims_nothing_about_saved_settings_it_cannot_read(self):
        self.run_cli("menubar", "enable")
        state = os.path.join(self.tmp.name, "state", "noctalia")
        self.assertIsNone(cli.noctalia_saved_placed())            # no file where it looks
        self.assertNotIn("saved settings", self.run_cli("menubar")[1])
        os.makedirs(state)
        settings = os.path.join(state, "settings.toml")
        with open(settings, "w", encoding="utf-8") as f:
            f.write("[bar.main\nend = ")                          # not TOML
        self.assertIsNone(cli.noctalia_saved_placed())
        self.assertNotIn("saved settings", self.run_cli("menubar")[1])
        with open(settings, "w", encoding="utf-8") as f:
            f.write('[bar.main]\nend = [ "tray" ]\n')              # would be said, if it could be read
        # Python 3.10 has no tomllib, and the README supports 3.10: status must still run.
        with mock.patch.dict(sys.modules, {"tomllib": None}):
            self.assertIsNone(cli.noctalia_saved_placed())
            code, said = self.run_cli("menubar")
        self.assertEqual(0, code, said)
        self.assertNotIn("saved settings", said)
        self.assertIn(f"in {self.dir()}", said)

    def test_noctalias_directories_are_found_the_way_the_shell_finds_them(self):
        # lib/util/file_utils.h, applicationXdgDir: NOCTALIA_<kind>_HOME, else XDG_<kind>_HOME, else
        # under the home directory, a variable set to nothing counting as not set.
        with mock.patch.dict(os.environ, {"NOCTALIA_DATA_HOME": "/n/data", "XDG_DATA_HOME": "/x/data"}):
            self.assertEqual("/n/data/noctalia", cli.noctalia_home("DATA", "~/.local/share"))
            os.environ["NOCTALIA_DATA_HOME"] = ""
            self.assertEqual("/x/data/noctalia", cli.noctalia_home("DATA", "~/.local/share"))
            os.environ["XDG_DATA_HOME"] = ""
            self.assertEqual(os.path.expanduser("~/.local/share/noctalia"), cli.noctalia_home("DATA", "~/.local/share"))
            del os.environ["NOCTALIA_DATA_HOME"], os.environ["XDG_DATA_HOME"]
            self.assertEqual(os.path.expanduser("~/.local/share/noctalia"), cli.noctalia_home("DATA", "~/.local/share"))

    @unittest.skipUnless(tomllib, "the saved settings are read with tomllib (Python 3.11)")
    def test_the_saved_settings_are_read_from_noctalias_own_state_home_when_it_is_set(self):
        self.run_cli("menubar", "enable")
        for home, end in (("state", '"tray", "zmk-hud"'), ("profile", '"tray"')):
            os.makedirs(os.path.join(self.tmp.name, home, "noctalia"))
            with open(os.path.join(self.tmp.name, home, "noctalia", "settings.toml"), "w", encoding="utf-8") as f:
                f.write(f"[bar.main]\nend = [ {end} ]\n")
        self.assertNotIn("saved settings", self.run_cli("menubar")[1])    # XDG_STATE_HOME's has it
        with mock.patch.dict(os.environ, {"NOCTALIA_STATE_HOME": os.path.join(self.tmp.name, "profile")}):
            self.assertIn("not in Noctalia's saved settings", self.run_cli("menubar")[1])

    def bar_writes(self):
        return [c for c in self.fake.calls if c[:2] == ("msg", "config-set") and c[2].startswith("bar.")]

    def test_an_array_of_strings_is_read_in_either_quoting_or_not_at_all(self):
        read = cli.noctalia_strings
        self.assertEqual(["tray", "clock"], read("[ 'tray', 'clock' ]"))
        self.assertEqual(["tray", "clock"], read('["tray","clock",]'))
        self.assertEqual(["a\"b", "c'd", "e\\f"], read(r'''[ "a\"b", "c'd", "e\\f" ]'''))
        self.assertEqual(["x", "y"], read("[\n  'x',\n  \"y\",\n]\n"))
        self.assertEqual([], read("[]"))
        self.assertEqual([], read("[ ]"))
        # Anything else is "cannot read it", never an empty list: written back over, it would
        # drop whatever the list held.
        for unreadable in ("", "tray", "[ 1, 2 ]", "[ 'a' 'b' ]", "[ 'a', , 'b' ]", "[ 'a'",
                           "[ # note\n 'a' ]", r'[ "\q" ]', "[ 'a', tray ]", "'a'"):
            self.assertIsNone(read(unreadable), unreadable)

    def test_a_string_is_written_as_a_literal_where_it_can_be(self):
        self.assertEqual("['tray', 'zmk-hud']", cli.noctalia_toml_array(["tray", "zmk-hud"]))
        self.assertEqual("[\"it's\"]", cli.noctalia_toml_array(["it's"]))
        self.assertEqual('["a\\\\b\\n"]', cli.noctalia_toml_array(["a\\b\n"]))  # a control character needs escaping
        self.assertEqual("'zmk/layer-hud:hud'", cli.noctalia_toml_string("zmk/layer-hud:hud"))

    def test_config_get_with_or_without_the_path_in_front_is_the_same_value(self):
        for said in ("end = [ 'tray' ]", "bar.main.end = [ 'tray' ]", "[ 'tray' ]", "bar.main.enabled = true"):
            with mock.patch.object(cli, "noctalia", lambda *a, said=said: (0, said + "\n", "")):
                text = cli.noctalia_config("x")
                self.assertTrue(text.startswith("[") or text == "true", text)
        with mock.patch.object(cli, "noctalia", lambda *a: (0, "\n", "")):
            self.assertIsNone(cli.noctalia_config("x"))             # an empty reply says nothing
        with mock.patch.object(cli, "noctalia", lambda *a: (1, "", "error: unknown path")):
            self.assertIsNone(cli.noctalia_config("x"))
        with mock.patch.object(cli, "noctalia", lambda *a: (0, "'a=b'\n", "")):
            self.assertEqual("'a=b'", cli.noctalia_config("x"))     # an = inside a string is not the key's

    def test_a_double_quoted_bar_list_is_read_and_everything_in_it_is_kept(self):
        self.fake.quote = '"'
        self.fake.cfg["bar"]["main"]["end"] = ["tray", "clock"]
        code, said = self.run_cli("menubar", "enable")
        self.assertEqual(0, code, said)
        self.assertEqual(["tray", "clock", cli.NOCTALIA_WIDGET], self.fake.cfg["bar"]["main"]["end"])
        self.assertEqual(0, self.run_cli("menubar", "enable")[0])
        self.assertEqual(1, self.fake.cfg["bar"]["main"]["end"].count(cli.NOCTALIA_WIDGET))

    def test_enable_says_so_when_the_shell_will_not_take_the_bar_list(self):
        self.fake.refuse.append(lambda argv: argv[1] == "config-set" and argv[2] == "bar.main.end")
        code, said = self.run_cli("menubar", "enable")
        self.assertEqual(1, code)
        self.assertIn("bar.main.end was not taken: error: refused", said)
        self.assertNotIn("already has", said)
        self.assertEqual(["tray"], self.fake.cfg["bar"]["main"]["end"])

    def test_enable_does_not_write_over_a_list_it_could_not_read(self):
        # Unset, so config-get errors: and answering with nothing at all (older shells did that for an
        # unknown or a defaulted path) is no more an empty list than an error is.
        del self.fake.cfg["bar"]["main"]["end"]
        code, said = self.run_cli("menubar", "enable")
        self.assertEqual(1, code)
        self.assertIn("bar.main.end could not be read", said)
        self.assertEqual([], self.bar_writes())
        self.fake.cfg["bar"]["main"]["end"] = ["tray", "clock"]
        self.fake.empty.add("bar.main.end")
        code, said = self.run_cli("menubar", "enable")
        self.assertEqual(1, code)
        self.assertEqual([], self.bar_writes())
        self.assertEqual(["tray", "clock"], self.fake.cfg["bar"]["main"]["end"])

    def test_enable_without_the_bars_listed_is_not_already_there(self):
        self.fake.empty.add("bar.order")
        code, said = self.run_cli("menubar", "enable")
        self.assertEqual(1, code)
        self.assertIn("does not say which bars there are", said)
        self.assertNotIn("already has", said)
        self.assertEqual([], self.bar_writes())
        # And a config whose every bar is switched off has none to put it in.
        self.fake.empty.clear()
        self.fake.cfg["bar"]["main"]["enabled"] = False
        code, said = self.run_cli("menubar", "enable")
        self.assertEqual(1, code)
        self.assertIn("switched on", said)
        self.assertNotIn("already has", said)

    def test_enable_puts_it_in_the_bars_that_take_it_and_says_about_the_rest(self):
        self.fake.cfg["bar"]["order"] = ["main", "second"]
        self.fake.cfg["bar"]["second"] = {"enabled": True, "start": [], "center": [], "end": ["clock"]}
        self.fake.refuse.append(lambda argv: argv[1] == "config-set" and argv[2] == "bar.second.end")
        code, said = self.run_cli("menubar", "enable")
        self.assertEqual(1, code)
        self.assertIn("the HUD's icon is in Noctalia's bar", said)
        self.assertIn("not in every bar", said)
        self.assertEqual(["tray", cli.NOCTALIA_WIDGET], self.fake.cfg["bar"]["main"]["end"])
        self.assertEqual(["clock"], self.fake.cfg["bar"]["second"]["end"])

    def test_a_bar_named_so_that_no_config_path_carries_it_is_left_alone(self):
        self.fake.cfg["bar"]["order"] = ["my.bar"]
        self.fake.cfg["bar"]["my.bar"] = {"enabled": True, "start": [], "center": [], "end": ["tray"]}
        code, said = self.run_cli("menubar", "enable")
        self.assertEqual(1, code)
        self.assertIn("a name a config path cannot carry", said)
        self.assertEqual([], self.bar_writes())

    def test_disable_with_the_shell_down_keeps_the_plugin_so_a_later_disable_can_finish(self):
        self.run_cli("menubar", "enable")
        with mock.patch.object(cli, "noctalia", lambda *a: None):
            code, said = self.run_cli("menubar", "disable")
        self.assertEqual(1, code)
        self.assertNotIn("is out of", said)                       # it is not, and is not said to be
        self.assertIn("does not say which bars there are", said)
        self.assertIn("left in place", said)
        self.assertTrue(os.path.isdir(self.dir()))
        self.assertIn(cli.NOCTALIA_WIDGET, self.fake.cfg["bar"]["main"]["end"])
        code, said = self.run_cli("menubar", "disable")             # the shell is back
        self.assertEqual(0, code, said)
        self.assertEqual(["tray"], self.fake.cfg["bar"]["main"]["end"])
        self.assertFalse(os.path.exists(self.dir()))
        self.assertIn("Noctalia's bar", said)

    def test_disable_with_a_bar_list_the_shell_will_not_take_keeps_the_plugin_too(self):
        self.run_cli("menubar", "enable")
        self.fake.refuse.append(lambda argv: argv[1] == "config-set" and argv[2] == "bar.main.end")
        code, said = self.run_cli("menubar", "disable")
        self.assertEqual(1, code)
        self.assertIn("bar.main.end was not taken", said)
        self.assertTrue(os.path.isdir(self.dir()))
        self.assertNotIn(("msg", "plugins", "disable", cli.NOCTALIA_ID), self.fake.calls)

    def test_disable_takes_a_bar_entry_out_when_the_folder_is_already_gone(self):
        self.run_cli("menubar", "enable")
        shutil.rmtree(self.dir())
        code, said = self.run_cli("menubar", "disable")
        self.assertEqual(0, code, said)
        self.assertEqual(["tray"], self.fake.cfg["bar"]["main"]["end"])
        self.assertIn("Noctalia's bar", said)

    def test_disable_with_nothing_installed_is_quiet_even_with_the_shell_down(self):
        with mock.patch.object(cli, "noctalia", lambda *a: None):
            code, said = self.run_cli("menubar", "disable")
        self.assertEqual(0, code, said)
        self.assertIn("not installed", said)
        self.assertNotIn("Noctalia:", said)

    def test_disable_without_noctalia_on_the_path_has_only_the_folder_to_take(self):
        self.run_cli("menubar", "enable")
        calls = len(self.fake.calls)
        code, said = self.run_cli("menubar", "disable",
                                  which=lambda n: None if n.startswith("omarchy") or n == "noctalia" else "/usr/bin/" + n)
        self.assertEqual(0, code, said)
        self.assertFalse(os.path.exists(self.dir()))
        self.assertEqual(calls, len(self.fake.calls))                # nothing was asked

    def test_disable_says_when_the_shell_would_not_disable_the_plugin_but_still_takes_the_folder(self):
        self.run_cli("menubar", "enable")
        self.fake.refuse.append(lambda argv: argv[1:3] == ("plugins", "disable"))
        code, said = self.run_cli("menubar", "disable")
        self.assertEqual(1, code)
        self.assertIn("would not disable", said)
        self.assertIn("noctalia msg plugins disable", said)
        self.assertEqual(["tray"], self.fake.cfg["bar"]["main"]["end"])
        self.assertFalse(os.path.exists(self.dir()))
        self.assertNotIn("left in place", said)

    def test_status_says_when_the_shell_does_not_list_the_bars(self):
        self.run_cli("menubar", "enable")
        self.fake.empty.add("bar.order")
        code, said = self.run_cli("menubar")
        self.assertEqual(0, code)
        self.assertIn("does not say which bars there are", said)
        self.assertNotIn("but not in the bar", said)

    # The directory is named for the tail of the id alone (`layer-hud`), which another author's
    # plugin can have too: only one whose plugin.toml carries this plugin's id is ever removed or
    # written over.

    THEIRS = 'id = "someone/layer-hud"\nname = "Theirs"\nversion = "1.0.0"\nplugin_api = 3\n'

    def someone_elses(self, manifest=THEIRS):
        os.makedirs(self.dir())
        if manifest is not None:
            with open(os.path.join(self.dir(), "plugin.toml"), "w", encoding="utf-8") as f:
                f.write(manifest)
        with open(os.path.join(self.dir(), "widget.luau"), "w", encoding="utf-8") as f:
            f.write("-- theirs\n")

    def assert_theirs_is_untouched(self, manifest=THEIRS):
        self.assertEqual(["layer-hud"], os.listdir(self.plugins))      # and nothing staged beside it
        self.assertEqual(sorted(["widget.luau"] + (["plugin.toml"] if manifest is not None else [])),
                         sorted(os.listdir(self.dir())))
        with open(os.path.join(self.dir(), "widget.luau"), encoding="utf-8") as f:
            self.assertEqual("-- theirs\n", f.read())
        if manifest is not None:
            with open(os.path.join(self.dir(), "plugin.toml"), encoding="utf-8") as f:
                self.assertEqual(manifest, f.read())

    OURS = b'id = "zmk/layer-hud"\n'

    # Manifests the reader takes an id from: the id is the first statement, written plainly.
    READ = (
        (b'id = "a/b"\n', "a/b"),
        (b'id = "a/b"', "a/b"),                                           # no newline at the end
        (b"id='a/b'   # the id\r\n", "a/b"),                              # TOML's other line ending
        (b'"id" = "a/b"\n', "a/b"),
        (b"'id'\t=\t'a/b'\n", "a/b"),
        (b'# a comment\n\n  \t\n\r\nid = "a/b"\nname = "B"\n', "a/b"),
        ('# café   still the comment\nid = "a/b" # café\n'.encode("utf-8"), "a/b"),
        # A `[[widget]]`'s own id, further down, is not the plugin's.
        (b'id = "a/b"\n\n[[widget]]\nid = "zmk/layer-hud"\n', "a/b"),
    )

    # Manifests it takes none from, each with this plugin's id somewhere a careless reader finds it.
    UNREAD = (
        # Someone else's valid manifest, with our id as a line of a multi-line string.
        b'description = """\nid = "zmk/layer-hud"\n"""\nid = "someone/layer-hud"\n',
        b"description = '''\nid = \"zmk/layer-hud\"\n'''\nid = \"someone/layer-hud\"\n",
        b'description = """Shows"""\nid = "zmk/layer-hud"\n',
        b'id = """zmk/layer-hud"""\n',
        b'id = """\nzmk/layer-hud"""\n',
        # Not the first statement: after another key, inside an array, under a table header.
        b'name = "B"\n' + OURS,
        b'tags = [\n' + OURS + b']\n',
        b'[[widget]]\n' + OURS,
        b'[plugin]\n' + OURS,
        # Whitespace and line endings that are not TOML's, which Python's own would let through.
        'id = "zmk/layer-hud"\n'.encode("utf-8"),
        ' id = "zmk/layer-hud"\n'.encode("utf-8"),
        b'id\x0c= "zmk/layer-hud"\n',
        b'id\x0b= "zmk/layer-hud"\n',
        b'name = "B"\r' + OURS,
        b'# a comment\r' + OURS,
        b'\r' + OURS,
        b'id = "zmk/layer-hud"\r',
        b'id = "zmk/layer-hud"\r\r\n',
        'name = "B\u0085id = "zmk/layer-hud"\n'.encode("utf-8"),
        '# a comment id = "zmk/layer-hud"\n'.encode("utf-8"),
        b'# a comment\x00\n' + OURS,
        b'\xef\xbb\xbf' + OURS,                                          # a byte order mark
        # An id that is not one plain string, or not an id.
        b"", b"\n\n# nothing\n", b"name = 'B'\n", b"id = 3\n", b'id = "zmk\\u002flayer-hud"\n',
        b'idx = "zmk/layer-hud"\n', b'id.x = "zmk/layer-hud"\n', b'id = "zmk/layer-hud" "c"\n',
        b'id = "zmk/layer-hud", name = "B"\n', b'id = "zmk/layer-hud\n', b'id = "zmk/layer-hud\x7f"\n',
        b'id = "zmk/layer-hud\xff"\n', b'id == "zmk/layer-hud"\n', b'= "zmk/layer-hud"\n',
    )

    def manifest_id(self, data):
        where = tempfile.mkdtemp(dir=self.tmp.name)
        with open(os.path.join(where, "plugin.toml"), "wb") as f:
            f.write(data)
        return cli.noctalia_manifest_id(where)

    def test_the_id_of_a_manifest_is_read_without_tomllib(self):
        # Python 3.10 has no tomllib, and the README supports 3.10.
        with mock.patch.dict(sys.modules, {"tomllib": None}):
            # The manifest this ships, as it is written: were its id ever written a way this does
            # not read, `enable` would take its own older copy for someone else's.
            self.assertEqual(cli.NOCTALIA_ID, cli.noctalia_manifest_id(self.SRC))
            for data, said in self.READ:
                self.assertEqual(said, self.manifest_id(data), data)
        self.assertIsNone(cli.noctalia_manifest_id(os.path.join(self.tmp.name, "nowhere")))

    def test_an_id_the_reader_cannot_be_sure_of_is_no_id(self):
        # What is read as this plugin's id gets the directory removed, so what is not plainly the
        # manifest's first statement is not read at all: None, never a guess.
        for data in self.UNREAD:
            self.assertIsNone(self.manifest_id(data), data)

    @unittest.skipUnless(tomllib, "compared with tomllib (Python 3.11)")
    def test_whatever_id_the_reader_gives_is_the_one_a_toml_parser_gives(self):
        # One way only: the reader may give no id where a parser finds one (it reads less), never
        # one a parser does not find. Every shape above, and the manifest this ships.
        with open(os.path.join(self.SRC, "plugin.toml"), "rb") as f:
            shipped = f.read()
        self.assertEqual(cli.NOCTALIA_ID, tomllib.loads(shipped.decode("utf-8"))["id"])
        for data in (shipped,) + tuple(d for d, _ in self.READ) + self.UNREAD:
            said = self.manifest_id(data)
            if said is not None:
                self.assertEqual(said, tomllib.loads(data.decode("utf-8")).get("id"), data)

    def test_someone_elses_manifest_with_this_plugins_id_inside_a_string_is_left_alone(self):
        # Valid TOML whose id is someone/layer-hud: read a line at a time without knowing of the
        # string, it was taken for this plugin, written over by `enable` and removed by `disable`.
        manifest = ('description = """\nid = "zmk/layer-hud"\n"""\nid = "someone/layer-hud"\n'
                    'name = "Theirs"\nversion = "1.0.0"\nplugin_api = 3\n')
        self.someone_elses(manifest)
        self.assertFalse(cli.noctalia_installed())
        code, said = self.run_cli("menubar", "enable")
        self.assertEqual(1, code)
        self.assertIn("is not this plugin's", said)
        self.assert_theirs_is_untouched(manifest)
        code, said = self.run_cli("menubar", "disable")
        self.assertEqual(1, code)
        self.assertIn("is not this plugin's", said)
        self.assert_theirs_is_untouched(manifest)
        self.assertIn("is not this plugin's", self.uninstall_step())
        self.assert_theirs_is_untouched(manifest)

    def test_enable_leaves_another_plugins_directory_alone_and_says_so(self):
        self.someone_elses()
        self.assertFalse(cli.noctalia_installed())
        code, said = self.run_cli("menubar", "enable")
        self.assertEqual(1, code)
        self.assertIn(f"{self.dir()} is not this plugin's", said)
        self.assertIn("left alone", said)
        self.assertNotIn("Noctalia's bar", said)
        self.assert_theirs_is_untouched()
        self.assertEqual([], self.fake.calls)                       # and the shell was asked nothing
        self.assertEqual(["tray"], self.fake.cfg["bar"]["main"]["end"])

    def test_enable_takes_nothing_it_cannot_tell_is_this_plugin_for_this_plugin(self):
        # No manifest, one that does not parse, one with no id, and one that only names this
        # plugin's id further down, where it is something else's id.
        for manifest in (None, "[[[", 'name = "ZMK Layer HUD"\n',
                         f'id = "someone/layer-hud"\n\n[[widget]]\nid = "{cli.NOCTALIA_ID}"\n'):
            with self.subTest(manifest=manifest):
                self.someone_elses(manifest)
                code, said = self.run_cli("menubar", "enable")
                self.assertEqual(1, code)
                self.assertIn("is not this plugin's", said)
                self.assert_theirs_is_untouched(manifest)
                shutil.rmtree(self.dir())
        with open(self.dir(), "w", encoding="utf-8") as f:              # not even a directory
            f.write("a file\n")
        code, said = self.run_cli("menubar", "enable")
        self.assertEqual(1, code)
        self.assertIn("is not this plugin's", said)
        with open(self.dir(), encoding="utf-8") as f:
            self.assertEqual("a file\n", f.read())
        self.assertEqual([], self.fake.calls)

    def test_enable_writes_over_an_older_copy_of_this_plugin_and_leaves_nothing_staged(self):
        self.run_cli("menubar", "enable")
        with open(os.path.join(self.dir(), "old.luau"), "w", encoding="utf-8") as f:
            f.write("-- from a release before\n")
        code, said = self.run_cli("menubar", "enable")
        self.assertEqual(0, code, said)
        self.assertEqual(sorted(os.listdir(self.SRC)), sorted(os.listdir(self.dir())))
        self.assertEqual(["layer-hud"], os.listdir(self.plugins))

    def test_an_install_that_fails_leaves_nothing_staged_and_the_copy_that_was_there(self):
        self.run_cli("menubar", "enable")
        with open(os.path.join(self.dir(), "widget.luau"), encoding="utf-8") as f:
            before = f.read()
        with mock.patch.object(cli, "bake_marks", side_effect=OSError("disk full")), \
                mock.patch.object(cli.shutil, "which", return_value=None):
            with self.assertRaises(OSError):
                cli.install_noctalia_plugin()
        self.assertEqual(["layer-hud"], os.listdir(self.plugins))
        with open(os.path.join(self.dir(), "widget.luau"), encoding="utf-8") as f:
            self.assertEqual(before, f.read())

    def test_disable_leaves_another_plugins_directory_alone_and_fails_saying_so(self):
        self.someone_elses()
        code, said = self.run_cli("menubar", "disable")
        self.assertEqual(1, code)
        self.assertIn(f"{self.dir()} is not this plugin's", said)
        self.assertIn("left alone", said)
        self.assertNotIn("is out of", said)
        self.assertNotIn("left in place", said)                     # that is said of ours, to finish later
        self.assertNotIn("not installed", said)                     # nor is it passed over as an empty place
        self.assert_theirs_is_untouched()
        self.assertNotIn(("msg", "plugins", "disable", cli.NOCTALIA_ID), self.fake.calls)

    def test_disable_takes_the_icon_out_of_the_bar_and_still_leaves_another_plugins_directory(self):
        # This plugin was enabled once, and its directory has since become someone else's.
        self.fake.cfg["bar"]["main"]["end"] = ["tray", cli.NOCTALIA_WIDGET]
        self.someone_elses()
        code, said = self.run_cli("menubar", "disable")
        self.assertEqual(1, code)
        self.assertEqual(["tray"], self.fake.cfg["bar"]["main"]["end"])
        self.assertIn(("msg", "plugins", "disable", cli.NOCTALIA_ID), self.fake.calls)
        self.assertIn("is not this plugin's", said)
        self.assertIn("left alone", said)
        self.assert_theirs_is_untouched()

    def test_disable_without_noctalia_on_the_path_leaves_another_plugins_directory_too(self):
        self.someone_elses()
        code, said = self.run_cli("menubar", "disable",
                                  which=lambda n: None if n.startswith("omarchy") or n == "noctalia" else "/usr/bin/" + n)
        self.assertEqual(1, code)
        self.assertIn("is not this plugin's", said)
        self.assert_theirs_is_untouched()
        self.assertEqual([], self.fake.calls)

    def test_status_does_not_call_another_plugins_directory_installed(self):
        self.someone_elses()
        code, said = self.run_cli("menubar")
        self.assertEqual(0, code)
        self.assertIn("is not installed", said)
        self.assertIn(f"{self.dir()} is not this plugin's", said)
        self.assertIn("will not replace it", said)
        self.assertNotIn(f"is in {self.dir()}", said)
        self.assertEqual([], self.fake.calls)                       # nothing to ask the shell about
        self.assert_theirs_is_untouched()

    def test_a_link_to_this_plugin_is_taken_as_a_link_and_what_it_points_at_stays(self):
        elsewhere = os.path.join(self.tmp.name, "checkout")
        shutil.copytree(self.SRC, elsewhere)
        os.makedirs(self.plugins)
        os.symlink(elsewhere, self.dir())
        self.assertTrue(cli.noctalia_installed())
        code, said = self.run_cli("menubar", "enable")              # a copy of regular files, as ever
        self.assertEqual(0, code, said)
        self.assertFalse(os.path.islink(self.dir()))
        self.assertEqual(sorted(os.listdir(self.SRC)), sorted(os.listdir(elsewhere)))
        shutil.rmtree(self.dir())
        os.symlink(elsewhere, self.dir())
        code, said = self.run_cli("menubar", "disable")
        self.assertEqual(0, code, said)
        self.assertFalse(os.path.lexists(self.dir()))
        self.assertEqual(sorted(os.listdir(self.SRC)), sorted(os.listdir(elsewhere)))

    def uninstall_step(self):
        out = io.StringIO()
        with mock.patch.object(cli.shutil, "which", side_effect=lambda n: "/usr/bin/" + n), \
                contextlib.redirect_stdout(out):
            cli.noctalia_uninstall()
        return out.getvalue()

    def test_uninstall_takes_the_icon_out_and_says_what_is_left_to_do_by_hand_when_it_cannot(self):
        self.run_cli("menubar", "enable")
        with mock.patch.object(cli, "noctalia", lambda *a: None):
            said = self.uninstall_step()
        self.assertIn("does not say which bars there are", said)
        self.assertIn(f"left {self.dir()} in place", said)
        self.assertIn(cli.NOCTALIA_WIDGET, said)
        self.assertNotIn("took the bar plugin out", said)
        self.assertTrue(os.path.isdir(self.dir()))
        said = self.uninstall_step()                                # the shell is back
        self.assertIn("took the bar plugin out of Noctalia", said)
        self.assertFalse(os.path.exists(self.dir()))
        self.assertEqual(["tray"], self.fake.cfg["bar"]["main"]["end"])
        self.assertEqual("", self.uninstall_step())                 # and again, nothing to say

    def test_uninstall_leaves_another_plugins_directory_alone_and_says_so(self):
        self.someone_elses()
        said = self.uninstall_step()
        self.assertIn(f"{self.dir()} is not this plugin's", said)
        self.assertIn("left alone", said)
        self.assertNotIn("took the bar plugin out", said)
        self.assertNotIn("then delete it", said)                    # it is not ours to have deleted
        self.assert_theirs_is_untouched()

    def test_the_marks_are_written_into_the_widget_as_a_string_literal_a_quote_cannot_end(self):
        text = '"__ZMK_LAYER_HUD_COMMAND__" "__ZMK_LAYER_HUD_STATE__" "__ZMK_LAYER_HUD_START_WITH__"'
        with mock.patch.object(cli, "ROOT", 'a"b\\c'), mock.patch.object(cli, "STATE", 'st"a\\te'), \
                mock.patch.object(cli.shutil, "which", return_value=None):
            baked = cli.bake_marks(text)
        self.assertEqual(r'"a\"b\\c/bin/zmk-layer-hud" "st\"a\\te" ""', baked)

    def update_runs(self):
        """What an `update` of an installed tree runs once the new tree is in place."""
        root = os.path.join(self.tmp.name, "tree")
        os.makedirs(os.path.join(root, "bin"))
        with open(os.path.join(root, "bin", "zmk-layer-hud"), "w") as f:
            f.write("old\n")
        cli.write_installed(root, "v1.0.0", "1" * 40)

        def fetch(ref, dest):
            os.makedirs(os.path.join(dest, "bin"))
            with open(os.path.join(dest, "bin", "zmk-layer-hud"), "w") as f:
                f.write("new\n")
            return "2" * 40
        args = cli.build_parser().parse_args(["update"])
        with mock.patch.object(cli, "ROOT", root), mock.patch.object(cli, "in_clone", lambda: False), \
                mock.patch.object(cli, "fetch_tree", fetch), mock.patch.object(cli, "latest_release", lambda: "v1.1.0"), \
                mock.patch.object(cli, "processes", lambda pattern: {}), mock.patch.object(cli, "venv_ok", lambda: False), \
                mock.patch.object(cli.subprocess, "call", return_value=0) as call, \
                mock.patch.dict(os.environ, {"ZMKHUD_REF": ""}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, cli.cmd_update(args))
        return root, [c[0][0] for c in call.call_args_list]

    def test_update_refreshes_the_noctalia_plugin_when_it_is_the_only_one_installed(self):
        shutil.copytree(self.SRC, self.dir())
        root, ran = self.update_runs()
        self.assertIn([os.path.join(root, "bin", "zmk-layer-hud"), "menubar", "enable"], ran)

    def test_update_does_not_take_another_plugins_directory_for_an_install_to_refresh(self):
        self.someone_elses()
        root, ran = self.update_runs()
        self.assertNotIn([os.path.join(root, "bin", "zmk-layer-hud"), "menubar", "enable"], ran)
        self.assert_theirs_is_untouched()

    def test_without_a_bar_here_it_says_so(self):
        code, said = self.run_cli("menubar", "enable", which=lambda n: None)
        self.assertEqual(1, code)
        self.assertIn("toggle", said)
        self.assertFalse(os.path.exists(self.plugins))


def desktop_exec(text):
    """The argv a desktop entry's Exec says, undone the way the spec does it: the string value's
    escapes, then the quoting, then the doubled %."""
    import configparser
    import shlex
    entry = configparser.ConfigParser(interpolation=None)
    entry.optionxform = str
    entry.read_string(text)
    value = entry["Desktop Entry"]["Exec"].replace("\\\\", "\\")
    return [a.replace("%%", "%") for a in shlex.split(value)]


class SyncDue(unittest.TestCase):
    """The doctor's word that a sync is due: a file the definitions are made from (sync.inputs),
    newer than they are."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.d)
        self.config = os.path.join(self.d, "hud.yaml")
        repo = os.path.join(self.d, "repo")
        self.files = {
            "hud.yaml": "keymap: drawing/board.yaml\n",
            "drawing/board.yaml": "layout: {qmk_info_json: docs/layout.json}\nlayers: {}\n",
            "docs/layout.json": "{}",
            "repo/config/b.keymap": '#include "defs.dtsi"\n' + __import__("sync_test").KEYMAP,
            "repo/config/defs.dtsi": "#define X 1\n",
            "repo/config/b.overlay": "/ { };\n",
            "hud.definitions.json": json.dumps({"version": 1, "drawing": {}, "sources": {"repo": repo, "keyboard": "b"}}),
        }
        for name, text in self.files.items():
            path = os.path.join(self.d, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            os.utime(path, (1000, 1000))
        os.utime(os.path.join(self.d, "hud.definitions.json"), (2000, 2000))
        p = mock.patch.dict(os.environ, {"ZMKHUD_CONFIG": self.config})
        p.start()
        self.addCleanup(p.stop)

    def touch(self, name):
        os.utime(os.path.join(self.d, name), (3000, 3000))

    def test_nothing_newer_is_no_sync_due(self):
        self.assertIsNone(cli.sync_due())

    def test_a_drawings_layout_file_newer_is_a_sync_due(self):
        self.touch("docs/layout.json")
        self.assertEqual("layout.json", cli.sync_due())

    def test_a_file_the_keymap_includes_newer_is_a_sync_due(self):
        self.touch("repo/config/defs.dtsi")
        self.assertEqual("defs.dtsi", cli.sync_due())

    def test_a_board_file_the_drawing_is_not_made_from_is_not_a_sync_due(self):
        # With keymap: naming a drawing, the physical layout is the drawing's: the keyboard's
        # .overlay is never read, and a sync would leave the definitions as they are.
        self.touch("repo/config/b.overlay")
        self.assertIsNone(cli.sync_due())

    def test_the_config_alone_is_not(self):
        # Its settings are read by the HUD as they are; only what it names is synced.
        self.touch("hud.yaml")
        self.assertIsNone(cli.sync_due())


class Doctor(unittest.TestCase):
    """What the doctor says of secure input: the feed's own report of its probes, one row for what
    sees a secret being typed and one per probe that should and does not."""

    def rows(self, stdout):
        results = []
        done = subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")
        with mock.patch.object(cli, "venv_ok", return_value=True), \
                mock.patch.object(cli.subprocess, "run", return_value=done) as run, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            cli.check_secure(results)
        self.assertIn("--probes", run.call_args[0][0])
        return results, out.getvalue()

    def test_what_sees_a_secret_is_one_row_and_what_does_not_one_each(self):
        results, said = self.rows("carbon\tnone\tnot macOS\nterminals\ton\ta prompt is seen\n"
                                  "a11y\toff\tjeepney is not in the venv (zmk-layer-hud setup)\nlock\ton\tHyprland says unlocked\n")
        self.assertEqual([cli.OK, cli.WARN], results)
        self.assertIn("seen by the terminals' prompts, the session lock", said)
        self.assertIn("secure input: the accessibility bus: jeepney is not in the venv", said)

    def test_a_probe_that_sees_only_what_is_named_says_so_in_the_row(self):
        # niri's session lock: a locker is seen by its process name alone, which is not the same as seen.
        results, said = self.rows("carbon\tnone\tnot macOS\nterminals\ton\ta prompt is seen\n"
                                  "a11y\ton\tthe apps expose their widgets\nlock\tpartial\tby process name\n")
        self.assertEqual([cli.OK], results)
        self.assertIn("seen by the terminals' prompts, the accessibility bus, "
                      "the session lock (by process name only: feed.lockers adds more)", said)

    def test_nothing_watching_is_a_warning_with_what_to_do(self):
        results, said = self.rows("carbon\tnone\tnot macOS\nterminals\toff\tno terminals' directory\n"
                                  "a11y\toff\tno bus\nlock\toff\tno Hyprland\n")
        self.assertEqual(cli.WARN, results[0])
        self.assertIn("nothing here sees a secret being typed", said)
        self.assertIn("stop the HUD before typing one", said)

    def test_without_a_venv_the_bus_alone_is_asked(self):
        with mock.patch.object(cli, "venv_ok", return_value=False), \
                mock.patch.object(cli, "check_accessibility") as fallback:
            cli.check_secure([])
        self.assertTrue(fallback.called)


class Autostart(unittest.TestCase):
    """The login item: what it runs, and enable/disable putting it there and taking it out."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        for p in (mock.patch.object(cli, "LAUNCH_AGENT", os.path.join(t, "LaunchAgents", "agent.plist")),
                  mock.patch.object(cli, "LOGIN_APP", os.path.join(t, "Support", "ZMK Layer HUD.app")),
                  mock.patch.object(cli, "AUTOSTART_DESKTOP", os.path.join(t, "autostart", "zmk-layer-hud.desktop")),
                  mock.patch.object(cli, "STATE", os.path.join(t, "state dir")),
                  mock.patch.object(cli, "definitions_missing", lambda: None),
                  mock.patch.object(cli, "build_launcher", lambda: True)):
            p.start()
            self.addCleanup(p.stop)

    def run_cli(self, *argv, system="Darwin"):
        with mock.patch.object(cli.platform, "system", return_value=system), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = cli.main(list(argv))
        return code, out.getvalue() + err.getvalue()

    def test_the_launch_agent_starts_it_hidden_through_the_app(self):
        import plistlib
        a = plistlib.loads(cli.launch_agent_plist())
        self.assertEqual(cli.APP_ID, a["Label"])
        self.assertEqual([cli.launcher_path(), os.path.join(ROOT, "bin", "zmk-layer-hud"), "start", "--hidden",
                          "--foreground"], a["ProgramArguments"])
        self.assertEqual(cli.STATE, a["EnvironmentVariables"]["ZMKHUD_STATE"])
        self.assertEqual((True, "Aqua"), (a["RunAtLoad"], a["LimitLoadToSessionType"]))
        self.assertNotIn("KeepAlive", a)          # a HUD that is stopped stays stopped

    def test_the_logs_are_this_users_alone_even_from_before(self):
        # launchd makes autostart.log with the default umask, and `>` keeps a file's mode: both logs
        # are made 0600 first, the login item's kept for launchd to append to.
        os.makedirs(cli.STATE)
        auto, menu = os.path.join(cli.STATE, "autostart.log"), os.path.join(cli.STATE, "menubar.log")
        for log in (auto, menu):
            with open(log, "w") as f:
                f.write("from before\n")
            os.chmod(log, 0o644)
        code, said = self.run_cli("autostart", "enable")
        self.assertEqual(0, code, said)
        self.assertEqual(0o600, stat.S_IMODE(os.stat(auto).st_mode))
        with open(auto) as f:
            self.assertEqual("from before\n", f.read())
        with mock.patch.object(cli, "venv_ok", return_value=True), mock.patch.object(cli, "pgrep", return_value=False), \
             mock.patch.object(cli, "MENUBAR_OFF", os.path.join(self.tmp.name, "menubar-off")), \
             mock.patch.object(cli.subprocess, "Popen") as popen:
            code, said = self.run_cli("menubar", "enable")
        self.assertEqual(0, code, said)
        self.assertTrue(popen.called)
        self.assertEqual(0o600, stat.S_IMODE(os.stat(menu).st_mode))

    def test_the_desktop_entry_says_the_same_quoted(self):
        with mock.patch.dict(os.environ, {"ZMKHUD_CONFIG": '/k/my "50%" board.yaml'}):
            argv = desktop_exec(cli.autostart_desktop())
        self.assertEqual(["env", "ZMKHUD_STATE=" + cli.STATE, 'ZMKHUD_CONFIG=/k/my "50%" board.yaml'] + cli.login_command(),
                         argv)

    def test_enable_and_disable_on_macos(self):
        code, said = self.run_cli("autostart", "enable")
        self.assertEqual(0, code, said)
        self.assertTrue(os.path.isfile(cli.LAUNCH_AGENT))
        self.assertTrue(os.path.isdir(cli.STATE))
        self.assertIn("launchctl bootstrap", said)
        self.assertIn("starts it hidden", self.run_cli("autostart")[1])
        os.makedirs(cli.LOGIN_APP)
        self.assertEqual(0, self.run_cli("autostart", "disable")[0])
        self.assertFalse(os.path.exists(cli.LAUNCH_AGENT))
        self.assertTrue(os.path.isdir(cli.LOGIN_APP))           # kept, and its grants with it
        self.assertIn("off", self.run_cli("autostart")[1])

    def test_enable_and_disable_on_linux(self):
        self.assertEqual(0, self.run_cli("autostart", "enable", system="Linux")[0])
        with open(cli.AUTOSTART_DESKTOP, encoding="utf-8") as f:
            self.assertEqual(cli.login_command(), desktop_exec(f.read())[-4:])
        self.assertEqual(0, self.run_cli("autostart", "disable", system="Linux")[0])
        self.assertFalse(os.path.exists(cli.AUTOSTART_DESKTOP))


def compiler():
    import shutil
    if not shutil.which("cc"):
        return False
    # On macOS /usr/bin/cc is there without the Command Line Tools too, and only offers them.
    return sys.platform != "darwin" or subprocess.call(["xcode-select", "-p"], stdout=subprocess.DEVNULL,
                                                       stderr=subprocess.DEVNULL) == 0


@unittest.skipUnless(compiler(), "no C compiler")
class Launcher(unittest.TestCase):
    """host/macos/launcher.c: the child is run, waited for, passed TERM, and its status is its own."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.exe = os.path.join(cls.tmp.name, "launcher")
        subprocess.check_call(["cc", "-O2", "-Wall", "-Werror", "-o", cls.exe,
                               os.path.join(ROOT, "host", "macos", "launcher.c")])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_childs_status_is_its_own(self):
        self.assertEqual(3, subprocess.call([self.exe, "/bin/sh", "-c", "exit 3"]))
        self.assertEqual(128 + 9, subprocess.call([self.exe, "/bin/sh", "-c", "kill -9 $$"]))
        self.assertEqual(64, subprocess.call([self.exe], stderr=subprocess.DEVNULL))

    def test_term_is_passed_to_the_child(self):
        proc = subprocess.Popen([self.exe, "/bin/sh", "-c", "trap 'exit 7' TERM; echo ready; while :; do sleep 0.05; done"],
                                stdout=subprocess.PIPE, text=True)
        self.addCleanup(proc.stdout.close)
        self.assertEqual("ready", proc.stdout.readline().strip())
        proc.terminate()
        self.assertEqual(7, proc.wait(timeout=10))


class Update(unittest.TestCase):
    """What `update` takes out of the tarball: files and directories under the archive's one top
    directory, and nothing that could land outside the staged tree."""

    def tarball(self, build, pax=None):
        import io
        import tarfile
        buf = io.BytesIO()
        # GitHub's archives are pax ones, the commit in their global header's comment.
        with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT, pax_headers=pax or {}) as tar:
            def add(name, data=b"", **kw):
                info = tarfile.TarInfo(name)
                for k, v in kw.items():
                    setattr(info, k, v)
                if info.type == tarfile.REGTYPE:
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
                else:
                    tar.addfile(info)
            build(add, tarfile)
        return buf.getvalue()

    def fetch(self, data):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        dest = os.path.join(tmp.name, "staging")
        os.makedirs(dest)

        def retrieve(url, path):
            with open(path, "wb") as f:
                f.write(data)
        with mock.patch("urllib.request.urlretrieve", retrieve), contextlib.redirect_stdout(io.StringIO()):
            self.commit = cli.fetch_tree("main", dest)
        return dest

    def test_the_tree_comes_out_without_its_top_directory(self):
        def build(add, tarfile):
            add("zmk-layer-hud-main/", type=tarfile.DIRTYPE, mode=0o755)
            add("zmk-layer-hud-main/bin/", type=tarfile.DIRTYPE, mode=0o755)
            add("zmk-layer-hud-main/bin/zmk-layer-hud", b"#!/bin/sh\n", mode=0o755)
            add("zmk-layer-hud-main/README.md", b"hi\n", mode=0o644)
            add("LICENSE", b"a file at the top, outside the one directory, is not part of the tree\n")
        dest = self.fetch(self.tarball(build))
        self.assertEqual({"bin", "README.md"}, set(os.listdir(dest)))
        with open(os.path.join(dest, "bin", "zmk-layer-hud")) as f:
            self.assertEqual("#!/bin/sh\n", f.read())

    def test_a_link_or_a_path_that_climbs_is_refused_whole(self):
        for what, build in {
            "a symlink": lambda add, tarfile: (add("t/README.md", b"x\n"),
                                               add("t/.venv", type=tarfile.SYMTYPE, linkname="/etc")),
            "a hardlink": lambda add, tarfile: add("t/x", type=tarfile.LNKTYPE, linkname="../../etc/passwd"),
            "a climb": lambda add, tarfile: add("t/../outside", b"x\n"),
            "an absolute path": lambda add, tarfile: add("t//etc/passwd", b"x\n"),
            "a device": lambda add, tarfile: add("t/dev", type=tarfile.CHRTYPE),
        }.items():
            with self.assertRaises(cli.Fail, msg=what) as e:
                self.fetch(self.tarball(build))
            self.assertIn("refusing", str(e.exception), what)

    def test_the_commit_the_archive_says_it_is_of_comes_back(self):
        sha = "06846f57b2f0df49a42d508a7324b83638b0bdf9"
        def build(add, tarfile):
            add("t/", type=tarfile.DIRTYPE, mode=0o755)
            add("t/README.md", b"hi\n")
        self.fetch(self.tarball(build, pax={"comment": sha}))
        self.assertEqual(sha, self.commit)
        self.fetch(self.tarball(build, pax={"comment": "not a commit"}))
        self.assertIsNone(self.commit)

    def test_a_tag_is_fetched_as_a_tag_and_a_branch_as_a_branch(self):
        self.assertTrue(cli.tarball_url("v1.0.0").endswith("/tar.gz/refs/tags/v1.0.0"))
        self.assertTrue(cli.tarball_url("main").endswith("/tar.gz/refs/heads/main"))
        for bad in ("../main", "main/../x", "main;rm", "-x", "a b", ""):
            with self.assertRaises(cli.Fail, msg=bad):
                cli.tarball_url(bad)

    def test_latest_is_the_newest_releases_tag(self):
        class Answer(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False
        with mock.patch("urllib.request.urlopen", lambda *a, **k: Answer(b'{"tag_name": "v1.2.0"}')):
            self.assertEqual("v1.2.0", cli.latest_release())
        with mock.patch("urllib.request.urlopen", lambda *a, **k: Answer(b'{"tag_name": "nightly"}')), \
                self.assertRaises(cli.Fail):
            cli.latest_release()

    def test_an_installed_tree_says_what_it_is_and_update_follows_it(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = os.path.join(tmp.name, "tree")
        os.makedirs(os.path.join(root, "bin"))
        sha = "06846f57b2f0df49a42d508a7324b83638b0bdf9"
        with mock.patch.object(cli, "ROOT", root), mock.patch.object(cli, "in_clone", lambda: False):
            self.assertEqual("(installed tree)", cli.describe_version())          # from before it said
            self.assertEqual("latest", cli.ref_to_follow(cli.installed_version()))
            cli.write_installed(root, "v1.0.0", sha)
            self.assertEqual("v1.0.0 (installed)", cli.describe_version())
            self.assertEqual("latest", cli.ref_to_follow(cli.installed_version()))  # a release: the newest
            cli.write_installed(root, "main", sha)
            self.assertEqual("main 06846f5 (installed)", cli.describe_version())
            self.assertEqual("main", cli.ref_to_follow(cli.installed_version()))    # a branch: that branch again

    def test_a_tree_with_no_record_says_the_version_release_please_keeps(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.object(cli, "ROOT", tmp.name), mock.patch.object(cli, "in_clone", lambda: False):
            with open(os.path.join(tmp.name, "version.txt"), "w") as f:
                f.write("1.2.0\n")
            self.assertEqual("v1.2.0 (installed tree)", cli.describe_version())
            with open(os.path.join(tmp.name, "version.txt"), "w") as f:
                f.write("not a version\n")
            self.assertEqual("(installed tree)", cli.describe_version())
        with open(os.path.join(ROOT, "version.txt")) as f:
            self.assertRegex(f.read().strip(), r"^\d+\.\d+\.\d+$")           # the tree's own, as release-please writes it

    def test_update_leaves_a_tree_already_at_the_commit_alone_and_swaps_one_that_is_not(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = os.path.join(tmp.name, "tree")
        os.makedirs(os.path.join(root, "bin"))
        with open(os.path.join(root, "bin", "zmk-layer-hud"), "w") as f:
            f.write("old\n")
        old, new = "1" * 40, "2" * 40
        cli.write_installed(root, "v1.0.0", old)
        answer, fetched = {"commit": old}, []

        def fetch(ref, dest):
            fetched.append(ref)
            os.makedirs(os.path.join(dest, "bin"))
            with open(os.path.join(dest, "bin", "zmk-layer-hud"), "w") as f:
                f.write("new\n")
            return answer["commit"]
        args = cli.build_parser().parse_args(["update"])
        with mock.patch.object(cli, "ROOT", root), mock.patch.object(cli, "in_clone", lambda: False), \
                mock.patch.object(cli, "fetch_tree", fetch), mock.patch.object(cli, "latest_release", lambda: "v1.1.0"), \
                mock.patch.object(cli, "processes", lambda pattern: {}), mock.patch.object(cli, "venv_ok", lambda: False), \
                mock.patch.object(cli, "plugin_dir", lambda: os.path.join(tmp.name, "no-plugin")), \
                mock.patch.object(cli, "NOCTALIA_PLUGINS", os.path.join(tmp.name, "no-noctalia")), \
                mock.patch.dict(os.environ, {"ZMKHUD_REF": ""}), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(0, cli.cmd_update(args))
            with open(os.path.join(root, "bin", "zmk-layer-hud")) as f:
                self.assertEqual("old\n", f.read())                           # the same commit: left alone
            answer["commit"] = new
            self.assertEqual(0, cli.cmd_update(args))
        with open(os.path.join(root, "bin", "zmk-layer-hud")) as f:
            self.assertEqual("new\n", f.read())
        self.assertEqual({"ref": "v1.1.0", "commit": new}, cli.installed_version(root))
        self.assertEqual(["v1.1.0", "v1.1.0"], fetched)                       # a release follows the newest
        self.assertEqual(["tree"], os.listdir(tmp.name))                       # no staging left behind
        self.assertIn("already up to date: v1.1.0", out.getvalue())
        self.assertIn("tree updated to v1.1.0", out.getvalue())


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

    def test_a_passthrough_names_itself_however_the_tests_were_started(self):
        # Under `python -m unittest`, some Python 3.14 releases default a parser's prog to `python -m
        # unittest` (argparse._prog_name reads __main__'s spec); poke's and the feed's parsers name
        # themselves from argv[0], as the CLI sets it, so the reference reads the same on every one.
        import clidocs
        for verb in cli.PASSTHROUGH:
            spec = mock.Mock(origin=f"zmk-layer-hud {verb}")
            spec.name = "unittest"
            with mock.patch.object(sys.modules["__main__"], "__spec__", spec, create=True):
                self.assertTrue(clidocs.passthrough_help(verb).startswith(f"usage: zmk-layer-hud {verb}"), verb)


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
            status = run("session").stdout
            self.assertIn("sym: 3 keys", status)
            self.assertIn("layers, of the keys:", status)
            self.assertIn("base  4 keys   57.1%", status)
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
            self.assertIn("layers: base 100%", out.stdout)
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
                self.assertIn("rect.key.hs6 { fill: #3730a3; }", f.read())

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
