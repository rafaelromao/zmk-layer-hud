"""Tests for host/hudfeed.py: the readers, the stream, the socket and what watches for secrets.

The wire format itself is host/signal_frame.py's business and is tested in signal_frame_test.py;
what a report means is host/hiddecode.py's, tested in hiddecode_test.py. Stream ties the two together.
"""

import asyncio
import functools
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import termios
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import session  # noqa: E402
import signal_frame  # noqa: E402
from hiddecode import SignalDecoder  # noqa: E402
from hudfeed import (COMBOS_DEFAULT, INJECTABLE, Feed, Hub, HubThread, SecureInput, SerialReader, any_of, probe_report,  # noqa: E402
                     Stream, asks_secret, hid_scan_note, hidraw_match, sent_in, serve_hub,
                     split_report, terminal_probe)

try:
    import yaml  # noqa: F401
    HAVE_YAML = True
except ImportError:
    HAVE_YAML = bool(shutil.which("yq"))   # keymap.load_yaml's other way to read a config


class StreamTest(unittest.TestCase):
    """Bytes off a carrier, through the frame decoder, to the pages' messages."""

    def setUp(self):
        self.out = []
        self.stream = Stream(self.out.append, "Diamond")

    def frame(self, kind, payload):
        body = bytes([1, kind, len(payload)]) + bytes(payload)
        return b"\xa5\x5a" + body + bytes([signal_frame.crc8(body)])

    def test_a_frame_becomes_a_message_tagged_with_its_keyboard(self):
        self.stream.feed(self.frame(signal_frame.KIND_LAYERS, [0x00, 0x00, 0x40, 0x00]), 0)
        self.assertEqual(self.out, [{"kind": "layers", "ids": [22], "device": "Diamond"}])
        self.assertEqual(self.stream.frames_seen, 1)

    def test_a_frame_split_across_two_reads(self):
        data = self.frame(signal_frame.KIND_LAYERS, [0x04, 0x00, 0x00, 0x00])
        self.stream.feed(data[:3], 0)
        self.assertEqual(self.out, [])
        self.stream.feed(data[3:], 1)
        self.assertEqual([m["ids"] for m in self.out], [[2]])

    def test_a_keys_message_from_the_hid_reader_takes_the_same_path(self):
        # The carrier does not carry these -- HidKeysReader reads them off the keyboard's HID
        # reports and hands them straight to message(), which is why one decoder serves both.
        self.stream.message({"kind": "keys", "mods": 0, "keys": [0x04]}, 0)
        self.assertEqual([(m["type"], m["chars"], m["device"]) for m in self.out],
                         [("keyDown", "a", "Diamond")])

    def test_noise_produces_nothing_and_counts_nothing(self):
        # What a port that is not ours looks like; the reader gives up on it after probe_s.
        self.stream.feed(b"*** Booting Zephyr OS ***\r\n", 0)
        self.assertEqual((self.out, self.stream.frames_seen), ([], 0))

    def test_resync_makes_the_next_heartbeat_speak_again(self):
        # Something else drew on the pages, so the keyboard agreeing with itself is no longer
        # silence worth keeping: the next heartbeat has to restate what is really held.
        layers = self.frame(signal_frame.KIND_LAYERS, [0x02, 0x00, 0x00, 0x00])
        self.stream.feed(layers, 0)
        self.stream.feed(layers, 1)                      # heartbeat, unchanged
        self.assertEqual(len(self.out), 1)
        self.stream.resync()
        self.stream.feed(layers, 2)                      # same heartbeat, now a change again
        self.assertEqual([m["ids"] for m in self.out], [[1], [1]])

    def test_raw_logging_asks_before_it_logs(self):
        # --raw is the typing in the log; a callable in place of True is asked at every message,
        # which is how the feed keeps it quiet while a secret is being typed.
        allowed, said = [False], []
        stream = Stream(self.out.append, "Diamond", raw=lambda: allowed[0], log=said.append)
        stream.message({"kind": "keys", "mods": 0, "keys": [0x04]}, 0)
        self.assertEqual([], said)
        self.assertEqual(1, len(self.out))                       # the message itself still goes on: the gate is the feed's
        allowed[0] = True
        stream.message({"kind": "keys", "mods": 0, "keys": []}, 1)
        self.assertEqual(1, len(said))
        self.assertTrue(said[0].startswith("hudfeed: Diamond {"), said)
        plain = Stream(self.out.append, "Diamond", raw=True, log=said.append)
        plain.message({"kind": "layers", "ids": [1]}, 2)
        self.assertEqual(2, len(said))

    def test_closing_lets_a_held_modifier_go(self):
        # Shift held as the keyboard was unplugged: the page draws it held until it is told otherwise.
        self.stream.message({"kind": "keys", "mods": 0x02, "keys": []}, 0)
        self.out.clear()
        self.stream.close()
        self.assertEqual([("flagsChanged", False)], [(m["type"], m["flags"]["shift"]) for m in self.out])

    def test_closing_leaves_held_a_modifier_another_keyboard_holds(self):
        # Shift held on both keyboards, one unplugged: the other still holds it, and will not say
        # so again until it lets it go -- so the pages are not told it came up.
        self.stream.message({"kind": "keys", "mods": 0x02, "keys": [0x04]}, 0)
        self.out.clear()
        self.stream.close(held_elsewhere=0x02)
        self.assertEqual([("keyUp", "A")], [(m["type"], m["chars"]) for m in self.out])
        # Ctrl and Shift here, Shift there: Ctrl comes up, Shift stays.
        stream = Stream(self.out.append, "Diamond")
        stream.message({"kind": "keys", "mods": 0x03, "keys": []}, 0)
        self.out.clear()
        stream.close(held_elsewhere=0x02)
        self.assertEqual([("flagsChanged", False, True)],
                         [(m["type"], m["flags"]["ctrl"], m["flags"]["shift"]) for m in self.out])

    def test_closing_types_a_dead_key_that_was_waiting_for_its_letter(self):
        # ' held back for the letter that would make it an accent: the keyboard went away instead,
        # so it is the plain key it was -- down, then up -- never an up with no down.
        self.stream.message({"kind": "keys", "mods": 0, "keys": [0x34]}, 0)
        self.assertEqual([], self.out)
        self.stream.close()
        self.assertEqual([("keyDown", "'"), ("keyUp", "'")], [(m["type"], m["chars"]) for m in self.out])

    def test_closing_releases_what_was_held(self):
        self.stream.message({"kind": "keys", "mods": 0, "keys": [0x04]}, 0)
        self.out.clear()
        self.stream.close()
        self.assertEqual([(m["type"], m["chars"], m["device"]) for m in self.out],
                         [("keyUp", "a", "Diamond")])


class Reports(unittest.TestCase):
    """The HID reports the host reads for the strip, split into what the decoder wants."""

    def test_zmk_keyboard_report(self):
        # [report id 1, modifiers, reserved, keys...] as hidapi returns it
        self.assertEqual(split_report([1, 0x02, 0, 0x04, 0x05, 0, 0, 0, 0]), (0x02, b"\x04\x05\x00\x00\x00\x00"))

    def test_other_report_ids_are_not_ours(self):
        self.assertIsNone(split_report([2, 0xCD, 0, 0, 0, 0]))  # consumer
        self.assertIsNone(split_report([3, 0, 0, 0x04]))        # mouse

    def test_short_or_empty(self):
        self.assertIsNone(split_report([]))
        self.assertIsNone(split_report([1, 0]))

    def test_without_report_id(self):
        self.assertEqual(split_report([0x02, 0, 0x04], report_id=None), (0x02, b"\x04"))
        self.assertIsNone(split_report([0], report_id=None))

    def test_a_report_becomes_the_same_message_the_firmware_would_send(self):
        # The point of the split: both sources hand SignalDecoder the identical shape, so the
        # layout tables and dead-key composition below cannot tell them apart.
        mods, keys = split_report([1, 0x02, 0, 0x04, 0, 0, 0, 0, 0])
        d = SignalDecoder()
        msgs = d.feed({"kind": "keys", "mods": mods, "keys": list(keys)})
        self.assertEqual([m["chars"] for m in msgs if m.get("type") == "keyDown"], ["A"])


class ScanNote(unittest.TestCase):
    """A HID scan that finds no keyboard used to say nothing at all, which is how one missing
    permission came to look like two unrelated features being broken."""

    def test_a_keyboard_was_found(self):
        self.assertIsNone(hid_scan_note(1, 0x1D50, 0x615E))

    def test_none_found_names_what_it_looked_for(self):
        note = hid_scan_note(0, 0x1D50, 0x615E, platform="darwin")
        self.assertIn("1d50:615e", note)
        # Both features the reports feed are named, because neither of them is obviously HID.
        self.assertIn("strip", note)
        self.assertIn("capitalisation", note)

    def test_the_name_filter_is_named_too(self):
        # A filter that matches nothing looks exactly like a keyboard that is not plugged in.
        self.assertIn("'Diamond'", hid_scan_note(0, 0x1D50, 0x615E, name="Diamond", platform="linux"))

    def test_only_linux_is_told_about_the_udev_rule(self):
        self.assertIn("udev", hid_scan_note(0, 0x1D50, 0x615E, platform="linux"))
        self.assertNotIn("udev", hid_scan_note(0, 0x1D50, 0x615E, platform="darwin"))


class HidrawNodes(unittest.TestCase):
    """Which /dev/hidrawN is the keyboard, answered from sysfs without opening anything —
    the Linux path, because hidapi's wheel there talks libusb and cannot open it at all."""

    USB = ("DRIVER=hid-generic\n"
           "HID_ID=0003:00001D50:0000615E\n"
           "HID_NAME=ZMK Project Diamond\n"
           "HID_PHYS=usb-0000:00:14.0-3/input0\n")

    def test_the_keyboard_is_recognised_and_named(self):
        self.assertEqual(hidraw_match(self.USB, 0x1D50, 0x615E), "ZMK Project Diamond")

    def test_bluetooth_is_the_same_keyboard(self):
        # bus 0005 rather than 0003; only the vendor and product decide.
        ble = self.USB.replace("0003:", "0005:")
        self.assertEqual(hidraw_match(ble, 0x1D50, 0x615E), "ZMK Project Diamond")

    def test_another_vendors_node_is_not_ours(self):
        self.assertIsNone(hidraw_match(self.USB.replace("00001D50", "0000046D"), 0x1D50, 0x615E))

    def test_the_name_filter_picks_one_of_several_zmk_boards(self):
        self.assertEqual(hidraw_match(self.USB, 0x1D50, 0x615E, name="diamond"), "ZMK Project Diamond")
        self.assertIsNone(hidraw_match(self.USB, 0x1D50, 0x615E, name="Rommana"))

    def test_a_node_with_no_hid_parent_is_skipped(self):
        # Not every uevent has HID_ID, and a malformed one must not take the scan down.
        self.assertIsNone(hidraw_match("DEVNAME=hidraw0\n", 0x1D50, 0x615E))
        self.assertIsNone(hidraw_match("HID_ID=garbage\n", 0x1D50, 0x615E))
        self.assertIsNone(hidraw_match("HID_ID=0003:zz:yy\n", 0x1D50, 0x615E))

    def test_a_nameless_node_still_counts(self):
        self.assertEqual(hidraw_match("HID_ID=0003:00001D50:0000615E\n", 0x1D50, 0x615E), "?")


class Injectable(unittest.TestCase):
    """What a WebSocket client is allowed to put on the pages (host/hudpoke.py)."""

    def test_the_cosmetic_kinds_are_accepted(self):
        for kind in ("layers", "press", "release", "key", "device"):
            self.assertIn(kind, INJECTABLE)

    def test_the_keymap_is_not(self):
        # It is large, it is built from files the feed already watches, and a page given a wrong
        # one has no way back to the right one.
        self.assertNotIn("keymap", INJECTABLE)

    def test_close_is_not_injectable_either(self):
        # It is handled before this list and exits the process; it must not be broadcast.
        self.assertNotIn("close", INJECTABLE)


