"""Tests for host/hudpoke.py: what `zmk-layer-hud poke` puts on the socket.

Only the messages; the socket itself is websockets' business, and the pages' reading of these
messages is hud/tests/words_test.js's.
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hudpoke  # noqa: E402
import panelstate  # noqa: E402


def sent(argv):
    args = hudpoke.parse_args(argv)
    return [msg for _, msg in hudpoke.says_combos(hudpoke.messages(args), args.combos)]


class Url(unittest.TestCase):
    """Where `poke` sends: the running feed's socket, with the token the feed keeps in $STATE/token."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"ZMKHUD_STATE": self.tmp.name}, clear=False)
        self.env.start()
        for name in ("ZMKHUD_TOKEN", "ZMKHUD_PORT"):
            os.environ.pop(name, None)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def url(self, *argv):
        return hudpoke.feed_url(hudpoke.parse_args([*argv, "--layers", "1"]))

    def test_the_running_feeds_token_is_read_from_the_state_directory(self):
        panelstate.write_token(self.tmp.name, "abc123")
        self.assertEqual("ws://127.0.0.1:8766/abc123", self.url())
        os.environ["ZMKHUD_PORT"] = "9000"
        self.assertEqual("ws://127.0.0.1:9000/abc123", self.url())

    def test_the_environment_and_url_come_first(self):
        panelstate.write_token(self.tmp.name, "abc123")
        os.environ["ZMKHUD_TOKEN"] = "fromenv"
        self.assertEqual("ws://127.0.0.1:8766/fromenv", self.url())
        self.assertEqual("ws://127.0.0.1:8767/demo", self.url("--url", "ws://127.0.0.1:8767/demo"))

    def test_with_no_running_feed_it_says_where_the_token_would_be(self):
        err = io.StringIO()
        with self.assertRaises(SystemExit) as stop, contextlib.redirect_stderr(err):
            self.url()
        self.assertIn(os.path.join(self.tmp.name, "token"), str(stop.exception))
        self.assertIn("--url", str(stop.exception))

    def test_the_token_is_kept_out_of_what_is_said_back(self):
        self.assertEqual("ws://127.0.0.1:8766/<token>", hudpoke.shown("ws://127.0.0.1:8766/abc123"))
        self.assertEqual("ws://127.0.0.1:8766", hudpoke.shown("ws://127.0.0.1:8766"))


class Combos(unittest.TestCase):
    def test_typing_says_no_combos_by_default(self):
        keys = [m for m in sent(["--type", "zebra"]) if m["kind"] == "key"]
        self.assertTrue(keys)
        self.assertTrue(all(m["combos"] is False for m in keys))

    def test_combos_says_so_on_every_key(self):
        keys = [m for m in sent(["--type", "zebra", "--combos"]) if m["kind"] == "key"]
        self.assertTrue(keys)
        self.assertTrue(all(m["combos"] is True for m in keys))

    def test_a_legend_is_typing_too(self):
        keys = [m for m in sent(["--legend", "á"]) if m["kind"] == "key"]
        self.assertTrue(keys)
        self.assertTrue(all(m["combos"] is False for m in keys))

    def test_only_keys_carry_it(self):
        for m in sent(["--layers", "13", "--press", "16", "--combos"]):
            self.assertNotIn("combos", m)

    def test_a_raw_message_that_says_keeps_its_word(self):
        raw = [(0, {"kind": "key", "type": "keyDown", "chars": "z", "combos": True})]
        self.assertIs(next(hudpoke.says_combos(iter(raw), False))[1]["combos"], True)


class Play(unittest.TestCase):
    def test_a_script_is_something_to_send(self):
        args = hudpoke.parse_args(["--play", "docs/demo-type.json", "--loop", "--speed", "2"])
        self.assertEqual(("docs/demo-type.json", True, 2.0), (args.play, args.loop, args.speed))

    def test_a_script_plays_alone(self):
        with self.assertRaises(SystemExit):
            hudpoke.parse_args(["--play", "docs/demo-type.json", "--type", "hi"])

    def test_the_keymap_is_the_one_the_feed_replays(self):
        import asyncio
        import json

        class Replay:
            def __init__(self, *msgs):
                self.msgs = [json.dumps(m) for m in msgs]

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self.msgs:
                    raise StopAsyncIteration
                return self.msgs.pop(0)
        ws = Replay({"kind": "layers", "ids": []}, {"kind": "device", "name": "x"}, {"kind": "keymap", "base": "b"})
        self.assertEqual("b", asyncio.run(hudpoke.replayed_keymap(ws, 1.0))["base"])
        self.assertIsNone(asyncio.run(hudpoke.replayed_keymap(Replay({"kind": "layers", "ids": []}), 1.0)))


if __name__ == "__main__":
    unittest.main()
