"""Tests for the global shortcuts: what the config may say, how each system shows them, and the
Hyprland file and the line that sources it."""

import contextlib
import io
import json
import os
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


class HyprlandLua(unittest.TestCase):
    """A Hyprland configured in Lua, as Omarchy 4's is: the binds in a file of our own, read by a
    line at the end of hyprland.lua."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hypr = os.path.join(self.tmp.name, "hypr")
        self.state = os.path.join(self.tmp.name, "state")
        os.makedirs(self.hypr)
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
        self.assertTrue(shortcuts.remove(self.hypr))
        with open(self.main) as f:
            self.assertEqual(self.original, f.read())
        self.assertFalse(os.path.exists(os.path.join(self.hypr, shortcuts.HYPR_LUA)))
        self.assertFalse(shortcuts.remove(self.hypr))

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
                mock.patch.object(shortcuts, "read", lambda: shortcuts.settings({})), \
                mock.patch.object(shortcuts.subprocess, "call") as call, \
                mock.patch.dict(os.environ, {"ZMKHUD_STATE": self.state}), contextlib.redirect_stdout(io.StringIO()):
            shortcuts.main()
            shortcuts.main()                                  # nothing changed: Hyprland is left alone
        self.assertEqual([mock.call(["hyprctl", "reload"], stdout=shortcuts.subprocess.DEVNULL,
                                    stderr=shortcuts.subprocess.DEVNULL)], call.call_args_list)
        self.assertTrue(os.path.exists(os.path.join(self.hypr, shortcuts.HYPR_LUA)))
        self.assertFalse(os.path.exists(os.path.join(self.hypr, shortcuts.HYPR_FILE)))
        with open(conf) as f:
            self.assertNotIn(shortcuts.HYPR_SOURCE, f.read())


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
        import stat
        self.install()
        for path in (os.path.join(self.hypr, shortcuts.HYPR_FILE), os.path.join(self.state, shortcuts.JSON_FILE)):
            self.assertEqual(0o600, stat.S_IMODE(os.stat(path).st_mode), path)
        self.assertEqual({"hyprland.conf", "hyprland.conf.bak-zmk-layer-hud", shortcuts.HYPR_FILE},
                         set(os.listdir(self.hypr)))                               # no temporary file left


if __name__ == "__main__":
    unittest.main()
