"""host/dock.py: the screen the HUD docks on, and how tall the strip is."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dock  # noqa: E402

EDP = {"name": "eDP-1", "description": "BOE 0x0BCA", "focused": False}
DP = {"name": "DP-2", "description": "Dell Inc. DELL U2723QE", "focused": True}
OFF = {"name": "HDMI-A-1", "description": "LG", "disabled": True}


class Monitor(unittest.TestCase):
    def test_the_one_the_config_names(self):
        self.assertIs(EDP, dock.choose_monitor([DP, EDP], wanted="edp-1"))
        self.assertIs(DP, dock.choose_monitor([EDP, DP], wanted="dell"))

    def test_the_one_hyprland_maps_touches_to(self):
        self.assertIs(DP, dock.choose_monitor([EDP, DP], touch_output="DP-2"))
        self.assertIs(DP, dock.choose_monitor([EDP, DP], touch_output="[[Auto]]"))    # unset: the focused one

    def test_with_a_touch_screen_the_built_in_panel(self):
        self.assertIs(EDP, dock.choose_monitor([DP, EDP], devices={"touch": [{"name": "elan9008"}]}))
        self.assertIs(DP, dock.choose_monitor([DP, EDP], devices={"touch": []}))

    def test_else_the_focused_one_and_never_a_disabled_one(self):
        self.assertIs(DP, dock.choose_monitor([EDP, DP]))
        self.assertIs(DP, dock.choose_monitor([OFF, DP], wanted="HDMI-A-1"))
        self.assertIsNone(dock.choose_monitor([OFF]))
        self.assertIsNone(dock.choose_monitor([]))


class Height(unittest.TestCase):
    def test_what_the_page_needs_within_the_screens_share(self):
        self.assertEqual(300, dock.height(300, 1080, 40))
        self.assertEqual(432, dock.height(900, 1080, 40))
        self.assertEqual(432, dock.height(0, 1080, 40))           # before the page says: the share
        self.assertEqual(dock.MIN_HEIGHT, dock.height(40, 1080, 40))
        self.assertEqual(dock.MIN_HEIGHT, dock.max_height(200, 40))


if __name__ == "__main__":
    unittest.main()
