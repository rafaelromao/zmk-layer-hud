"""Tests for the global shortcuts: what the config may say, how each system shows them, and the
files each compositor reads them from -- Hyprland's and the line that sources them, niri's KDL
and the include that reads it."""

import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import shortcuts  # noqa: E402


class Parse(unittest.TestCase):
    def test_modifiers_come_out_in_one_order_whatever_they_were_typed_in(self):
        self.assertEqual((("ctrl", "alt"), "l"), shortcuts.parse("Alt+Ctrl+L"))
        self.assertEqual((("ctrl", "alt", "gui"), "l"), shortcuts.parse("cmd + option + control + l"))
        self.assertEqual((("ctrl", "shift"), "7"), shortcuts.parse("ctrl+shift+7"))

    def test_what_cannot_be_bound_is_refused_with_a_reason(self):
        for bad, why in (("ctrl+alt", "letter or a digit"), ("hyper+l", "not a modifier"),
                         ("shift+l", "needs ctrl, alt or gui"), ("ctrl+alt+f1", "letter or a digit"),
                         ("", "ends in its key"), (5, "written like")):
            with self.assertRaises(shortcuts.ShortcutError) as e:
                shortcuts.parse(bad)
            self.assertIn(why, str(e.exception))


class Settings(unittest.TestCase):
    def test_the_defaults(self):
        self.assertEqual({"toggle": (("ctrl", "alt"), "l"), "power": (("ctrl", "alt", "gui"), "l")},
                         shortcuts.settings({}))

    def test_the_config_changes_one_and_null_turns_one_off(self):
        sets = shortcuts.settings({"shortcuts": {"toggle": "ctrl+alt+h", "power": None}})
        self.assertEqual({"toggle": (("ctrl", "alt"), "h"), "power": None}, sets)

    def test_unknown_names_and_twice_the_same_keys_are_refused(self):
        with self.assertRaises(shortcuts.ShortcutError):
            shortcuts.settings({"shortcuts": {"show": "ctrl+alt+s"}})
        with self.assertRaises(shortcuts.ShortcutError):
            shortcuts.settings({"shortcuts": {"toggle": "ctrl+alt+l", "power": "alt+ctrl+l"}})
        with self.assertRaises(shortcuts.ShortcutError):
            shortcuts.settings({"shortcuts": "ctrl+alt+l"})


class Label(unittest.TestCase):
    def test_each_system_its_own_way(self):
        sets = shortcuts.settings({})
        self.assertEqual("⌃⌥L", shortcuts.label(sets["toggle"], "Darwin"))
        self.assertEqual("⌃⌥⌘L", shortcuts.label(sets["power"], "Darwin"))
        self.assertEqual("Ctrl+Alt+Super+L", shortcuts.label(sets["power"], "Linux"))
        self.assertEqual("", shortcuts.label(None))


