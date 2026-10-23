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
        self.assertEqual(40, dock.height(40, 1080, 40))             # minimized: a bar, the windows take the rest
        self.assertEqual(dock.BAR_HEIGHT, dock.height(10, 1080, 40))
        self.assertEqual(dock.MIN_HEIGHT, dock.max_height(200, 40))
        self.assertEqual(648, dock.height(0, 1080))                 # unless said, 60%: a keyboard at its real size


class Measure(unittest.TestCase):
    """How many of the page's pixels are a millimetre on the dock's screen: what keys of a real size
    are drawn with."""

    def test_a_screen_that_says_its_size(self):
        # A 13.6" laptop at its default scaling: 1470 points across 303 mm.
        self.assertEqual((round(1470 / 303, 4), True), dock.px_per_mm(1470, 956, 303, 197))
        # Turned upright, its millimetres still given as when it was not.
        self.assertEqual((round(956 / 197, 4), True), dock.px_per_mm(956, 1470, 303, 197))

    def test_one_that_does_not_gets_css_measure(self):
        nominal = (dock.NOMINAL_PX_PER_MM, False)
        for mm in ((0, 0), (None, None), (160, 90), (16, 10), (1, 1), (5000, 3000)):
            self.assertEqual(nominal, dock.px_per_mm(1920, 1080, *mm), msg=repr(mm))
        self.assertEqual(nominal, dock.px_per_mm(0, 0, 300, 200))


class PagesWord(unittest.TestCase):
    """What the page says of its board docked (hud.js dockFit, sayFit), as the panels say it."""

    def test_a_board_too_big_at_its_real_size(self):
        msg = {"kind": "dock", "fits": False, "key_mm": 18, "need_mm": [191, 115], "room_mm": [286, 103]}
        self.assertEqual("at 18 mm a key (touch.key_mm) the board is 191 x 115 mm, and the dock may be 103 mm tall "
                         "(dock.height_pct, 60% of the screen)", dock.refusal(msg))

    def test_a_board_too_big_even_with_smaller_keys(self):
        msg = {"kind": "dock", "fits": False, "key_mm": 16, "want_mm": 18, "need_mm": [400, 102], "room_mm": [286, 103]}
        self.assertEqual("even at 16 mm a key, the smallest touch.min_key_mm lets them be (touch.key_mm is 18), the "
                         "board is 400 x 102 mm, and the screen is 286 mm across", dock.refusal(msg))

    def test_a_board_docked_with_smaller_keys_is_said_and_not_refused(self):
        msg = {"kind": "dock", "fits": True, "key_mm": 16.9, "want_mm": 18}
        self.assertIsNone(dock.refusal(msg))
        self.assertEqual("at 16.9 mm a key, smaller than touch.key_mm's 18 to fit", dock.smaller(msg))
        self.assertEqual("", dock.smaller({"kind": "dock", "fits": True, "key_mm": 18}))       # its own size again
        for other in ({"kind": "dock", "fits": False, "key_mm": 16}, {"kind": "dock", "on": True}, "fits",
                      {"kind": "dock", "fits": True, "key_mm": "16"}, {"kind": "dock", "fits": True, "key_mm": True}):
            self.assertIsNone(dock.smaller(other), msg=repr(other))


if __name__ == "__main__":
    unittest.main()