class SentIn(unittest.TestCase):
    """Typing sent in says whether combos are how it is typed, and the pages rely on the field
    being there: it is also what tells typing sent in from the keyboard's own reports."""

    KEY = {"kind": "key", "type": "keyDown", "name": "z", "chars": "z"}

    def test_the_default_is_no_combos(self):
        self.assertIs(COMBOS_DEFAULT, False)
        self.assertIs(sent_in(dict(self.KEY))["combos"], False)

    def test_a_sender_that_says_keeps_its_word(self):
        for said in (True, False):
            self.assertIs(sent_in({**self.KEY, "combos": said})["combos"], said)

    def test_a_value_that_is_not_a_yes_or_no_is_not_one(self):
        # "true" from a hand-typed client is not True; the default stands rather than a guess.
        self.assertIs(sent_in({**self.KEY, "combos": "true"})["combos"], COMBOS_DEFAULT)

    def test_only_keys_carry_it(self):
        for msg in ({"kind": "layers", "ids": [13]}, {"kind": "press", "pos": 16}, {"kind": "device", "name": "x"}):
            self.assertNotIn("combos", sent_in(dict(msg)))

    def test_the_sender_s_message_is_not_changed_under_it(self):
        msg = dict(self.KEY)
        sent_in(msg)
        self.assertNotIn("combos", msg)
        self.assertNotIn("sent", msg)

    def test_every_kind_says_it_was_sent_in(self):
        # A press sent in lights its key like the keyboard's own; this is the one field that
        # keeps it out of a session.
        for msg in (self.KEY, {"kind": "layers", "ids": [13]}, {"kind": "press", "pos": 16},
                    {"kind": "release", "pos": 16}, {"kind": "device", "name": "x"}):
            self.assertIs(sent_in(dict(msg))["sent"], True, msg["kind"])

    def test_a_client_cannot_say_it_is_the_keyboard(self):
        for said in (False, None, "no"):
            self.assertIs(sent_in({**self.KEY, "sent": said})["sent"], True)


class Secure(unittest.TestCase):
    """While macOS says a secret is being typed, nothing typed goes on."""

    def setUp(self):
        self.secret = False
        self.asked = 0
        self.said = []

        def probe():
            self.asked += 1
            return self.secret
        self.secure = SecureInput(self.said.append, probe)

    def test_keys_and_positions_stop_while_it_is_on(self):
        key, press = {"kind": "key", "type": "keyDown", "chars": "p"}, {"kind": "press", "pos": 3}
        self.assertTrue(self.secure.allows(key) and self.secure.allows(press))
        self.secret = True
        for msg in (key, press, {"kind": "release", "pos": 3}):
            self.assertFalse(self.secure.allows(msg))
        self.assertFalse(self.secure.allows({"kind": "layers", "ids": [2]}))  # which layer a character came from is part of it
        self.assertTrue(self.secure.allows({"kind": "device", "name": "Diamond"}))
        self.secret = False
        self.assertTrue(self.secure.allows(key))

    def test_it_is_asked_at_every_keystroke(self):
        # Not only by the poll: the first key typed into a password field must not slip through.
        self.secret = True
        self.assertFalse(self.secure.allows({"kind": "key", "type": "keyDown", "chars": "p"}))
        self.assertEqual(1, self.asked)

    def test_layers_are_asked_about_at_once_too(self):
        # A layer held for a symbol is the first thing to arrive at a password field.
        self.secret = True
        self.assertFalse(self.secure.allows({"kind": "layers", "ids": [2], "device": "A"}))
        self.assertEqual(1, self.asked)

    def test_layers_are_held_and_the_latest_follows_when_it_ends(self):
        self.secret = True
        self.assertFalse(self.secure.allows({"kind": "layers", "ids": [2], "device": "A"}))
        self.assertFalse(self.secure.allows({"kind": "layers", "ids": [2, 5], "device": "A"}))
        self.assertFalse(self.secure.allows({"kind": "layers", "ids": [0], "device": "B"}))
        self.assertEqual([{"kind": "secure", "on": True, "watched": []}], self.said)
        self.secret = False
        self.secure.check()
        self.assertEqual([{"kind": "secure", "on": True, "watched": []}, {"kind": "secure", "on": False, "watched": []},
                          {"kind": "layers", "ids": [2, 5], "device": "A"},      # the latest of each keyboard, once
                          {"kind": "layers", "ids": [0], "device": "B"}], self.said)
        self.secure.check()
        self.assertEqual(4, len(self.said))                                     # and nothing is held any more

    def test_a_probe_that_does_not_know_yet_holds_the_keys_and_says_nothing(self):
        # The accessibility watch, while an app is asked what the widget that gained focus is.
        key, layers = {"kind": "key", "type": "keyDown", "chars": "p"}, {"kind": "layers", "ids": [3], "device": "A"}
        self.secret = None
        self.assertFalse(self.secure.allows(key))
        self.assertFalse(self.secure.allows(layers))
        self.assertEqual([], self.said)                          # the page is not told of a lookup
        self.secret = True
        self.assertFalse(self.secure.allows(key))
        self.assertEqual([{"kind": "secure", "on": True, "watched": []}], self.said)
        self.secret = None
        self.assertFalse(self.secure.allows(key))
        self.assertEqual(1, len(self.said))                      # still on, nothing new said
        self.secret = False
        self.assertTrue(self.secure.allows(key))
        self.assertEqual([{"kind": "secure", "on": True, "watched": []}, {"kind": "secure", "on": False, "watched": []}, layers], self.said)

    def test_a_layer_change_held_during_a_lookup_that_was_no_secret_goes_on_at_once(self):
        layers = {"kind": "layers", "ids": [3], "device": "A"}
        self.secret = None
        self.assertFalse(self.secure.allows(layers))
        self.secret = False
        self.assertTrue(self.secure.allows({"kind": "key", "type": "keyDown", "chars": "p"}))
        self.assertEqual([layers], self.said)                    # no secure message: it never was one

    def test_what_watches_is_said_at_the_start_and_on_every_message(self):
        said = []
        secure = SecureInput(said.append, lambda: self.secret, watched=("terminals", "lock"))
        secure.start()
        secure.stop()
        self.assertEqual([{"kind": "secure", "on": False, "watched": ["terminals", "lock"]}], said)
        self.secret = True
        secure.check()
        self.assertEqual({"kind": "secure", "on": True, "watched": ["terminals", "lock"]}, said[-1])
        said.clear()
        secure = SecureInput(said.append, lambda: True, watched=("carbon",))
        secure.start()
        secure.stop()
        self.assertEqual([{"kind": "secure", "on": True, "watched": ["carbon"]}], said)    # on at once: said once

    def test_the_report_names_each_probe(self):
        lines = [line.split("\t") for line in probe_report()]
        self.assertEqual(["carbon", "terminals", "a11y", "lock"], [name for name, _, _ in lines])
        self.assertTrue(all(state in ("on", "off", "none") and why for _, state, why in lines), lines)
        if sys.platform == "darwin":
            self.assertEqual("on", lines[0][1])
            self.assertEqual(("none", "none"), (lines[2][1], lines[3][1]))
            with mock.patch("hudfeed.carbon_probe", return_value=None):
                self.assertEqual("off", probe_report()[0].split("\t")[1])

    def test_any_of_the_probes(self):
        self.assertIsNone(any_of([lambda: False, lambda: None])())
        self.assertTrue(any_of([lambda: None, lambda: True])())
        self.assertFalse(any_of([lambda: False, lambda: False])())
        self.assertFalse(any_of([])())

    def test_the_page_is_told_when_it_changes(self):
        self.secure.check()
        self.secret = True
        self.secure.check()
        self.secure.check()
        self.secret = False
        self.secure.check()
        self.assertEqual([{"kind": "secure", "on": True, "watched": []}, {"kind": "secure", "on": False, "watched": []}], self.said)

    def test_a_page_that_connects_is_told_too(self):
        hub = Hub()
        asyncio.run(hub.send({"kind": "secure", "on": True, "watched": []}))
        self.assertIn("secure", hub.cache)

    @mock.patch("hudfeed.a11y.available", new=lambda: False)
    def test_the_feed_asks_the_system_and_the_terminals(self):
        # Either saying so is enough; and where neither can be asked, there is nothing to hold back.
        # Only the probes this test supplies exist, regardless of the desktop running the suite.
        said = {"system": False, "terminal": False, "lock": False}
        probes = {"system": lambda: said["system"], "terminal": lambda: said["terminal"], "lock": lambda: said["lock"]}

        def feed_with(**have):
            with mock.patch("hudfeed.carbon_probe", return_value=probes["system"] if have.get("system") else None), \
                 mock.patch("hudfeed.terminal_probe", return_value=probes["terminal"] if have.get("terminal") else None), \
                 mock.patch("hudfeed.sessionlock.probe", return_value=probes["lock"] if have.get("lock") else None), \
                 tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "config.yaml")
                with open(path, "w", encoding="utf-8") as f:
                    f.write("keyboard: {name: Test Board}\nserial: {port: /dev/cu.test}\nble: {enabled: false}\n")
                return Feed(lambda msg: None, log=lambda *a: None, config=path, keys=False, sessions=False)
        feed = feed_with(system=True, terminal=True)
        self.assertFalse(feed.secure.probe())
        said["terminal"] = True
        self.assertTrue(feed.secure.probe())      # a terminal's prompt counts as much as the switch
        said.update(system=True, terminal=False)
        self.assertTrue(feed.secure.probe())
        said["system"] = False
        self.assertIsNotNone(feed_with(terminal=True).secure)    # Linux: the terminals alone
        feed = feed_with()
        self.assertIsNone(feed.secure)
        self.assertEqual((), feed.watched)
        # Nothing watching is said when it starts, in the log and to the page, so nobody trusts it.
        told, logged = [], []
        with mock.patch("hudfeed.carbon_probe", return_value=None), mock.patch("hudfeed.terminal_probe", return_value=None), \
             mock.patch("hudfeed.sessionlock.probe", return_value=None), mock.patch("hudfeed.SerialReader.start"), \
             tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.yaml")
            with open(path, "w", encoding="utf-8") as f:
                f.write("keyboard: {name: Test Board}\nserial: {port: /dev/cu.test}\nble: {enabled: false}\n")
            Feed(told.append, log=logged.append, config=path, keys=False, sessions=False, keymap=False).start()
        self.assertIn({"kind": "secure", "on": False, "watched": []}, told)
        self.assertTrue(any("nothing here sees a secret" in line for line in logged), logged)
        feed = feed_with(terminal=True, lock=True)               # Linux under Hyprland: the session lock too
        self.assertEqual(("terminals", "lock"), feed.watched)
        self.assertFalse(feed.secure.probe())
        said["lock"] = True
        self.assertTrue(feed.secure.probe())

    @mock.patch("hudfeed.a11y.available", new=lambda: False)
    @mock.patch("hudfeed.sessionlock.probe", new=lambda log=None: None)
    def test_raw_logging_is_held_while_it_is_on(self):
        # `feed --raw` logs what the readers decode, which is the typing: not while a secret is typed.
        said = {"terminal": False}
        with mock.patch("hudfeed.carbon_probe", return_value=None), \
             mock.patch("hudfeed.terminal_probe", return_value=lambda: said["terminal"]), \
             tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.yaml")
            with open(path, "w", encoding="utf-8") as f:
                f.write("keyboard: {name: Test Board}\nserial: {port: /dev/cu.test}\nble: {enabled: false}\n")
            feed = Feed(lambda msg: None, log=lambda *a: None, config=path, keys=False, sessions=False, raw=True)
            self.assertTrue(feed.reader.raw())
            said["terminal"] = True
            self.assertFalse(feed.reader.raw())
            with open(path, "a", encoding="utf-8") as f:
                f.write("feed: {secure_input: 0}\n")
            off = Feed(lambda msg: None, log=lambda *a: None, config=path, keys=False, sessions=False, raw=True)
            self.assertIsNone(off.secure)
            self.assertTrue(off.reader.raw())                    # nothing watches, nothing holds
            quiet = Feed(lambda msg: None, log=lambda *a: None, config=path, keys=False, sessions=False)
            self.assertFalse(quiet.reader.raw)                   # and without --raw, no log at all

    @mock.patch("hudfeed.sessionlock.probe", new=lambda log=None: None)
    def test_on_linux_the_accessibility_bus_is_watched_too(self):
        # A password field with focus in a window (host/a11y.py), with the apps told to expose their
        # widgets unless the config says to leave that alone.
        def feed_with(text):
            with mock.patch("hudfeed.carbon_probe", return_value=None), \
                 mock.patch("hudfeed.terminal_probe", return_value=None), \
                 mock.patch("hudfeed.a11y.available", return_value=True), \
                 mock.patch("hudfeed.a11y.PasswordFocus") as focus, \
                 tempfile.TemporaryDirectory() as tmp:
                focus.return_value.probe.return_value = False
                path = os.path.join(tmp, "config.yaml")
                with open(path, "w", encoding="utf-8") as f:
                    f.write("keyboard: {name: Test Board}\nserial: {port: /dev/cu.test}\nble: {enabled: false}\n" + text)
                feed = Feed(lambda msg: None, log=lambda *a: None, config=path, keys=False, sessions=False)
                return feed, focus
        feed, focus = feed_with("")
        self.assertIs(feed.focus, focus.return_value)
        self.assertTrue(focus.call_args.kwargs["enable"])
        self.assertFalse(feed.secure.probe())
        focus.return_value.probe.return_value = True
        self.assertTrue(feed.secure.probe())
        feed, focus = feed_with("feed: {accessibility: 0}\n")
        self.assertFalse(focus.call_args.kwargs["enable"])
        feed, focus = feed_with("feed: {secure_input: 0}\n")
        self.assertIsNone(feed.focus)
        self.assertFalse(focus.called)