class Niri(unittest.TestCase):
    """niri's side: our KDL file, and the include once appended to config.kdl."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.niri = os.path.join(self.tmp.name, "niri")
        self.state = os.path.join(self.tmp.name, "state")
        os.makedirs(self.niri)
        self.main = os.path.join(self.niri, shortcuts.NIRI_MAIN)
        self.original = 'spawns-at-startup {}\n\ninclude "binds.kdl"\n'
        with open(self.main, "w") as f:
            f.write(self.original)

    def conf(self, sets=None, start_with=""):
        return shortcuts.niri_conf(sets or {"toggle": (("ctrl", "alt"), "l"),
                                            "power": (("ctrl", "alt", "gui"), "l"),
                                            "off": None},
                                   "/x/zmk-layer-hud", self.state, start_with)

    def test_each_key_is_a_spawn_of_the_command_in_one_binds_block(self):
        text = self.conf()
        self.assertIn("binds {", text)
        self.assertIn('Ctrl+Alt+L repeat=false { spawn "env" "ZMKHUD_STATE=' + self.state +
                      '" "/x/zmk-layer-hud" "toggle"; } // ZMK HUD show/hide', text)
        self.assertIn('Ctrl+Alt+Super+L repeat=false { spawn "env"', text)
        self.assertNotIn('"off"', text)                 # a null shortcut binds nothing

    def test_no_bind_repeats_while_its_key_is_held(self):
        # niri repeats a held bind unless told not to (Hyprland's `bind` does not): held, the toggle
        # would flicker and the power shortcut would start and stop the HUD over and over.
        binds = [line for line in self.conf().splitlines() if "spawn" in line]
        self.assertEqual(2, len(binds))
        self.assertTrue(all(" repeat=false { spawn " in line for line in binds), binds)

    def test_the_power_shortcut_takes_uwsm_app_and_the_toggle_does_not(self):
        text = self.conf(start_with="uwsm-app --")
        self.assertIn('Ctrl+Alt+Super+L repeat=false { spawn "uwsm-app" "--" "env"', text)
        self.assertIn('Ctrl+Alt+L repeat=false { spawn "env"', text)     # no uwsm prefix on the toggle

    def test_what_a_path_may_hold_is_escaped_in_the_kdl_strings(self):
        self.assertEqual(r'"a \"b\" \\ c\nd"', shortcuts.kdl_string('a "b" \\ c\nd'))
        text = shortcuts.niri_conf({"toggle": (("ctrl", "alt"), "l")}, '/home/u/my "dir"/zmk-layer-hud',
                                   "/s", "")
        self.assertIn(r'"/home/u/my \"dir\"/zmk-layer-hud" "toggle"', text)

    def test_the_file_is_written_and_the_include_appended_once(self):
        changed = shortcuts.install_niri({"toggle": (("ctrl", "alt"), "l")}, "/x", self.state,
                                         niri_dir=self.niri)
        self.assertTrue(changed)
        with open(os.path.join(self.niri, shortcuts.NIRI_FILE)) as f:
            self.assertIn("binds {", f.read())
        with open(self.main) as f:
            text = f.read()
        self.assertIn(shortcuts.NIRI_INCLUDE, text)
        self.assertTrue(text.startswith(self.original))      # what was there is still there
        again = shortcuts.install_niri({"toggle": (("ctrl", "alt"), "l")}, "/x", self.state,
                                       niri_dir=self.niri)
        self.assertFalse(again)                         # nothing changed the second time
        with open(self.main) as f:
            self.assertEqual(1, f.read().count(shortcuts.NIRI_INCLUDE))

    def test_no_config_kdl_is_nothing_at_all(self):
        os.remove(self.main)
        self.assertIsNone(shortcuts.install_niri({"toggle": (("ctrl", "alt"), "l")}, "/x",
                                                 self.state, niri_dir=self.niri))
        self.assertFalse(os.path.exists(os.path.join(self.niri, shortcuts.NIRI_FILE)))

    def test_remove_takes_the_include_and_the_file_out_and_the_rest_is_left(self):
        shortcuts.install_niri({"toggle": (("ctrl", "alt"), "l")}, "/x", self.state,
                               niri_dir=self.niri)
        self.assertTrue(shortcuts.remove_niri(self.niri))
        with open(self.main) as f:
            self.assertEqual(self.original, f.read())
        self.assertFalse(os.path.exists(os.path.join(self.niri, shortcuts.NIRI_FILE)))
        self.assertFalse(shortcuts.remove_niri(self.niri))

    def test_a_config_without_a_final_newline_or_with_nothing_in_it_round_trips(self):
        for original in ("input {}", ""):
            with open(self.main, "w") as f:
                f.write(original)
            shortcuts.install_niri({"toggle": (("ctrl", "alt"), "l")}, "/x", self.state, niri_dir=self.niri)
            with open(self.main) as f:
                text = f.read()
            self.assertEqual(1, text.splitlines().count(shortcuts.NIRI_INCLUDE), repr(text))
            self.assertTrue(shortcuts.remove_niri(self.niri))
            with open(self.main) as f:
                self.assertEqual(original.rstrip("\n") + ("\n" if original else ""), f.read())

    def installed(self):
        shortcuts.install_niri({"toggle": (("ctrl", "alt"), "l")}, "/x", self.state, niri_dir=self.niri)
        with open(self.main) as f:
            return f.read()

    def test_remove_puts_a_whole_file_in_config_kdls_place_and_leaves_no_temporary_one(self):
        self.installed()
        before = os.stat(self.main).st_ino
        self.assertTrue(shortcuts.remove_niri(self.niri))
        self.assertNotEqual(before, os.stat(self.main).st_ino)       # another file in its place, not this one written into
        self.assertEqual({shortcuts.NIRI_MAIN, shortcuts.NIRI_MAIN + ".bak-zmk-layer-hud"}, set(os.listdir(self.niri)))

    def test_a_remove_cut_short_leaves_config_kdl_as_it_was(self):
        # Wherever the rewrite stops -- the text half written, or written and not yet in place --
        # config.kdl is whole, as it was before: it is never the file being written.
        text = self.installed()
        for where, cut in (("write", mock.patch.object(shortcuts.os, "fsync", side_effect=OSError(28, "No space left"))),
                           ("replace", mock.patch.object(shortcuts.os, "replace", side_effect=OSError(5, "I/O error")))):
            with self.subTest(where), cut, self.assertRaises(OSError):
                shortcuts.remove_niri(self.niri)
            with open(self.main) as f:
                self.assertEqual(text, f.read(), where)
            self.assertEqual([], [n for n in os.listdir(self.niri) if n.endswith(".tmp")], where)
        self.assertTrue(shortcuts.remove_niri(self.niri))             # and the next one takes the include out
        with open(self.main) as f:
            self.assertEqual(self.original, f.read())

    def test_a_config_kdl_that_is_a_link_stays_one_and_the_file_it_leads_to_is_the_one_replaced(self):
        dotfiles = os.path.join(self.tmp.name, "dotfiles")
        os.makedirs(dotfiles)
        os.replace(self.main, os.path.join(dotfiles, "niri.kdl"))
        link = os.path.join("..", "dotfiles", "niri.kdl")              # relative, as a dotfiles manager writes it
        os.symlink(link, self.main)
        self.assertIn(shortcuts.NIRI_INCLUDE, self.installed())
        self.assertTrue(shortcuts.remove_niri(self.niri))
        self.assertTrue(os.path.islink(self.main))
        self.assertEqual(link, os.readlink(self.main))
        self.assertEqual(["niri.kdl"], os.listdir(dotfiles))           # replaced where it lives, nothing left beside it
        self.assertFalse(os.path.islink(os.path.join(dotfiles, "niri.kdl")))
        for path in (self.main, os.path.join(dotfiles, "niri.kdl")):
            with open(path) as f:
                self.assertEqual(self.original, f.read())

    def test_config_kdl_keeps_its_mode(self):
        for mode in (0o640, 0o600, 0o664):
            self.installed()
            os.chmod(self.main, mode)
            self.assertTrue(shortcuts.remove_niri(self.niri))
            self.assertEqual(mode, stat.S_IMODE(os.stat(self.main).st_mode), oct(mode))
            with open(self.main) as f:
                self.assertEqual(self.original, f.read())

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root may write a read-only file")
    def test_a_config_kdl_this_user_may_not_write_is_refused_and_left_as_it_is(self):
        # As when it was written in place. A rename asks only the directory, and would replace it.
        text = self.installed()
        os.chmod(self.main, 0o444)
        with self.assertRaises(PermissionError):
            shortcuts.remove_niri(self.niri)
        with open(self.main) as f:
            self.assertEqual(text, f.read())
        self.assertEqual(0o444, stat.S_IMODE(os.stat(self.main).st_mode))
        self.assertEqual([], [n for n in os.listdir(self.niri) if n.endswith(".tmp")])

    def run_main(self, version=(26, 4), call_effect=None, call_code=0):
        """shortcuts.main() as niri's session, with niri's version, the reload call and the
        output said; -> (the call mock, stdout, stderr)."""
        with mock.patch.object(shortcuts, "NIRI_DIR", self.niri), \
                mock.patch.object(shortcuts, "on_niri", lambda *a, **k: True), \
                mock.patch.object(shortcuts, "niri_version", lambda: version), \
                mock.patch.object(shortcuts, "read", lambda: shortcuts.settings({})), \
                mock.patch.object(shortcuts.subprocess, "call", return_value=call_code,
                                  side_effect=call_effect) as call, \
                mock.patch.dict(os.environ, {"ZMKHUD_STATE": self.state}), \
                contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            shortcuts.main()
        return call, out.getvalue(), err.getvalue()

    def test_main_writes_niris_files_and_asks_niri_to_read_them_again(self):
        call, out, err = self.run_main()
        self.assertTrue(os.path.exists(os.path.join(self.niri, shortcuts.NIRI_FILE)))
        with open(self.main) as f:
            self.assertIn(shortcuts.NIRI_INCLUDE, f.read())
        call.assert_called_once_with(["niri", "msg", "action", "load-config-file"],
                                     stdout=mock.ANY, stderr=mock.ANY)
        self.assertIn("shows or hides", out)
        self.assertIn("starts or stops", out)
        self.assertEqual("", err)

    def test_a_niri_older_than_the_include_needs_is_left_alone_and_it_is_said(self):
        for version in ((25, 11), (26, 3), (24, 12)):
            call, out, err = self.run_main(version=version)
            self.assertFalse(os.path.exists(os.path.join(self.niri, shortcuts.NIRI_FILE)), version)
            with open(self.main) as f:
                self.assertEqual(self.original, f.read())        # config.kdl as it was
            self.assertFalse(os.path.exists(self.main + ".bak-zmk-layer-hud"))
            call.assert_not_called()
            self.assertIn("is older than 26.04", err)
            self.assertIn("none are bound", err)
            self.assertEqual("", out)

    def test_the_first_niri_that_reads_the_include_and_later_ones_are_written(self):
        for version in ((26, 4), (26, 10), (27, 1)):
            call, out, err = self.run_main(version=version)
            self.assertTrue(os.path.exists(os.path.join(self.niri, shortcuts.NIRI_FILE)), version)
            self.assertEqual("", err)
            shortcuts.remove_niri(self.niri)

    def test_a_version_that_cannot_be_told_does_not_stop_the_binds(self):
        # No niri on PATH, or a build whose version string has no year.month: not a reason to refuse.
        call, out, err = self.run_main(version=None)
        self.assertTrue(os.path.exists(os.path.join(self.niri, shortcuts.NIRI_FILE)))
        self.assertEqual("", err)

    def test_a_niri_client_that_is_not_on_path_is_not_a_crash(self):
        # hud.sh runs this under `set -e`: a FileNotFoundError here would stop the HUD from starting.
        call, out, err = self.run_main(call_effect=FileNotFoundError(2, "No such file", "niri"))
        self.assertTrue(os.path.exists(os.path.join(self.niri, shortcuts.NIRI_FILE)))
        with open(self.main) as f:
            self.assertIn(shortcuts.NIRI_INCLUDE, f.read())
        self.assertIn("shows or hides", out)
        self.assertEqual("", err)                           # nothing was asked, so nothing failed

    def test_a_reload_niri_refuses_is_said_beside_what_was_written(self):
        call, out, err = self.run_main(call_code=1)
        self.assertIn("shows or hides", out)
        self.assertIn("`niri msg action load-config-file` exited 1", err)


class NiriVersion(unittest.TestCase):
    """`niri --version`: MAJOR.MINOR[.PATCH] (commit) in niri's source, the minor in two digits; what
    comes before it is not relied on."""

    def version(self, said):
        with mock.patch.object(shortcuts.subprocess, "check_output", return_value=said) as run:
            got = shortcuts.niri_version()
        self.assertEqual(["niri", "--version"], run.call_args[0][0])
        return got

    def test_the_year_and_month_are_read_out_of_the_answer(self):
        self.assertEqual((26, 4), self.version("niri 26.04 (abc1234)\n"))
        self.assertEqual((25, 11), self.version("25.11 (unknown commit)\n"))
        self.assertEqual((26, 4), self.version("niri 26.04.1 (a1b2c3d)\n"))        # a patch release
        self.assertEqual((25, 8), self.version("niri 25.08 (v25.08)\n"))

    def test_an_answer_with_no_version_in_it_is_none(self):
        self.assertIsNone(self.version("niri (a distribution's build)\n"))
        self.assertIsNone(self.version(""))

    def test_no_niri_on_path_or_one_that_hangs_or_fails_is_none(self):
        for effect in (FileNotFoundError(2, "No such file", "niri"),
                       shortcuts.subprocess.TimeoutExpired("niri", 5),
                       shortcuts.subprocess.CalledProcessError(1, "niri")):
            with mock.patch.object(shortcuts.subprocess, "check_output", side_effect=effect):
                self.assertIsNone(shortcuts.niri_version())


class OnNiri(unittest.TestCase):
    """Which compositor this session is: niri's socket says so, a Hyprland one says not, and
    without either a niri config is what is left to go on."""

    def test_niris_socket_in_the_environment_is_niri(self):
        self.assertTrue(shortcuts.on_niri({"NIRI_SOCKET": "/run/user/1000/niri.sock"}))

    def test_hyprlands_signature_says_not_niri_however_the_configs_look(self):
        self.assertFalse(shortcuts.on_niri({"HYPRLAND_INSTANCE_SIGNATURE": "abc123"}))

    def test_without_either_a_niri_config_is_the_sign(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        niri = os.path.join(tmp.name, "niri")
        os.makedirs(niri)
        with mock.patch.object(shortcuts, "NIRI_DIR", niri), \
                mock.patch.object(shortcuts.shutil, "which", return_value="/usr/bin/niri"):
            self.assertFalse(shortcuts.on_niri({}))
            with open(os.path.join(niri, shortcuts.NIRI_MAIN), "w") as f:
                f.write("")
            self.assertTrue(shortcuts.on_niri({}))

    def test_a_niri_config_left_where_niri_is_not_installed_is_not_the_sign(self):
        # A Hyprland machine that once had niri, run where neither compositor's variable is set
        # (ssh, a service): its binds are Hyprland's to take, and `niri` is not there to ask.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        niri = os.path.join(tmp.name, "niri")
        os.makedirs(niri)
        with open(os.path.join(niri, shortcuts.NIRI_MAIN), "w") as f:
            f.write("")
        with mock.patch.object(shortcuts, "NIRI_DIR", niri), \
                mock.patch.object(shortcuts.shutil, "which", return_value=None):
            self.assertFalse(shortcuts.on_niri({}))
            self.assertTrue(shortcuts.on_niri({"NIRI_SOCKET": "/run/user/1000/niri.sock"}))   # the socket still says it


class HyprlandLua(unittest.TestCase):
    """A Hyprland configured in Lua, as Omarchy 4's is: the binds in a file of our own, read by a
    line at the end of hyprland.lua."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hypr = os.path.join(self.tmp.name, "hypr")
        self.niri = os.path.join(self.tmp.name, "niri")     # remove() undoes niri's files too
        self.state = os.path.join(self.tmp.name, "state")
        os.makedirs(self.hypr)
        os.makedirs(self.niri)
        self.main = os.path.join(self.hypr, "hyprland.lua")
        self.original = 'require("default.hypr.omarchy")\nrequire("hypr.bindings")\n'
        with open(self.main, "w") as f:
            f.write(self.original)

    def install(self, sets=None):
        return shortcuts.install_lua(sets or shortcuts.settings({}), '/opt/zmk "hud"/bin/zmk-layer-hud', self.state,
                                     hypr_dir=self.hypr, start_with="uwsm-app --")

    def test_each_key_is_taken_from_whatever_had_it_then_bound_in_hyprlands_own_calls(self):
        text = shortcuts.lua_conf(shortcuts.settings({}), "/opt/zmk/bin/zmk-layer-hud", "/s", start_with="uwsm-app --")
        self.assertEqual([
            'pcall(hl.unbind, "CTRL + ALT + L")',
            'hl.bind("CTRL + ALT + L", hl.dsp.exec_cmd("env ZMKHUD_STATE=/s /opt/zmk/bin/zmk-layer-hud toggle"), '
            '{ description = "ZMK HUD show/hide" })',
            'pcall(hl.unbind, "CTRL + ALT + SUPER + L")',
            'hl.bind("CTRL + ALT + SUPER + L", hl.dsp.exec_cmd("uwsm-app -- env ZMKHUD_STATE=/s /opt/zmk/bin/zmk-layer-hud power"), '
            '{ description = "ZMK HUD start/stop" })',
        ], [line for line in text.splitlines() if line and not line.startswith("--")])
        self.assertEqual(r'"a \"b\" \\c\n"', shortcuts.lua_string('a "b" \\c\n'))

    def test_read_once_from_the_end_of_hyprland_lua_and_taken_out_again(self):
        self.assertTrue(self.install())
        self.assertFalse(self.install())
        with open(self.main) as f:
            text = f.read()
        self.assertEqual(1, text.splitlines().count(shortcuts.LUA_LOAD))
        self.assertTrue(text.startswith(self.original) and text.rstrip().endswith(shortcuts.LUA_LOAD))
        with open(os.path.join(self.hypr, shortcuts.HYPR_LUA)) as f:
            self.assertIn(r'\"hud\"', f.read())              # the command's own quotes, a Lua string's
        with open(os.path.join(self.state, shortcuts.JSON_FILE)) as f:
            self.assertEqual("Ctrl+Alt+L", json.load(f)["toggle"])
        self.assertTrue(shortcuts.remove(self.hypr, self.niri))
        with open(self.main) as f:
            self.assertEqual(self.original, f.read())
        self.assertFalse(os.path.exists(os.path.join(self.hypr, shortcuts.HYPR_LUA)))
        self.assertFalse(shortcuts.remove(self.hypr, self.niri))

    def test_no_hyprland_lua_binds_nothing(self):
        os.remove(self.main)
        self.assertIsNone(self.install())
        self.assertEqual([], os.listdir(self.hypr))

    def test_a_lua_config_wins_and_what_an_earlier_version_left_in_hyprland_conf_goes(self):
        conf = os.path.join(self.hypr, "hyprland.conf")
        with open(conf, "w") as f:
            f.write("monitor = , preferred, auto, 1\n")
        shortcuts.install_hyprland(shortcuts.settings({}), "/x", self.state, hypr_dir=self.hypr)
        with mock.patch.object(shortcuts, "HYPR_DIR", self.hypr), \
                mock.patch.object(shortcuts, "on_niri", lambda *a, **k: False), \
                mock.patch.object(shortcuts, "read", lambda: shortcuts.settings({})), \
                mock.patch.object(shortcuts.subprocess, "call") as call, \
                mock.patch.dict(os.environ, {"ZMKHUD_STATE": self.state}), contextlib.redirect_stdout(io.StringIO()):
            call.return_value = 0
            shortcuts.main()
            shortcuts.main()                                  # nothing changed: Hyprland is left alone
        self.assertEqual([mock.call(["hyprctl", "reload"], stdout=shortcuts.subprocess.DEVNULL,
                                    stderr=shortcuts.subprocess.DEVNULL)], call.call_args_list)
        self.assertTrue(os.path.exists(os.path.join(self.hypr, shortcuts.HYPR_LUA)))
        self.assertFalse(os.path.exists(os.path.join(self.hypr, shortcuts.HYPR_FILE)))
        with open(conf) as f:
            self.assertNotIn(shortcuts.HYPR_SOURCE, f.read())

    def test_a_hyprctl_that_is_not_on_path_is_not_a_crash(self):
        # hud.sh runs this under `set -e`: a FileNotFoundError here would stop the HUD from starting.
        with mock.patch.object(shortcuts, "HYPR_DIR", self.hypr), \
                mock.patch.object(shortcuts, "on_niri", lambda *a, **k: False), \
                mock.patch.object(shortcuts, "read", lambda: shortcuts.settings({})), \
                mock.patch.object(shortcuts.subprocess, "call",
                                  side_effect=FileNotFoundError(2, "No such file", "hyprctl")), \
                mock.patch.dict(os.environ, {"ZMKHUD_STATE": self.state}), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            shortcuts.main()
        self.assertTrue(os.path.exists(os.path.join(self.hypr, shortcuts.HYPR_LUA)))
        self.assertIn("shows or hides", out.getvalue())


