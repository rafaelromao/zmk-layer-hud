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
        self.assertEqual("ws://127.0.0.1:8767/tok", ws)     # the token in the path: the page, and `poke --url`, carry it
        self.assertEqual("http://127.0.0.1:8765/index.html?ws=ws://127.0.0.1:8767/tok", page)

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
        for p in (mock.patch.object(cli, "OMARCHY_PLUGINS", self.plugins),
                  mock.patch.object(cli, "SHELL_JSON", self.shell),
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

    def tarball(self, build):
        import io
        import tarfile
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
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
            cli.fetch_tree("main", dest)
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
