"""Tests for the global shortcuts: what the config may say, how each system shows them, and the
Hyprland file and the line that sources it."""

import json
import os
import sys
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
