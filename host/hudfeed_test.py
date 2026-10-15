"""Tests for host/hudfeed.py: the readers, the stream, the socket and what watches for secrets.

The wire format itself is host/signal_frame.py's business and is tested in signal_frame_test.py;
what a report means is host/hiddecode.py's, tested in hiddecode_test.py. Stream ties the two together.
"""

import asyncio
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

    def test_closing_releases_what_was_held(self):
        self.stream.message({"kind": "keys", "mods": 0, "keys": [0x04]}, 0)
        self.out.clear()
        self.stream.close()
        self.assertEqual([(m["type"], m["chars"], m["device"]) for m in self.out],
                         [("keyUp", "a", "Diamond")])

    def test_closing_lets_a_held_modifier_go(self):
        # Shift held as the keyboard was unplugged: the page draws it held until it is told otherwise.
        self.stream.message({"kind": "keys", "mods": 0x02, "keys": []}, 0)
        self.out.clear()
        self.stream.close()
        self.assertEqual([("flagsChanged", False)], [(m["type"], m["flags"]["shift"]) for m in self.out])

    def test_closing_types_a_dead_key_that_was_waiting_for_its_letter(self):
        # ' held back for the letter that would make it an accent: the keyboard went away instead,
        # so it is the plain key it was -- down, then up -- never an up with no down.
        self.stream.message({"kind": "keys", "mods": 0, "keys": [0x34]}, 0)
        self.assertEqual([], self.out)
        self.stream.close()
        self.assertEqual([("keyDown", "'"), ("keyUp", "'")], [(m["type"], m["chars"]) for m in self.out])


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

    def test_the_feed_asks_the_system_and_the_terminals(self):
        # Either saying so is enough; and where neither can be asked, there is nothing to hold back.
        said = {"system": False, "terminal": False, "lock": False}
        probes = {"system": lambda: said["system"], "terminal": lambda: said["terminal"], "lock": lambda: said["lock"]}

        def feed_with(**have):
            # No accessibility bus: the next test has it. A Linux machine with one (CI's) would
            # otherwise always have something to ask.
            with mock.patch("hudfeed.carbon_probe", return_value=probes["system"] if have.get("system") else None), \
                 mock.patch("hudfeed.terminal_probe", return_value=probes["terminal"] if have.get("terminal") else None), \
                 mock.patch("hudfeed.a11y.available", return_value=False), \
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
        # Nothing watching is said when it starts, in the log and to the page, so nobody trusts it. No
        # accessibility bus either, as above: a Linux machine with one (CI's) would have it watching.
        told, logged = [], []
        with mock.patch("hudfeed.carbon_probe", return_value=None), mock.patch("hudfeed.terminal_probe", return_value=None), \
             mock.patch("hudfeed.a11y.available", return_value=False), \
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


if __name__ == "__main__":
    unittest.main()