class TerminalPrompts(unittest.TestCase):
    """A terminal asking for a secret is a secret being typed: on Linux, which has no switch, and
    on macOS beside it."""

    def setUp(self):
        try:
            self.master, self.slave = os.openpty()
        except OSError as e:               # a sandbox that hands out no terminals
            self.skipTest(f"no pty to be had here: {e}")
        self.addCleanup(os.close, self.master)
        self.addCleanup(os.close, self.slave)
        self.path = os.ttyname(self.slave)

    def mode(self, on=0, off=0):
        attrs = termios.tcgetattr(self.slave)
        attrs[3] = (attrs[3] | on) & ~off
        termios.tcsetattr(self.slave, termios.TCSANOW, attrs)

    def test_echo_off_a_line_at_a_time_is_a_password_prompt(self):
        self.mode(on=termios.ECHO | termios.ICANON)
        self.assertFalse(asks_secret(self.path))         # nothing asked: the terminal echoes
        self.mode(off=termios.ECHO)
        self.assertTrue(asks_secret(self.path))          # sudo, ssh, su, getpass: echo off, a line at a time
        self.mode(off=termios.ICANON)
        self.assertFalse(asks_secret(self.path))         # a shell's line editor, an editor: raw, key by key
        self.mode(on=termios.ECHO)
        self.assertFalse(asks_secret(self.path))

    def test_what_is_not_a_terminal_of_ours_says_nothing(self):
        self.assertFalse(asks_secret(os.path.join(tempfile.gettempdir(), "no-such-tty")))
        with tempfile.NamedTemporaryFile() as f:
            self.assertFalse(asks_secret(f.name))        # a file, not a terminal

    def test_the_probe_looks_through_the_terminals(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.symlink(self.path, os.path.join(tmp, "7"))
            with open(os.path.join(tmp, "ptmx"), "w"):   # /dev/pts has one; it is nobody's terminal
                pass
            probe = terminal_probe((tmp, re.compile(r"\d+$")))
            self.mode(on=termios.ECHO | termios.ICANON)
            self.assertFalse(probe())
            self.mode(off=termios.ECHO)
            self.assertTrue(probe())
        self.assertIsNone(terminal_probe(("/no/such/dir", re.compile(r"\d+$"))))   # nowhere to look


class FakeSocket:
    """A client of the Hub: what it sends, then the end of the connection."""

    def __init__(self, *incoming):
        self.incoming = [json.dumps(m) for m in incoming]
        self.sent = []

    async def send(self, data):
        self.sent.append(json.loads(data))

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.incoming:
            raise StopAsyncIteration
        return self.incoming.pop(0)


class FakeStore:
    apply_said = session.Store.apply_said     # the real routing, onto the recording methods below

    def __init__(self):
        self.applied, self.modes, self.prefs, self.flushed = [], [], [], 0

    def apply(self, msg):
        self.applied.append(msg)

    def set_heatmap(self, mode):
        self.modes.append(mode)

    def set_pref(self, name, value):
        self.prefs.append((name, value))

    def flush(self):
        self.flushed += 1


class Sessions(unittest.TestCase):
    """The page reports its counts over the socket; only the panel's own page may."""

    def talk(self, hub, *incoming):
        ws = FakeSocket(*incoming)
        asyncio.run(hub.handler(ws))
        return ws

    def test_counts_come_only_with_the_panels_token(self):
        store = FakeStore()
        hub = Hub(sessions=store, tally_token="t0k")
        self.talk(hub, {"kind": "tally", "seq": 1}, {"kind": "tally", "seq": 2, "token": "guess"},
                  {"kind": "tally", "seq": 3, "token": "t0k"}, {"kind": "heatmap", "mode": "session", "token": "t0k"},
                  {"kind": "heatmap", "mode": "off"}, {"kind": "pref", "name": "theme", "value": "dark", "token": "t0k"},
                  {"kind": "pref", "name": "side", "value": "hidden"})
        self.assertEqual([3], [m["seq"] for m in store.applied])
        self.assertEqual(["session"], store.modes)
        self.assertEqual([("theme", "dark")], store.prefs)

    def test_without_a_token_nothing_counts(self):
        store = FakeStore()
        self.talk(Hub(sessions=store), {"kind": "tally", "seq": 1, "token": None}, {"kind": "tally", "seq": 2, "token": ""})
        self.assertEqual([], store.applied)

    def test_sent_in_is_marked_and_the_keyboard_said_again(self):
        # The demo's player and every client go this one way.
        again = []
        hub = Hub(on_inject=lambda: again.append(1))
        page = FakeSocket()
        hub.clients.add(page)
        asyncio.run(hub.send_in({"kind": "press", "pos": 3}))
        self.assertEqual([{"kind": "press", "pos": 3, "sent": True}], page.sent)
        self.assertEqual([1], again)

    def test_a_page_that_is_no_client_is_given_what_was_sent_in(self):
        # The macOS panel's page is reached through its bridge, not the socket.
        given = []
        hub = Hub(on_sent_in=given.append)
        self.talk(hub, {"kind": "press", "pos": 3}, {"kind": "tally", "seq": 1}, {"kind": "keymap"})
        self.assertEqual([{"kind": "press", "pos": 3, "sent": True}], given)

    def test_a_page_that_connects_gets_the_session(self):
        hub = Hub()
        asyncio.run(hub.send({"kind": "session", "id": "abc", "gen": 0}))
        ws = self.talk(hub)
        self.assertEqual(["session"], [m["kind"] for m in ws.sent])

    def test_close_writes_the_counts_before_it_exits(self):
        store = FakeStore()

        class Exited(Exception):
            pass

        def exit_(code):
            raise Exited(code)
        real = os._exit
        os._exit = exit_
        try:
            with self.assertRaises(Exited):
                self.talk(Hub(sessions=store), {"kind": "close"})
        finally:
            os._exit = real
        self.assertEqual(1, store.flushed)


try:
    import websockets
    HAVE_WEBSOCKETS = True
except ImportError:
    HAVE_WEBSOCKETS = False


def can_listen():
    """Whether this process may open a local port at all (a sandbox may say no)."""
    import socket
    try:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
        return True
    except OSError:
        return False


class Gate(unittest.TestCase):
    """What the socket lets through, before the upgrade: the run's token in the URL path, from a
    local page or a client that is not a browser. Driven directly, with no port."""

    def request(self, path, origin=None):
        headers = {} if origin is None else {"Origin": origin}
        return types.SimpleNamespace(path=path, headers=headers)

    def gate(self, path, origin=None, token="t0ken"):
        hub = Hub(token=token)
        hub.log = lambda *a: self.said.append(" ".join(str(x) for x in a))
        connection = types.SimpleNamespace(respond=lambda status, text: (int(status), text))
        return hub.process_request(connection, self.request(path, origin))

    def setUp(self):
        self.said = []

    def test_the_token_in_the_path_lets_a_client_in(self):
        self.assertIsNone(self.gate("/t0ken"))
        self.assertIsNone(self.gate("/t0ken/"))
        self.assertIsNone(self.gate("/t0ken?x=1"))             # a query is not the token's business
        self.assertEqual([], self.said)

    def test_without_it_or_with_another_a_client_is_refused_before_the_upgrade(self):
        for path in ("/", "", "/T0KEN", "/t0ke", "/t0ken1", "/other/t0ken"):
            status, text = self.gate(path)
            self.assertEqual(403, status, path)
            self.assertIn("token", text)
        self.assertEqual(6, len(self.said))
        self.assertTrue(all("refused" in s for s in self.said), self.said)
        self.assertFalse(any("T0KEN" in s or "t0ke" in s for s in self.said), "what was offered is never logged")

    def test_a_hub_without_a_token_refuses_everyone_and_cannot_be_served(self):
        self.assertEqual(403, self.gate("/", token=None)[0])
        with self.assertRaises(ValueError):
            serve_hub(Hub(), "127.0.0.1", 0)

    def test_a_local_page_gets_in_and_a_page_from_elsewhere_does_not(self):
        for origin in ("file://", "null", "http://127.0.0.1:8765", "http://localhost", "https://localhost:443",
                       "http://[::1]:8790"):
            self.assertIsNone(self.gate("/t0ken", origin), origin)
        for origin in ("https://evil.example", "http://127.0.0.1.evil.example", "http://localhost.evil.example",
                       "ws://127.0.0.1", "file:///etc", ""):
            status, _ = self.gate("/t0ken", origin)
            self.assertEqual(403, status, origin)
        self.assertIn("origin https://evil.example", self.said[0])


@unittest.skipUnless(HAVE_WEBSOCKETS, "the socket needs python-websockets (the venv)")
@unittest.skipUnless(can_listen(), "this process may not open a local port")
class Socket(unittest.TestCase):
    """The macOS panel's socket: a Hub on a thread of its own, fed from the panel's."""

    def test_what_is_sent_early_is_there_for_a_client_and_what_it_sends_comes_back(self):
        given = []
        thread = HubThread(Hub(on_sent_in=given.append, token="t"), 0, log=lambda *a: None)
        thread.send({"kind": "keymap", "layers": {}})              # the feed starts before the socket
        thread.start()
        self.assertTrue(thread.ready.wait(5) and thread.serving)

        async def client():
            async with websockets.connect(f"ws://127.0.0.1:{thread.port}/t") as ws:
                first = json.loads(await asyncio.wait_for(ws.recv(), 3))
                await ws.send(json.dumps({"kind": "press", "pos": 5}))
                echo = json.loads(await asyncio.wait_for(ws.recv(), 3))
                return first, echo
        try:
            first, echo = asyncio.run(client())
        finally:
            thread.stop()
        self.assertEqual("keymap", first["kind"])
        self.assertEqual({"kind": "press", "pos": 5, "sent": True}, echo)
        self.assertEqual([echo], given)

    def test_a_client_without_the_token_or_from_another_site_gets_nothing(self):
        given = []
        hub = Hub(on_sent_in=given.append, token="t")
        thread = HubThread(hub, 0, log=lambda *a: None)
        thread.send({"kind": "keymap", "layers": {}})
        thread.start()
        self.assertTrue(thread.ready.wait(5) and thread.serving)

        async def refused(url, **kw):
            try:
                async with websockets.connect(url, **kw) as ws:
                    await ws.send(json.dumps({"kind": "press", "pos": 5}))
                    return "connected"
            except websockets.InvalidStatus as e:
                return e.response.status_code

        async def client():
            return (await refused(f"ws://127.0.0.1:{thread.port}"),
                    await refused(f"ws://127.0.0.1:{thread.port}/T"),
                    await refused(f"ws://127.0.0.1:{thread.port}/t", origin="https://evil.example"),
                    await refused(f"ws://127.0.0.1:{thread.port}/t", origin="http://127.0.0.1:8765"))
        try:
            outcomes = asyncio.run(client())
        finally:
            thread.stop()
        self.assertEqual((403, 403, 403, "connected"), outcomes)
        self.assertEqual(1, len(given), "only the local page's press was fanned out")
        self.assertEqual(set(), hub.clients)

    def test_a_port_taken_is_said_and_nothing_breaks(self):
        said = []
        first = HubThread(Hub(token="t"), 0, log=lambda *a: None).start()
        self.assertTrue(first.ready.wait(5) and first.serving)
        second = HubThread(Hub(token="t"), first.port, log=said.append).start()
        try:
            self.assertTrue(second.ready.wait(5))
            self.assertFalse(second.serving)
            second.send({"kind": "layers", "ids": []})               # goes nowhere, and raises nothing
        finally:
            first.stop()
            second.stop()
        self.assertTrue(any("poke" in m for m in said), said)


class PortTaken(unittest.TestCase):
    """Once a port has produced a frame it is the signal, and the feed takes it for this process
    alone (TIOCEXCL): on macOS /dev/cu.* is open to every user, and with `positions;` on the port
    carries which key went down and when. Not before: a composite board's other CDC ports are tried
    too, and the tools that want them must still get them."""

    class Dev:
        """A serial port that produces what it was given and then nothing, at which point it
        stops the reader -- the real loop runs until the feed stops or the port goes."""
        def __init__(self, frames, reader):
            self.chunks, self.reader = list(frames), reader
            self.in_waiting, self.timeout, self.closed = 0, 0.5, False
        def fileno(self): return 42
        def read(self, n):
            if self.chunks:
                return self.chunks.pop(0)
            self.reader.stop()
            return b""
        def close(self): self.closed = True

    @staticmethod
    def frame(kind, payload):
        body = bytes([signal_frame.VERSION, kind, len(payload)]) + payload
        return signal_frame.MAGIC + body + bytes([signal_frame.crc8(body)])

    def read_once(self, frames, reader):
        # The frames go in, then the empty read: the probe is over (probe_s < 0) and the reader stopped.
        reader.probe_s = -1
        dev = self.Dev(frames, reader)
        reader._read_loop("/dev/cu.test", dev, "Test Board")
        return dev

    def test_taken_once_the_first_frame_has_been_seen(self):
        import termios
        emitted, said = [], []
        reader = SerialReader(emitted.append, log=said.append)
        with mock.patch("fcntl.ioctl") as ioctl:
            dev = self.read_once([self.frame(signal_frame.KIND_LAYERS, (0b110).to_bytes(4, "little"))], reader)
        ioctl.assert_called_once_with(42, termios.TIOCEXCL)
        self.assertEqual([{"kind": "layers", "ids": [1, 2]}, {"kind": "device", "name": "Test Board"}],
                         [{k: v for k, v in m.items() if k != "device"} if m["kind"] != "device" else m for m in emitted])
        self.assertTrue(dev.closed)

    def test_a_port_that_is_not_the_signal_is_left_as_it_was(self):
        reader = SerialReader(lambda m: None, log=lambda *a: None)
        with mock.patch("fcntl.ioctl") as ioctl:
            self.read_once([b"\x00\x01\x02 not a frame"], reader)
        ioctl.assert_not_called()
        self.assertEqual("no frames", reader._failed.get("/dev/cu.test"))

    def test_a_port_that_cannot_be_taken_is_still_read(self):
        said = []
        reader = SerialReader(lambda m: None, log=said.append)
        with mock.patch("fcntl.ioctl", side_effect=OSError(25, "Inappropriate ioctl for device")):
            self.read_once([self.frame(signal_frame.KIND_LAYERS, (1).to_bytes(4, "little"))], reader)
        self.assertTrue(any("could not take the port" in s for s in said), said)
        self.assertTrue(any("reading Test Board" in s for s in said), said)

    def test_a_permission_failure_is_retried_without_unplugging(self):
        # udev can grant access after enumeration. HID typing keeps arriving meanwhile,
        # but without the serial layer signal Alpha 2's k looks like Alpha 1's combo.
        emitted, said = [], []
        reader = SerialReader(emitted.append, log=said.append)
        dev = self.Dev([self.frame(signal_frame.KIND_LAYERS, (1 << 13).to_bytes(4, "little"))], reader)
        serial = types.ModuleType("serial")
        serial.SerialException = OSError
        serial.Serial = mock.Mock(side_effect=[PermissionError(13, "Permission denied"),
                                              PermissionError(13, "Permission denied"), dev])
        ports = types.ModuleType("serial.tools")
        ports.list_ports = mock.Mock()

        def start_read(**kwargs):
            return types.SimpleNamespace(start=lambda: kwargs["target"](*kwargs["args"]))

        def scan_wait(_):
            # The port stays connected, and access is granted after two failed scans.
            if serial.Serial.call_count >= 3 or reader._stop.wait.call_count >= 3:
                reader.stop()

        with mock.patch.dict(sys.modules, {"serial": serial, "serial.tools": ports}), \
             mock.patch.object(reader, "find", return_value=[("/dev/cu.test", "Test Board")]), \
             mock.patch.object(reader._stop, "wait", side_effect=scan_wait), \
             mock.patch("hudfeed.threading.Thread", side_effect=start_read), mock.patch("fcntl.ioctl"):
            reader._scan_loop()
        self.assertIn({"kind": "layers", "ids": [13], "device": "Test Board"}, emitted)
        self.assertEqual(3, serial.Serial.call_count)
        self.assertNotIn("/dev/cu.test", reader._failed)
        self.assertNotIn("/dev/cu.test", reader._retrying)
        self.assertEqual(1, sum("cannot open" in line for line in said))


class SerialScan(unittest.TestCase):
    """The serial reader's scan with a pyserial that lists what each round says."""

    def scan(self, *rounds):
        said = []
        reader = SerialReader(lambda m: None, log=said.append)
        left = list(rounds)

        def comports():
            got = left[0]
            if isinstance(got, Exception):
                raise got
            return got
        list_ports = types.SimpleNamespace(comports=comports)
        serial = types.ModuleType("serial")
        tools = types.ModuleType("serial.tools")
        tools.list_ports = list_ports
        serial.tools = tools

        def next_round(_s):
            left.pop(0)
            if not left:
                reader._stop.set()
        with mock.patch.dict(sys.modules, {"serial": serial, "serial.tools": tools, "serial.tools.list_ports": list_ports}), \
                mock.patch.object(reader._stop, "wait", side_effect=next_round):
            reader._scan_loop()
        return said

    def test_no_port_is_said_once_and_not_every_rescan(self):
        said = self.scan([], [], [])
        self.assertEqual(1, len(said), said)
        self.assertIn("no serial port 1d50:615e", said[0])

    def test_a_port_that_cannot_be_listed_is_said_once_per_change(self):
        said = self.scan(OSError("busy"), OSError("busy"), OSError("busy"))
        self.assertEqual(1, len([s for s in said if "cannot list serial ports" in s]), said)


class ReaderLoop(unittest.TestCase):
    """The one loop every keyboard connection is read by (hudfeed.Reader), on a transport that finds
    what each scan says and reads what it is given."""

    class Fake:
        def __init__(self, reports):
            self.reports, self.closed = list(reports), False

        def close(self):
            self.closed = True

    def reader(self, rounds, refuse=(), reports=()):
        import hudfeed
        test = self

        class Fake(hudfeed.Reader):
            kind = "fake"

            def find(self):
                return [(p, "Board") for p in rounds[0]]

            def note(self, found):
                return None if found else "hudfeed: no fake keyboard"

            def open(self, path, product):
                test.opened.append(path)
                if path in refuse:
                    raise hudfeed.OpenFailed(f"hudfeed: cannot open {path}", "hudfeed: replug it")
                return test.Fake(reports)

            def read(self, conn, timeout):
                return conn.reports.pop(0) if conn.reports else self.stop()
        self.opened, self.said, self.out = [], [], []
        r = Fake(self.out.append, log=self.said.append, dead_key_ms=20)

        def next_round(_s):
            rounds.pop(0)
            if not rounds:
                r._stop.set()
        r._stop.wait = next_round
        return r

    def test_a_device_that_would_not_open_is_tried_again_once_it_comes_back(self):
        r = self.reader([["a"], ["a"], [], ["a"]], refuse=("a",))
        r._scan_loop()
        self.assertEqual(["a", "a"], self.opened)                   # not every scan: after the replug
        self.assertEqual(["hudfeed: cannot open a", "hudfeed: replug it", "hudfeed: no fake keyboard",
                          "hudfeed: cannot open a", "hudfeed: replug it"], self.said)

    def test_with_retry_opens_a_device_that_would_not_open_is_tried_each_scan_and_said_once(self):
        r = self.reader([["a"], ["a"], ["a"], ["a"]], refuse=("a",))
        r.retry_opens = True
        r._scan_loop()
        self.assertEqual(["a", "a", "a", "a"], self.opened)
        self.assertEqual(["hudfeed: cannot open a", "hudfeed: replug it"], self.said)
        self.assertNotIn("a", r._failed)

    def test_a_keyboard_is_read_said_and_its_keys_released_when_it_ends(self):
        r = self.reader([["a"]], reports=[{"kind": "keys", "mods": 0x02, "keys": [0x04]}])
        conn = self.Fake([{"kind": "keys", "mods": 0x02, "keys": [0x04]}])
        with r._lock:
            r._open["a"] = conn
        r._read_loop("a", conn, "Board")
        self.assertTrue(conn.closed)
        self.assertNotIn("a", r._open)
        said = [(m["type"], m["chars"] if m["type"] != "flagsChanged" else m["flags"]["shift"]) for m in self.out]
        self.assertEqual([("flagsChanged", True), ("keyDown", "A"), ("flagsChanged", False), ("keyUp", "a")], said)

    def test_a_keyboard_that_ends_leaves_held_what_another_still_holds(self):
        import hudfeed
        r = self.reader([["a"]])
        other = hudfeed.Stream(lambda m: None, "Other")
        other.message({"kind": "keys", "mods": 0x02, "keys": []}, 0)   # Shift, on the other keyboard
        r._streams.add(other)
        conn = self.Fake([{"kind": "keys", "mods": 0x02, "keys": [0x04]}])
        r._read_loop("a", conn, "Board")
        said = [(m["type"], m["chars"] if m["type"] != "flagsChanged" else m["flags"]["shift"]) for m in self.out]
        self.assertEqual([("flagsChanged", True), ("keyDown", "A"), ("keyUp", "A")], said)

    def test_a_listing_that_fails_is_said_and_the_scan_goes_on(self):
        r = self.reader([["a"], ["a"]])
        r.find = mock.Mock(side_effect=[OSError("bus"), []])
        r._scan_loop()
        self.assertEqual(["hudfeed: cannot list devices: OSError: bus", "hudfeed: no fake keyboard"], self.said)


class BleReading(unittest.TestCase):
    """The BLE reader's session and loop with a client that connects at once and is let go when the
    test says: bleak's interface as hudfeed uses it, no adapter."""

    def reader(self, rescan=2.0):
        import hudfeed
        self.said, self.out = [], []
        return hudfeed.BleReader(self.out.append, log=self.said.append, rescan=rescan)

    @staticmethod
    def client(drop_after):
        class Client:
            def __init__(self, device, disconnected_callback):
                self.gone = disconnected_callback

            async def __aenter__(self):
                asyncio.get_running_loop().call_later(drop_after, self.gone, self)
                return self

            async def __aexit__(self, *exc):
                return False

            async def start_notify(self, uuid, callback):
                pass
        return Client

    def test_a_keyboard_that_disconnects_is_said_gone(self):
        r = self.reader()
        device = types.SimpleNamespace(name="Diamond", address="AA")
        self.assertIsNone(asyncio.run(r._session(self.client(0.05), device)))
        self.assertIn("hudfeed: reading Diamond over BLE", self.said)
        self.assertTrue(any("Diamond over BLE gone" in s for s in self.said), self.said)

    def test_a_connection_with_nothing_to_wait_for_is_not_woken_every_few_milliseconds(self):
        import hudfeed
        r = self.reader()
        device = types.SimpleNamespace(name="Diamond", address="AA")
        with mock.patch.object(hudfeed.Stream, "idle") as idle:
            asyncio.run(r._session(self.client(0.4), device))
        self.assertLessEqual(idle.call_count, 1)          # not once every dead_key_ms (60) for 400 ms

    def test_stop_ends_a_backoff_at_once(self):
        import time
        r = self.reader(rescan=3.0)

        async def nothing():
            return None
        r._find = nothing
        threading.Timer(0.1, r.stop).start()
        started = time.monotonic()
        with mock.patch.dict(sys.modules, {"bleak": types.SimpleNamespace(BleakClient=None)}):
            asyncio.run(r._loop())
        self.assertLess(time.monotonic() - started, 1.0)       # not the 3 s backoff


class BluezConnected(unittest.TestCase):
    """The keyboard BlueZ has connected, from its managed objects: no scan, no adapter."""

    SVC = "d1f0a7c2-6b3e-4f8a-9c21-5e7b4a0d9f31"

    @staticmethod
    def device(address, name, connected=True, uuids=(), path=None, resolved=True):
        """`resolved`: ServicesResolved, and None for a device that does not have the property."""
        v = lambda x: types.SimpleNamespace(value=x)
        props = {"Connected": v(connected), "Address": v(address), "Name": v(name), "UUIDs": v(list(uuids))}
        if resolved is not None:
            props["ServicesResolved"] = v(resolved)
        return (path or "/org/bluez/hci0/dev_" + address.replace(":", "_")), {"org.bluez.Device1": props}

    def objects(self, *devices):
        return dict(devices)

    def test_the_connected_keyboard_with_the_service_is_found(self):
        import hudfeed
        objs = self.objects(self.device("F9:BD:CA:10:F7:A8", "TwoNr9", uuids=[self.SVC]),
                            self.device("DC:2C:26:2A:31:DE", "Keychron K4", uuids=["1812"]))
        self.assertEqual(("/org/bluez/hci0/dev_F9_BD_CA_10_F7_A8", "F9:BD:CA:10:F7:A8", "TwoNr9"),
                         hudfeed.bluez_connected(objs))

    def test_a_keyboard_not_connected_is_not_found(self):
        import hudfeed
        objs = self.objects(self.device("F9:BD:CA:10:F7:A8", "TwoNr9", connected=False, uuids=[self.SVC]))
        self.assertIsNone(hudfeed.bluez_connected(objs))

    def test_a_connected_keyboard_without_the_service_is_not_taken(self):
        import hudfeed
        objs = self.objects(self.device("DC:2C:26:2A:31:DE", "Keychron K4", uuids=["1812"]))
        self.assertIsNone(hudfeed.bluez_connected(objs))

    def test_the_configured_address_and_name_pick_between_two(self):
        import hudfeed
        objs = self.objects(self.device("AA:AA:AA:AA:AA:01", "Other", uuids=[self.SVC]),
                            self.device("F9:BD:CA:10:F7:A8", "TwoNr9", uuids=[self.SVC]))
        self.assertEqual("F9:BD:CA:10:F7:A8", hudfeed.bluez_connected(objs, address="f9:bd:ca:10:f7:a8")[1])
        self.assertEqual("TwoNr9", hudfeed.bluez_connected(objs, name="twonr")[2])
        self.assertIsNone(hudfeed.bluez_connected(objs, address="11:22:33:44:55:66"))

    def test_a_keyboard_whose_services_are_not_resolved_is_not_taken_yet(self):
        import hudfeed
        for resolved in (False, None):          # not yet, or a device without the property
            objs = self.objects(self.device("F9:BD:CA:10:F7:A8", "TwoNr9", uuids=[self.SVC], resolved=resolved))
            self.assertIsNone(hudfeed.bluez_connected(objs), resolved)
            self.assertEqual([("/org/bluez/hci0/dev_F9_BD_CA_10_F7_A8", "F9:BD:CA:10:F7:A8", "TwoNr9")],
                             hudfeed.bluez_matches(objs, resolving=True), resolved)
        both = self.objects(self.device("AA:AA:AA:AA:AA:01", "Other", uuids=[self.SVC], resolved=False),
                            self.device("F9:BD:CA:10:F7:A8", "TwoNr9", uuids=[self.SVC]))
        self.assertEqual("F9:BD:CA:10:F7:A8", hudfeed.bluez_connected(both)[1])
        self.assertEqual(["AA:AA:AA:AA:AA:01"], [a for _, a, _ in hudfeed.bluez_matches(both, resolving=True)])

    def test_while_bluez_resolves_the_keyboard_there_is_no_scan_and_the_log_says_why(self):
        import hudfeed
        said = []
        r = hudfeed.BleReader(lambda m: None, log=said.append, rescan=0.0)
        answers = [self.objects(self.device("F9:BD:CA:10:F7:A8", "TwoNr9", uuids=[self.SVC], resolved=False))] * 2

        async def objects_now():
            return answers.pop(0) if answers else self.objects(
                self.device("F9:BD:CA:10:F7:A8", "TwoNr9", uuids=[self.SVC]))

        async def scan(*a, **kw):
            raise AssertionError("the scan should not run")

        async def pause(seconds):
            if not answers:
                r.stop()
        r._pause = pause
        modules = self.fake_bleak(scan)
        modules["bleak"].BleakScanner.find_device_by_filter = scan
        with mock.patch.object(hudfeed.sys, "platform", "linux"), \
                mock.patch.object(hudfeed, "bluez_objects", objects_now), \
                mock.patch.dict(sys.modules, modules):
            self.assertIsNone(asyncio.run(r._find()))
            self.assertIsNone(r._found_connected)
            modules["bleak"].BleakClient = None
            asyncio.run(r._loop())          # one more look while it resolves, said once, then stopped
            self.assertEqual("F9:BD:CA:10:F7:A8", asyncio.run(r._find()).address)   # resolved: taken
        self.assertEqual(["hudfeed: TwoNr9 (F9:BD:CA:10:F7:A8) is connected and BlueZ has not resolved its services "
                          "yet: looking again rather than connect to it now"], said)
        self.assertIsNone(r._resolving)

    def stuck(self, times, resolved_from=None, **picked):
        """A keyboard BlueZ never resolves (TwoNr9), Other advertising, looked for at `times` (s):
        -> (what each look found, the scan's filters by look, the log), the scan finding Other
        when it runs. `resolved_from`: the look from which TwoNr9 is resolved after all."""
        import hudfeed
        said, scans = [], {}
        r = hudfeed.BleReader(lambda m: None, log=said.append, rescan=0.0, **picked)
        clock = iter(times)
        r._clock = lambda: now
        other = types.SimpleNamespace(address="AA:AA:AA:AA:AA:01", name="Other")

        async def objects_now():
            resolved = resolved_from is not None and look >= resolved_from
            return self.objects(self.device("F9:BD:CA:10:F7:A8", "TwoNr9", uuids=[self.SVC], resolved=resolved))

        async def by_filter(match, timeout):
            scans[look] = match
            return other

        async def by_address(address, timeout):
            scans[look] = address
            return None
        modules = self.fake_bleak(by_address)
        modules["bleak"].BleakScanner.find_device_by_filter = by_filter
        found = []
        with mock.patch.object(hudfeed.sys, "platform", "linux"), \
                mock.patch.object(hudfeed, "bluez_objects", objects_now), \
                mock.patch.dict(sys.modules, modules):
            for look, now in enumerate(clock):
                device = asyncio.run(r._find())
                found.append(None if device is None else (device.address, device is r._found_connected))
        return found, scans, said

    def test_a_keyboard_that_never_resolves_holds_the_scan_back_only_as_long_as_bleak_would_have(self):
        import hudfeed
        self.assertEqual(30, hudfeed.BLE_RESOLVE_WAIT_S)
        found, scans, said = self.stuck([100, 110, 129.9, 130, 145])
        self.assertEqual([None, None, None, ("AA:AA:AA:AA:AA:01", False), ("AA:AA:AA:AA:AA:01", False)], found)
        self.assertEqual([3, 4], sorted(scans))
        advert = types.SimpleNamespace(service_uuids=[self.SVC])
        keyboard = lambda address: types.SimpleNamespace(address=address, name="")
        self.assertFalse(scans[3](keyboard("f9:bd:ca:10:f7:a8"), advert))     # the scan passes the stuck one over
        self.assertTrue(scans[3](keyboard("AA:AA:AA:AA:AA:01"), advert))
        self.assertEqual(["hudfeed: BlueZ has not resolved the services of TwoNr9 (F9:BD:CA:10:F7:A8) in 30 s: "
                          "scanning for another keyboard too (the scan passes this one over and leaves it connected)"],
                         said)

    def test_one_resolved_inside_the_wait_or_after_it_is_taken_by_the_lookup_and_never_by_the_scan(self):
        found, scans, _ = self.stuck([0, 10, 20], resolved_from=2)
        self.assertEqual([None, None, ("F9:BD:CA:10:F7:A8", True)], found)
        self.assertEqual({}, scans)
        found, scans, _ = self.stuck([0, 31, 32], resolved_from=2)
        self.assertEqual([None, ("AA:AA:AA:AA:AA:01", False), ("F9:BD:CA:10:F7:A8", True)], found)
        self.assertEqual([1], list(scans))

    def test_with_ble_address_set_the_one_it_names_is_waited_for_and_no_scan_runs(self):
        found, scans, said = self.stuck([0, 31, 300], address="f9:bd:ca:10:f7:a8")
        self.assertEqual([None, None, None], found)
        self.assertEqual(({}, []), (scans, said))

    def test_the_wait_starts_again_for_a_keyboard_that_resolved_and_is_resolving_once_more(self):
        import hudfeed
        r = hudfeed.BleReader(lambda m: None, log=lambda *a: None)
        r._clock = lambda: now
        answers = {0: False, 25: True, 40: False, 65: False, 71: False}

        async def objects_now():
            return self.objects(self.device("F9:BD:CA:10:F7:A8", "TwoNr9", uuids=[self.SVC], resolved=answers[now]))
        modules = self.fake_bleak(None)
        modules["bleak"].BleakScanner.find_device_by_filter = None
        stuck = []
        with mock.patch.object(hudfeed.sys, "platform", "linux"), \
                mock.patch.object(hudfeed, "bluez_objects", objects_now), \
                mock.patch.dict(sys.modules, modules):
            for now in answers:
                asyncio.run(r._connected_bluez())
                stuck.append(r._stuck)
        self.assertEqual([None, None, None, None, "F9:BD:CA:10:F7:A8"], stuck)     # 40 + 30, not 0 + 30

    def test_looks_while_bluez_resolves_keep_to_the_rescan_and_do_not_back_off(self):
        import hudfeed
        r = hudfeed.BleReader(lambda m: None, log=lambda *a: None, rescan=2.0)
        rounds = [None, None, "resolving", "resolving", "resolving", None, None]
        paused = []

        async def find():
            r._resolving = rounds.pop(0)
            return None

        async def pause(seconds):
            paused.append(seconds)
            if not rounds:
                r.stop()
        r._find, r._pause = find, pause
        with mock.patch.dict(sys.modules, {"bleak": types.SimpleNamespace(BleakClient=None)}):
            asyncio.run(r._loop())
        self.assertEqual([2.0, 4.0, 2.0, 2.0, 2.0, 2.0, 4.0], paused)

    def test_a_name_comes_back_fit_for_the_log_whatever_the_device_called_itself(self):
        import hudfeed
        forged = "TwoNr9\nhudfeed: reading Diamond over BLE\r\x1b[2K\x1b]0;title\x07‮ \x00"
        objs = self.objects(self.device("F9:BD:CA:10:F7:A8", forged, uuids=[self.SVC]),
                            self.device("AA:AA:AA:AA:AA:01", "K" * 300, uuids=[self.SVC]))
        (_, _, long), (_, _, label) = hudfeed.bluez_matches(objs)
        self.assertEqual("TwoNr9?hudfeed: reading Diamond over BLE??[2K?]0;title????", label)
        self.assertEqual("K" * 63 + "…", long)
        # ... and that is the name every line of the log about it is written with
        devices, said = self.finds(objs)
        self.assertEqual(long, devices[0].name)
        self.assertEqual(1, len(said))
        self.assertTrue(said[0].isprintable(), said[0])
        self.assertIn(label, said[0])
        # the configured name is still matched against the name as BlueZ gave it
        self.assertEqual("F9:BD:CA:10:F7:A8", hudfeed.bluez_connected(objs, name="twonr9\nhudfeed")[1])
        self.assertEqual("Keychron K4", hudfeed.printable("Keychron K4"))
        self.assertEqual("Ünïcödé ⌨ 键盘", hudfeed.printable("Ünïcödé ⌨ 键盘"))

    def test_of_several_the_first_by_object_path_is_taken_however_bluez_lists_them(self):
        import hudfeed
        two = [self.device("F9:BD:CA:10:F7:A8", "TwoNr9", uuids=[self.SVC]),
               self.device("AA:AA:AA:AA:AA:01", "Other", uuids=[self.SVC]),
               self.device("00:00:00:00:00:01", "Keychron K4", uuids=["1812"])]
        for listed in (two, two[::-1]):
            objs = self.objects(*listed)
            self.assertEqual(("/org/bluez/hci0/dev_AA_AA_AA_AA_AA_01", "AA:AA:AA:AA:AA:01", "Other"),
                             hudfeed.bluez_connected(objs))
            self.assertEqual(["AA:AA:AA:AA:AA:01", "F9:BD:CA:10:F7:A8"],
                             [address for _, address, _ in hudfeed.bluez_matches(objs)])

    def finds(self, objs, times=1, **picked):
        """-> (the devices _find gave, what the log said), BlueZ answering `objs` each time."""
        import hudfeed
        said = []
        r = hudfeed.BleReader(lambda m: None, log=said.append, **picked)

        async def objects_now():
            return objs

        async def scan(*a, **kw):
            raise AssertionError("the scan should not run")
        with mock.patch.object(hudfeed.sys, "platform", "linux"), \
                mock.patch.object(hudfeed, "bluez_objects", objects_now), \
                mock.patch.dict(sys.modules, self.fake_bleak(scan)):
            return [asyncio.run(r._find()) for _ in range(times)], said

    def test_several_connected_are_said_once_with_the_one_taken_and_how_to_pick(self):
        objs = self.objects(self.device("F9:BD:CA:10:F7:A8", "TwoNr9", uuids=[self.SVC]),
                            self.device("AA:AA:AA:AA:AA:01", "Other", uuids=[self.SVC]))
        devices, said = self.finds(objs, times=3)
        self.assertEqual(["AA:AA:AA:AA:AA:01"] * 3, [d.address for d in devices])
        self.assertEqual(["hudfeed: 2 connected BLE keyboards have the layer signal: reading Other "
                          "(AA:AA:AA:AA:AA:01), not TwoNr9 (F9:BD:CA:10:F7:A8); `ble.address` in the config picks "
                          "one (`keyboard.name` does too, and also picks the keyboard the serial and HID readers "
                          "take)"], said)

    def test_one_connected_or_one_picked_of_several_is_taken_without_a_word(self):
        one = self.objects(self.device("F9:BD:CA:10:F7:A8", "TwoNr9", uuids=[self.SVC]),
                           self.device("DC:2C:26:2A:31:DE", "Keychron K4", uuids=["1812"]))
        two = self.objects(self.device("F9:BD:CA:10:F7:A8", "TwoNr9", uuids=[self.SVC]),
                           self.device("AA:AA:AA:AA:AA:01", "Other", uuids=[self.SVC]))
        for objs, picked in ((one, {}), (two, {"address": "f9:bd:ca:10:f7:a8"}), (two, {"name": "twonr"})):
            devices, said = self.finds(objs, **picked)
            self.assertEqual((["F9:BD:CA:10:F7:A8"], []), ([d.address for d in devices], said), picked)

    @staticmethod
    def fake_bleak(scan):
        # bleak is not needed to test the order: the device and the scanner are stand-ins.
        device = types.ModuleType("bleak.backends.device")
        device.BLEDevice = lambda address, name, details: types.SimpleNamespace(
            address=address, name=name, details=details)
        scanner = types.SimpleNamespace(BleakScanner=types.SimpleNamespace(find_device_by_address=scan))
        return {"bleak": scanner, "bleak.backends.device": device, "bleak.backends": types.ModuleType("bleak.backends")}

    def test_on_linux_the_connected_keyboard_is_tried_before_any_scan(self):
        import hudfeed
        r = hudfeed.BleReader(lambda m: None, address="F9:BD:CA:10:F7:A8", log=lambda *a: None)
        objs = self.objects(self.device("F9:BD:CA:10:F7:A8", "TwoNr9", uuids=[self.SVC]))

        async def objects_now():
            return objs

        async def scan(address, timeout):
            raise AssertionError("the scan should not run")
        with mock.patch.object(hudfeed.sys, "platform", "linux"), \
                mock.patch.object(hudfeed, "bluez_objects", objects_now), \
                mock.patch.dict(sys.modules, self.fake_bleak(scan)):
            device = asyncio.run(r._find())
        self.assertEqual("/org/bluez/hci0/dev_F9_BD_CA_10_F7_A8", device.details["path"])
        self.assertEqual("F9:BD:CA:10:F7:A8", device.address)

    def test_without_the_system_bus_the_scan_is_still_the_way_in(self):
        import hudfeed
        r = hudfeed.BleReader(lambda m: None, address="F9:BD:CA:10:F7:A8", log=lambda *a: None)

        async def no_bus():
            raise OSError("no system bus")

        async def scan(address, timeout):
            return "scanned"
        with mock.patch.object(hudfeed.sys, "platform", "linux"), \
                mock.patch.object(hudfeed, "bluez_objects", no_bus), \
                mock.patch.dict(sys.modules, self.fake_bleak(scan)):
            self.assertEqual("scanned", asyncio.run(r._find()))

    @staticmethod
    def fake_dbus(reply=None, connect_error=None):
        """dbus_fast as bluez_objects uses it: a bus that answers `reply` (which dbus_fast returns,
        and does not raise, when it is a D-Bus error) or whose connect fails. -> (modules, calls)."""
        calls = []

        class Bus:
            def __init__(self, bus_type=None):
                calls.append("new")

            async def connect(self):
                calls.append("connect")
                if connect_error:
                    raise connect_error
                return self

            async def call(self, message):
                calls.append("call")
                return reply

            def disconnect(self):
                calls.append("disconnect")
        dbus, aio, constants = (types.ModuleType(n) for n in ("dbus_fast", "dbus_fast.aio", "dbus_fast.constants"))
        dbus.BusType = types.SimpleNamespace(SYSTEM="system")
        dbus.Message = lambda **kw: types.SimpleNamespace(**kw)
        aio.MessageBus = Bus
        constants.MessageType = types.SimpleNamespace(METHOD_RETURN="return", ERROR="error")
        return {"dbus_fast": dbus, "dbus_fast.aio": aio, "dbus_fast.constants": constants}, calls

    def test_the_managed_objects_are_what_a_method_return_carries(self):
        import hudfeed
        objs = self.objects(self.device("F9:BD:CA:10:F7:A8", "TwoNr9", uuids=[self.SVC]))
        modules, calls = self.fake_dbus(types.SimpleNamespace(message_type="return", body=[objs]))
        with mock.patch.dict(sys.modules, modules):
            self.assertIs(objs, asyncio.run(hudfeed.bluez_objects()))
        self.assertEqual(["new", "connect", "call", "disconnect"], calls)

    def test_an_error_reply_is_not_taken_for_managed_objects(self):
        import hudfeed
        error = types.SimpleNamespace(message_type="error", error_name="org.freedesktop.DBus.Error.ServiceUnknown",
                                      body=["The name org.bluez was not provided by any .service files"])
        modules, calls = self.fake_dbus(error)
        with mock.patch.dict(sys.modules, modules):
            with self.assertRaisesRegex(RuntimeError, "ServiceUnknown"):
                asyncio.run(hudfeed.bluez_objects())
        self.assertEqual("disconnect", calls[-1])

    def test_a_bus_whose_connect_failed_is_still_disconnected(self):
        import hudfeed
        modules, calls = self.fake_dbus(connect_error=OSError("auth"))
        with mock.patch.dict(sys.modules, modules):
            with self.assertRaises(OSError):
                asyncio.run(hudfeed.bluez_objects())
        self.assertEqual(["new", "connect", "disconnect"], calls)

    def test_an_error_reply_from_bluez_leaves_the_scan_as_the_way_in(self):
        import hudfeed
        r = hudfeed.BleReader(lambda m: None, address="F9:BD:CA:10:F7:A8", log=lambda *a: None)
        error = types.SimpleNamespace(message_type="error", error_name="org.freedesktop.DBus.Error.ServiceUnknown",
                                      body=["The name org.bluez was not provided by any .service files"])

        async def scan(address, timeout):
            return "scanned"
        modules, _ = self.fake_dbus(error)
        with mock.patch.object(hudfeed.sys, "platform", "linux"), \
                mock.patch.dict(sys.modules, {**modules, **self.fake_bleak(scan)}):
            self.assertEqual("scanned", asyncio.run(r._find()))

    def test_managed_objects_that_cannot_be_read_leave_the_scan_as_the_way_in(self):
        import hudfeed
        r = hudfeed.BleReader(lambda m: None, address="F9:BD:CA:10:F7:A8", log=lambda *a: None)
        no_connected = {"/org/bluez/hci0/dev_X": {"org.bluez.Device1": {"Address": types.SimpleNamespace(value="X")}}}

        async def scan(address, timeout):
            return "scanned"
        for odd in ("not a dict", no_connected):
            async def objects_now():
                return odd
            with mock.patch.object(hudfeed.sys, "platform", "linux"), \
                    mock.patch.object(hudfeed, "bluez_objects", objects_now), \
                    mock.patch.dict(sys.modules, self.fake_bleak(scan)):
                self.assertEqual("scanned", asyncio.run(r._find()), odd)

    def test_with_no_connected_keyboard_and_no_address_the_scan_is_by_service(self):
        import hudfeed
        r = hudfeed.BleReader(lambda m: None, name="twonr", log=lambda *a: None)
        seen = []

        async def objects_now():
            return {}

        async def by_filter(match, timeout):
            seen.append(match)
            return "filtered"
        modules = self.fake_bleak(None)
        modules["bleak"].BleakScanner.find_device_by_filter = by_filter
        with mock.patch.object(hudfeed.sys, "platform", "linux"), \
                mock.patch.object(hudfeed, "bluez_objects", objects_now), \
                mock.patch.dict(sys.modules, modules):
            self.assertEqual("filtered", asyncio.run(r._find()))
        advert = lambda *uuids: types.SimpleNamespace(service_uuids=list(uuids))
        named = lambda name: types.SimpleNamespace(name=name)
        self.assertTrue(seen[0](named("TwoNr9"), advert(self.SVC)))
        self.assertFalse(seen[0](named("TwoNr9"), advert("1812")))
        self.assertFalse(seen[0](named("Other"), advert(self.SVC)))

    def test_nothing_found_on_linux_does_not_send_the_user_to_an_address(self):
        import hudfeed
        said = []
        r = hudfeed.BleReader(lambda m: None, log=said.append, rescan=0.0)

        async def nothing():
            return None

        async def pause(seconds):
            r.stop()
        r._find, r._pause = nothing, pause
        with mock.patch.object(hudfeed.sys, "platform", "linux"), \
                mock.patch.dict(sys.modules, {"bleak": types.SimpleNamespace(BleakClient=None)}):
            asyncio.run(r._loop())
        self.assertEqual(["hudfeed: no BLE keyboard with the layer signal connected or advertising yet"], said)

    def test_what_bluez_had_connected_is_remembered_and_what_the_scan_found_is_not(self):
        import hudfeed
        r = hudfeed.BleReader(lambda m: None, address="F9:BD:CA:10:F7:A8", log=lambda *a: None)
        answers = [self.objects(self.device("F9:BD:CA:10:F7:A8", "TwoNr9", uuids=[self.SVC])), {}]

        async def objects_now():
            return answers.pop(0)

        async def scan(address, timeout):
            return "scanned"
        with mock.patch.object(hudfeed.sys, "platform", "linux"), \
                mock.patch.object(hudfeed, "bluez_objects", objects_now), \
                mock.patch.dict(sys.modules, self.fake_bleak(scan)):
            device = asyncio.run(r._find())
            self.assertIs(device, r._found_connected)
            self.assertEqual("scanned", asyncio.run(r._find()))
            self.assertIsNone(r._found_connected)


class BleLeftConnected(unittest.TestCase):
    """A keyboard the BlueZ lookup found is left connected when the feed is done with it; one the
    scan handed over is disconnected, as before, whoever connected it. The client here stands in
    for bleak's BlueZ one as BleReader._let_go reads it: closing it sends Device1.Disconnect while
    it holds the device connected. BleakBluez, below, holds that reading to bleak itself."""

    def setUp(self):
        import hudfeed
        self.said, self.sent, self.backends = [], [], []
        self.reader = hudfeed.BleReader(lambda m: None, log=self.said.append)
        self.reader.stop()                      # a session that is read and ends at once
        self.device = types.SimpleNamespace(name="TwoNr9", address="F9:BD:CA:10:F7:A8")

    def client(self, notify_error=None, members=True, drops=False):
        sent, backends = self.sent, self.backends

        class Backend:
            def __init__(self):
                self._is_connected = True
                if members:
                    self._disconnect_monitor_event = self.monitor = asyncio.Event()

            def _cleanup_all(self):
                sent.append("cleaned up")

        class Client:
            def __init__(self, device, disconnected_callback):
                self._backend = Backend()
                backends.append(self._backend)

            @property
            def is_connected(self):
                return self._backend._is_connected

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                if self._backend._is_connected:
                    sent.append("Disconnect")
                sent.append("closed")
                return False

            async def start_notify(self, uuid, callback):
                if notify_error:
                    raise notify_error
                sent.append("StartNotify")
                if drops:
                    self._backend._is_connected = False

            async def stop_notify(self, uuid):
                sent.append("StopNotify")
        return Client

    def test_a_keyboard_found_connected_is_left_connected_when_the_feed_stops(self):
        self.reader._found_connected = self.device
        self.assertIsNone(asyncio.run(self.reader._session(self.client(), self.device)))
        self.assertEqual(["StartNotify", "StopNotify", "cleaned up", "closed"], self.sent)
        self.assertEqual(["hudfeed: reading TwoNr9 over BLE"], self.said)
        # ... and what would disconnect it when the event loop ends has been told it is over
        self.assertTrue(self.backends[0].monitor.is_set())
        self.assertIsNone(self.backends[0]._disconnect_monitor_event)

    def test_one_the_scan_handed_over_is_disconnected_as_before(self):
        self.reader._found_connected = types.SimpleNamespace(name="Other", address="AA:AA:AA:AA:AA:01")
        self.assertIsNone(asyncio.run(self.reader._session(self.client(), self.device)))
        self.assertEqual(["StartNotify", "Disconnect", "closed"], self.sent)

    def test_one_found_connected_that_cannot_be_read_is_left_connected_too(self):
        self.reader._found_connected = self.device
        failed = asyncio.run(self.reader._session(self.client(notify_error=OSError("no such characteristic")),
                                                  self.device))
        self.assertEqual("TwoNr9 over BLE, not read (OSError: no such characteristic)", failed)
        self.assertNotIn("Disconnect", self.sent)
        self.assertEqual(["cleaned up", "closed"], self.sent[-2:])

    def test_one_that_went_by_itself_is_closed_as_bleak_closes_it(self):
        self.reader._found_connected = self.device
        asyncio.run(self.reader._session(self.client(drops=True), self.device))
        self.assertEqual(["StartNotify", "closed"], self.sent)

    def test_a_bleak_without_what_this_reads_disconnects_as_before_and_says_so_once(self):
        self.reader._found_connected = self.device
        for _ in range(2):
            asyncio.run(self.reader._session(self.client(members=False), self.device))
        self.assertEqual(["StartNotify", "Disconnect", "closed"] * 2, self.sent)
        self.assertEqual(1, len([s for s in self.said if "cannot leave TwoNr9 connected" in s]), self.said)


class SimBluez:
    """BlueZ on the system bus, as far as bleak's BlueZ client and manager and hudfeed's own lookup
    talk to it: an adapter and one connected keyboard with the signal characteristic, every method
    call recorded by its member, a Disconnect answered with the property change BlueZ signals. It
    comes as the dbus_fast modules those import (`modules`), so there is no D-Bus and no dbus_fast
    in it, and what runs on it is the same on every system.

    It is bleak's Python that is run, not dbus_fast and not BlueZ: what a real bus or a real
    keyboard does differently is not seen here."""

    ADAPTER = "/org/bluez/hci0"
    DEVICE = ADAPTER + "/dev_F9_BD_CA_10_F7_A8"
    SERVICE = DEVICE + "/service000a"
    SIGNAL = SERVICE + "/char000b"
    DBUS_FAST = ("dbus_fast", "dbus_fast.aio", "dbus_fast.aio.message_bus", "dbus_fast.auth", "dbus_fast.constants",
                 "dbus_fast.errors", "dbus_fast.message", "dbus_fast.service", "dbus_fast.signature",
                 "dbus_fast.validators")

    def __init__(self):
        import hudfeed
        sim = self
        self.calls, self.buses = [], []
        self.objects = {
            self.ADAPTER: {"org.bluez.Adapter1": {"Address": "00:1A:7D:DA:71:13", "Powered": True, "Roles": ["central"]}},
            self.DEVICE: {"org.bluez.Device1": {
                "Address": "F9:BD:CA:10:F7:A8", "Name": "TwoNr9", "Alias": "TwoNr9", "Adapter": self.ADAPTER,
                "Connected": True, "Paired": True, "ServicesResolved": True, "UUIDs": [hudfeed.BLE_SERVICE_UUID]}},
            self.SERVICE: {"org.bluez.GattService1": {
                "UUID": hudfeed.BLE_SERVICE_UUID, "Device": self.DEVICE, "Primary": True}},
            self.SIGNAL: {"org.bluez.GattCharacteristic1": {
                "UUID": hudfeed.BLE_SIGNAL_UUID, "Service": self.SERVICE, "Flags": ["notify"], "Value": b""}},
        }

        class Variant:
            def __init__(self, signature, value):
                self.signature, self.value = signature, value

        def unpack_variants(value):
            if isinstance(value, Variant):
                return unpack_variants(value.value)
            if isinstance(value, dict):
                return {k: unpack_variants(v) for k, v in value.items()}
            return [unpack_variants(v) for v in value] if isinstance(value, list) else value

        class Message:
            def __init__(self, message_type="call", **fields):
                self.message_type, self.error_name, self.body, self.unix_fds = message_type, None, [], []
                self.destination = self.path = self.interface = self.member = self.signature = None
                self.__dict__.update(fields)

        class MessageBus:
            def __init__(self, bus_type=None, **kw):
                self.connected, self.handlers = False, []
                sim.buses.append(self)

            async def connect(self):
                self.connected = True
                return self

            def add_message_handler(self, handler):
                self.handlers.append(handler)

            async def call(self, message):
                return sim.answer(message)

            async def send(self, message):
                sim.answer(message)

            def disconnect(self):
                self.connected = False

            async def wait_for_disconnect(self):
                pass

            def _finalize(self, err):   # what bleak's manager does to the bus of an event loop that is over
                self.connected = False
        self.Variant, self.Message = Variant, Message
        kinds = types.SimpleNamespace(METHOD_CALL="call", METHOD_RETURN="return", ERROR="error", SIGNAL="signal")
        buses = types.SimpleNamespace(SYSTEM="system")
        m = self.modules = {name: mock.MagicMock(unsafe=True) for name in self.DBUS_FAST}
        m["dbus_fast"].AuthError = type("AuthError", (Exception,), {})
        m["dbus_fast"].unpack_variants = unpack_variants
        m["dbus_fast"].Variant = m["dbus_fast.signature"].Variant = Variant
        m["dbus_fast"].Message = m["dbus_fast.message"].Message = Message
        m["dbus_fast"].MessageType = m["dbus_fast.constants"].MessageType = kinds
        m["dbus_fast"].BusType = m["dbus_fast.constants"].BusType = buses
        m["dbus_fast.constants"].ErrorType = types.SimpleNamespace(
            UNKNOWN_OBJECT=types.SimpleNamespace(value="org.freedesktop.DBus.Error.UnknownObject"))
        m["dbus_fast.errors"].InvalidObjectPathError = type("InvalidObjectPathError", (Exception,), {})
        m["dbus_fast.aio"].MessageBus = m["dbus_fast.aio.message_bus"].MessageBus = MessageBus

    @property
    def keyboard(self):
        return self.objects[self.DEVICE]["org.bluez.Device1"]

    def answer(self, message):
        self.calls.append(message.member)
        body = []
        if message.member == "GetManagedObjects":
            body = [{path: {interface: {k: self.Variant("v", v) for k, v in props.items()}
                            for interface, props in interfaces.items()}
                     for path, interfaces in self.objects.items()}]
        elif message.member == "Disconnect" and message.path == self.DEVICE:
            self.change(Connected=False, ServicesResolved=False)
        elif message.member == "Connect" and message.path == self.DEVICE:
            self.change(Connected=True, ServicesResolved=True)
        return self.Message("return", body=body)

    def change(self, **props):
        """The keyboard's properties change, and every bus that listens is told, as BlueZ tells it."""
        self.keyboard.update(props)
        signal = self.Message("signal", path=self.DEVICE, interface="org.freedesktop.DBus.Properties",
                              member="PropertiesChanged",
                              body=["org.bluez.Device1", {k: self.Variant("v", v) for k, v in props.items()}, []])
        for bus in self.buses:
            for handler in list(bus.handlers) if bus.connected else ():
                handler(signal)

    def asked(self):
        """What the keyboard itself was asked for, in order: the calls that reach it or its link."""
        return [c for c in self.calls if c in ("Connect", "Disconnect", "StartNotify", "StopNotify")]


class BleakBluez(unittest.TestCase):
    """What BleReader._let_go relies on, held to the bleak that is installed. It is bleak's own
    BleakClient on its own BlueZ client and manager that run here, through their real connect(),
    start_notify, stop_notify and disconnect(), on a BlueZ that is simulated (SimBluez): _let_go
    reaches into bleak's private members, so a bump of bleak that changes what they do fails here
    and not on someone's keyboard.

    bleak's BlueZ scanner runs here too, wherever the feed scans, in place of the platform's.

    Not run: bleak's check of BlueZ's version (it starts `bluetoothctl`), dbus_fast, BlueZ, a
    keyboard."""

    def setUp(self):
        import importlib.util
        if importlib.util.find_spec("bleak") is None:
            self.skipTest("bleak is not installed")
        import hudfeed
        self.sim = SimBluez()
        patch = mock.patch.dict(sys.modules, self.sim.modules)    # all of it is put back after the test
        patch.start()
        self.addCleanup(patch.stop)
        for name in [n for n in sys.modules if n.startswith("bleak.backends.bluezdbus")]:
            del sys.modules[name]       # bleak's BlueZ modules are imported anew, on the stand-in
        import bleak
        from bleak.backends.bluezdbus.client import BleakClientBlueZDBus
        from bleak.backends.bluezdbus.scanner import BleakScannerBlueZDBus
        from bleak.backends.bluezdbus.version import BlueZFeatures
        from bleak.backends.device import BLEDevice
        BlueZFeatures.checked_bluez_version = True
        # The feed's own scan (BleakScanner, as _find calls it) is bleak's BlueZ scanner on this BlueZ
        # whatever system the tests run on: never CoreBluetooth, never a real adapter.
        scanner = mock.patch.object(bleak, "get_platform_scanner_backend_type",
                                    lambda: (BleakScannerBlueZDBus, "bluez_dbus"))
        scanner.start()
        self.addCleanup(scanner.stop)
        self.BleakClient = functools.partial(bleak.BleakClient, backend=BleakClientBlueZDBus)
        # As bleak's scanner hands a device over: by its path, with what BlueZ said of it.
        self.scanned = BLEDevice(self.sim.keyboard["Address"], "TwoNr9",
                                 {"path": self.sim.DEVICE, "props": dict(self.sim.keyboard)})
        self.said = []
        self.reader = hudfeed.BleReader(lambda m: None, log=self.said.append)
        self.reader.stop()              # a session that is read and ends at once

    def session(self, found_connected):
        """One session of the feed's reader on the keyboard: looked up in BlueZ as on Linux, or handed
        over as a scan hands it. -> what _session returned."""
        import hudfeed

        async def run():
            device = self.scanned
            if found_connected:
                with mock.patch.object(hudfeed.sys, "platform", "linux"):
                    device = await self.reader._find()
                self.assertIs(device, self.reader._found_connected)
            return await self.reader._session(self.BleakClient, device)
        return asyncio.run(run())       # and the event loop ends, which cancels what bleak left running

    def test_bleak_disconnects_a_keyboard_it_did_not_connect_when_its_client_closes(self):
        import hudfeed

        async def use():
            async with self.BleakClient(self.scanned) as client:
                await client.start_notify(hudfeed.BLE_SIGNAL_UUID, lambda *a: None)
        asyncio.run(use())
        self.assertEqual(["StartNotify", "Disconnect"], self.sim.asked())      # no Connect: it was connected
        self.assertFalse(self.sim.keyboard["Connected"])

    def test_and_when_the_event_loop_ends_with_its_client_never_closed(self):
        async def leave():
            await self.BleakClient(self.scanned).connect()
        asyncio.run(leave())
        self.assertEqual(["Disconnect"], self.sim.asked())

    def test_and_when_its_connect_gives_up_on_services_that_do_not_resolve_or_are_not_said(self):
        # Why bluez_matches wants ServicesResolved: this is the keyboard it would otherwise hand over.
        import bleak.exc
        for resolved, error in ((False, asyncio.TimeoutError), (None, bleak.exc.BleakError)):
            self.sim.calls.clear()
            self.sim.keyboard.update(Connected=True, ServicesResolved=resolved)
            if resolved is None:
                del self.sim.keyboard["ServicesResolved"]

            async def connect():
                await self.BleakClient(self.scanned, timeout=0.05).connect()
            with self.assertRaises(error):
                asyncio.run(connect())
            self.assertEqual(["Disconnect"], self.sim.asked(), resolved)     # no Connect: it was connected
            self.assertFalse(self.sim.keyboard["Connected"])

    def test_while_bluez_resolves_a_keyboard_the_feed_asks_nothing_of_it_and_does_not_scan(self):
        import hudfeed
        self.sim.keyboard["ServicesResolved"] = False
        with mock.patch.object(hudfeed.sys, "platform", "linux"):
            self.assertIsNone(asyncio.run(self.reader._find()))
        self.assertEqual(["GetManagedObjects"], self.sim.calls)                 # and no StartDiscovery
        self.assertTrue(self.sim.keyboard["Connected"])

    def test_a_session_on_a_keyboard_found_connected_stops_notifying_and_leaves_it_connected(self):
        self.assertIsNone(self.session(found_connected=True))
        self.assertEqual(["StartNotify", "StopNotify"], self.sim.asked())
        self.assertTrue(self.sim.keyboard["Connected"])
        self.assertEqual(["hudfeed: reading TwoNr9 over BLE"], self.said)      # bleak has what _let_go reads
        # the client's own bus and the lookup's are closed: the one left open is bleak's manager's
        self.assertEqual(1, sum(bus.connected for bus in self.sim.buses), len(self.sim.buses))

    def test_a_session_that_cannot_read_a_keyboard_found_connected_leaves_it_connected_too(self):
        del self.sim.objects[self.sim.SIGNAL]
        self.assertIn("TwoNr9 over BLE, not read (BleakCharacteristicNotFoundError", self.session(found_connected=True))
        self.assertEqual([], self.sim.asked())
        self.assertTrue(self.sim.keyboard["Connected"])

    def test_a_session_on_a_keyboard_the_scan_handed_over_ends_with_bleaks_disconnect(self):
        self.assertIsNone(self.session(found_connected=False))
        self.assertEqual(["StartNotify", "Disconnect"], self.sim.asked())
        self.assertFalse(self.sim.keyboard["Connected"])

    def test_a_stuck_keyboard_resolved_while_the_scan_runs_is_not_handed_over_by_it(self):
        # Past BLE_RESOLVE_WAIT_S the scan runs again; bleak's scanner reports the connected keyboard as
        # soon as BlueZ says something new of it, which here is that it is resolved at last.
        import hudfeed
        self.sim.keyboard["ServicesResolved"] = False
        said = []
        r = hudfeed.BleReader(lambda m: None, log=said.append, rescan=0.3)
        r._clock = lambda: now
        seen = []

        async def look():
            loop = asyncio.get_running_loop()
            if now:
                loop.call_later(0.05, lambda: self.sim.change(ServicesResolved=True))
            with mock.patch.object(hudfeed.sys, "platform", "linux"):
                device = await r._find()
            seen.append(device is not None and device is r._found_connected)
            return device
        for now in (0, hudfeed.BLE_RESOLVE_WAIT_S + 1):
            self.assertIsNone(asyncio.run(look()), now)
        self.assertIn("StartDiscovery", self.sim.calls)                      # the scan did run, and passed it over
        now = hudfeed.BLE_RESOLVE_WAIT_S + 2
        self.assertEqual(self.sim.keyboard["Address"], asyncio.run(look()).address)
        self.assertEqual([False, False, True], seen)                         # ... and the lookup took it
        self.assertEqual([], self.sim.asked())
        self.assertEqual(1, len([s for s in said if "scanning for another keyboard too" in s]), said)

    def test_a_name_a_keyboard_advertises_reaches_the_log_and_the_pages_printable(self):
        # Found by bleak's own scanner, which names it after BlueZ's Alias: the name is the device's.
        import hudfeed
        forged = "TwoNr9\nhudfeed: forged line\x1b[2K"
        self.sim.keyboard.update(Connected=False, ServicesResolved=False, Name=forged, Alias=forged)
        for picked in ({}, {"name": "twonr9\nhudfeed"}):      # a configured name is matched as it came
            self.sim.keyboard.update(Connected=False, ServicesResolved=False)
            said, out = [], []
            r = hudfeed.BleReader(out.append, log=said.append, rescan=1.0, **picked)
            r.stop()                            # a session that is read and ends at once

            async def scan_and_read():
                asyncio.get_running_loop().call_later(0.05, lambda: self.sim.change(RSSI=-50))
                with mock.patch.object(hudfeed.sys, "platform", "linux"):
                    device = await r._find()
                self.assertEqual(forged, device.name)               # as the scan gave it
                self.assertIsNot(device, r._found_connected)
                return await r._session(self.BleakClient, device)
            self.assertIsNone(asyncio.run(scan_and_read()), picked)
            self.assertEqual(["hudfeed: reading TwoNr9?hudfeed: forged line?[2K over BLE"], said, picked)
            self.assertEqual({"TwoNr9?hudfeed: forged line?[2K"}, {m["device"] for m in out if "device" in m}
                             | {m["name"] for m in out if m.get("kind") == "device"})


class HidKeys(unittest.TestCase):
    """The HID keys reader (hidapi, macOS) with a hid module that answers as the test says."""

    def module(self, opens=True):
        dev = mock.Mock()
        if not opens:
            dev.open_path.side_effect = OSError("open failed")
        dev.read.side_effect = [[1, 0, 0, 0x04, 0, 0, 0, 0, 0], []]
        return types.SimpleNamespace(device=lambda: dev, __file__="/nowhere/hid.so",
                                     enumerate=lambda vid, pid: [{"path": b"DevSrvsID:1", "product_string": "Diamond"},
                                                                 {"path": b"DevSrvsID:1", "product_string": "Diamond"},
                                                                 {"path": b"DevSrvsID:2", "product_string": "Other"}]), dev

    def test_one_path_per_keyboard_its_name_matched(self):
        import hudfeed
        r = hudfeed.HidKeysReader(lambda m: None, name="diamond", log=lambda *a: None)
        r.hid, _ = self.module()
        self.assertEqual([(b"DevSrvsID:1", "Diamond")], r.find())

    def test_a_report_is_what_is_typed(self):
        import hudfeed
        r = hudfeed.HidKeysReader(lambda m: None, log=lambda *a: None)
        r.hid, dev = self.module()
        conn = r.open(b"DevSrvsID:1", "Diamond")
        self.assertEqual({"kind": "keys", "mods": 0, "keys": [0x04, 0, 0, 0, 0, 0]}, r.read(conn, 0.5))
        self.assertIsNone(r.read(conn, 0.5))
        dev.read.assert_called_with(64, timeout_ms=500)

    def test_a_keyboard_it_may_not_open_says_where_and_why(self):
        import hudfeed
        r = hudfeed.HidKeysReader(lambda m: None, log=lambda *a: None)
        r.hid, _ = self.module(opens=False)
        with mock.patch.object(hudfeed.sys, "platform", "darwin"):
            with self.assertRaises(hudfeed.OpenFailed) as e:
                r.open(b"DevSrvsID:1", "Diamond")
        self.assertIn("(DevSrvsID:1)", e.exception.lines[0])
        self.assertIn("Input Monitoring", e.exception.lines[1])


class HidrawReading(unittest.TestCase):
    """The hidraw reader's loop on a pipe: what /dev/hidrawN is to it, one report per read."""

    def setUp(self):
        import hudfeed
        self.out, self.said = [], []
        self.reader = hudfeed.HidrawReader(self.out.append, log=self.said.append, dead_key_ms=20)
        self.rfd, self.wfd = os.pipe()
        self.addCleanup(lambda: [os.close(fd) for fd in (self.wfd,) if fd is not None])

    def test_an_error_reading_ends_the_connection_and_is_said(self):
        # OSError is the unplug; anything else the read raises must end the connection the same way
        # -- said, the node closed and forgotten so a replug opens it -- not as a thread's traceback.
        import hudfeed
        os.write(self.wfd, bytes([1, 0x02, 0, 0x04, 0, 0, 0, 0, 0]))
        self.reader._open["/dev/hidraw9"] = self.rfd
        with mock.patch.object(hudfeed, "split_report", side_effect=ValueError("a report it cannot take")):
            self.reader._read_loop("/dev/hidraw9", self.rfd, "Diamond")
        self.assertTrue(any("Diamond gone" in s for s in self.said), self.said)
        self.assertNotIn("/dev/hidraw9", self.reader._open)


@unittest.skipUnless(HAVE_YAML, "reading a config needs PyYAML or yq")
class ReaderSettings(unittest.TestCase):
    """Which keyboard to read, on which port, and with which timings is the config's own business.
    A keymap that does not convert -- a drawer file that moved, a config with no `keymap:` yet --
    must not also send the feed to the default keyboard, or leave it without its port, while the
    keymap is being fixed."""

    SETTINGS = ("keyboard: {name: Test Board, vid: 4660, pid: 22136}\n"
                "serial: {port: /dev/cu.test, probe_s: 3}\n"
                "ble: {enabled: false}\n"
                "hud: {width: 700}\n"
                "feed: {rescan_s: 7, secure_input: 0}\n")

    def feed_for(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.yaml")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            said = []
            # Readers are built, never started: nothing here opens a device.
            return Feed(lambda msg: None, log=said.append, config=path, keys=False, sessions=False), said

    def check(self, feed, said):
        r = feed.reader
        self.assertEqual((r.name, r.vid, r.pid, r.port), ("Test Board", 4660, 22136, "/dev/cu.test"))
        self.assertEqual((r.probe_s, r.rescan), (3.0, 7.0))
        self.assertIsNone(feed.ble)                       # ble: {enabled: false}
        self.assertIsNone(feed.secure)                    # feed.secure_input: 0
        self.assertEqual(feed.cfg["hud"]["width"], 700)   # the macOS panel sizes itself from this
        self.assertTrue(any(s.startswith("hudfeed:") for s in said), said)   # and it says why there is no keymap

    def test_a_keymap_that_does_not_convert(self):
        self.check(*self.feed_for("keymap: /nowhere/keymap-drawer.yaml\n" + self.SETTINGS))

    def test_no_keys_sends_layers_only(self):
        # Neither the keys typed nor the positions pressed: with the keymap, the positions are the
        # typing too. --no-hid-keys is the flag that drops just the strip and keeps them.
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.yaml")
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.SETTINGS)
            out = []
            feed = Feed(out.append, log=lambda *a: None, config=path, keys=False, sessions=False)
        self.assertIsNone(feed.hid)
        for msg in ({"kind": "press", "pos": 3, "device": "T"}, {"kind": "release", "pos": 3, "device": "T"},
                    {"kind": "key", "type": "keyDown", "chars": "a", "device": "T"},
                    {"kind": "layers", "ids": [2], "device": "T"}, {"kind": "device", "name": "T"}):
            feed._emit(msg)
        self.assertEqual(["layers", "device"], [m["kind"] for m in out])

    def test_a_config_with_no_keymap_yet(self):
        self.check(*self.feed_for(self.SETTINGS))

    def test_default_right_alt_decoding_matches_the_host(self):
        feed, _ = self.feed_for(self.SETTINGS)
        decoder = SignalDecoder(altgr=feed.reader.altgr)
        typed = [m["chars"] for m in decoder.feed({"kind": "keys", "mods": 0x40, "keys": [0x36]}, 0) if m["type"] == "keyDown"]
        self.assertEqual(["ç" if sys.platform.startswith("linux") else "≤"], typed)

    def test_explicit_right_alt_decoding_wins_over_the_host_default(self):
        for altgr, expected in ((0, "≤"), (1, "ç")):
            with self.subTest(altgr=altgr):
                feed, _ = self.feed_for(self.SETTINGS.replace("rescan_s: 7,", f"rescan_s: 7, altgr: {altgr},"))
                decoder = SignalDecoder(altgr=feed.reader.altgr)
                typed = [m["chars"] for m in decoder.feed({"kind": "keys", "mods": 0x40, "keys": [0x36]}, 0) if m["type"] == "keyDown"]
                self.assertEqual([expected], typed)


if __name__ == "__main__":
    unittest.main()