class Hyprland(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hypr = os.path.join(self.tmp.name, "hypr")
        self.state = os.path.join(self.tmp.name, "state dir")
        os.makedirs(self.hypr)
        self.main = os.path.join(self.hypr, "hyprland.conf")
        with open(self.main, "w") as f:
            f.write("source = ~/.config/hypr/bindings.conf")       # no newline at the end

    def install(self, sets=None):
        return shortcuts.install_hyprland(sets or shortcuts.settings({}), "/opt/zmk hud/bin/zmk-layer-hud",
                                          self.state, hypr_dir=self.hypr, start_with="uwsm-app --")

    def test_each_key_is_taken_from_whatever_had_it_then_bound(self):
        text = shortcuts.hyprland_conf(shortcuts.settings({}), "/opt/zmk hud/bin/zmk-layer-hud", self.state,
                                       start_with="uwsm-app --")
        lines = [line for line in text.splitlines() if line and not line.startswith("#")]
        self.assertEqual([
            "unbind = CTRL ALT, L",
            "bindd = CTRL ALT, L, ZMK HUD show/hide, exec, env ZMKHUD_STATE='%s' '/opt/zmk hud/bin/zmk-layer-hud' toggle"
            % self.state,
            "unbind = CTRL ALT SUPER, L",
            "bindd = CTRL ALT SUPER, L, ZMK HUD start/stop, exec, uwsm-app -- env ZMKHUD_STATE='%s' "
            "'/opt/zmk hud/bin/zmk-layer-hud' power" % self.state,
        ], lines)

    def test_sourced_once_at_the_end_and_written_only_when_it_changes(self):
        self.assertTrue(self.install())
        self.assertFalse(self.install())
        with open(self.main) as f:
            text = f.read()
        self.assertEqual(1, text.splitlines().count(shortcuts.HYPR_SOURCE))
        self.assertTrue(text.startswith("source = ~/.config/hypr/bindings.conf\n"))
        self.assertTrue(text.rstrip().endswith(shortcuts.HYPR_SOURCE))
        with open(os.path.join(self.state, shortcuts.JSON_FILE)) as f:
            self.assertEqual({"toggle": "Ctrl+Alt+L", "power": "Ctrl+Alt+Super+L"}, json.load(f))
        self.assertTrue(self.install(shortcuts.settings({"shortcuts": {"power": None}})))
        with open(os.path.join(self.hypr, shortcuts.HYPR_FILE)) as f:
            self.assertNotIn("power", f.read())

    def test_removed_again_leaves_the_rest_as_it_was(self):
        self.install()
        self.assertTrue(shortcuts.remove_hyprland(self.hypr))
        with open(self.main) as f:
            self.assertEqual("source = ~/.config/hypr/bindings.conf\n", f.read())
        self.assertFalse(os.path.exists(os.path.join(self.hypr, shortcuts.HYPR_FILE)))
        self.assertFalse(shortcuts.remove_hyprland(self.hypr))

    def test_no_hyprland_conf_binds_nothing(self):
        os.remove(self.main)
        self.assertIsNone(self.install())
        self.assertEqual([], os.listdir(self.hypr))

    def test_the_users_file_is_kept_as_it_was_before_the_first_edit_and_that_copy_is_never_written_over(self):
        bak = self.main + ".bak-zmk-layer-hud"
        self.install()
        with open(bak) as f:
            self.assertEqual("source = ~/.config/hypr/bindings.conf", f.read())    # as it was, newline and all
        with open(self.main, "a") as f:
            f.write("bind = SUPER, Q, killactive\n")                               # the user's later edit
        self.install(shortcuts.settings({"shortcuts": {"power": None}}))
        shortcuts.remove_hyprland(self.hypr)
        with open(bak) as f:
            self.assertEqual("source = ~/.config/hypr/bindings.conf", f.read())    # still the first copy
        with open(self.main) as f:
            self.assertIn("killactive", f.read())                                   # and the edit survived the removal

    def test_what_is_written_is_this_users_alone_and_whole(self):
        self.install()
        for path in (os.path.join(self.hypr, shortcuts.HYPR_FILE), os.path.join(self.state, shortcuts.JSON_FILE)):
            self.assertEqual(0o600, stat.S_IMODE(os.stat(path).st_mode), path)
        self.assertEqual({"hyprland.conf", "hyprland.conf.bak-zmk-layer-hud", shortcuts.HYPR_FILE},
                         set(os.listdir(self.hypr)))                               # no temporary file left


if __name__ == "__main__":
    unittest.main()
