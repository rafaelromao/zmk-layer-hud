"""Tests for host/play.py: a demo script, turned into what a keyboard would send, and when.

The expectations are the keymap's, through host/ways.py, and the committed scripts'; what is
checked of the timing is what the page needs of it -- a chord inside the combo term, keystrokes
never close enough to be one, a key let go before it goes down again."""

import asyncio
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import play  # noqa: E402
import ways  # noqa: E402
from hudfeed import INJECTABLE  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def fixture(name):
    with open(os.path.join(ROOT, "hud", "tests", "fixtures", name), encoding="utf-8") as f:
        return json.load(f)


DIAMOND = fixture("diamond.json")
SMALL = fixture("example-3x5.json")      # config/example-3x5.yaml: capitals drawn, words for keys


def script(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return json.load(f)


def compiled(steps, keymap=DIAMOND, speed=1.0, **top):
    return play.compile(dict({"steps": steps}, **top), keymap, speed=speed)


def of(c, kind):
    return [m for _, m in c.timeline if m["kind"] == kind]


def typed(c):
    return "".join(m["chars"] for m in of(c, "key") if m["type"] == "keyDown")


def absolute(c):
    t, out = 0, []
    for wait, msg in c.timeline:
        t += wait
        out.append((t, msg))
    return out


def floor(keymap):
    return (keymap.get("combo_term") or 50) + (keymap.get("hud") or {}).get("combo_slack_ms", 20) + 20


class Spelling(unittest.TestCase):
    def test_a_word_is_struck_where_the_keymap_types_it(self):
        first = {}
        for case in ways.cases(ways.Keymap(DIAMOND)):
            first.setdefault(case["legend"], case)
        want = [part["zmk"] for part in ways.spell("hello", first)]
        c = compiled([{"type": "hello"}])
        self.assertEqual(want, [s["strike"] for s in c.strokes])
        self.assertEqual("hello", typed(c))

    def test_a_space_in_the_text_is_the_space_key(self):
        c = compiled([{"type": "a b"}])
        self.assertIn("space", [m["name"] for m in of(c, "key") if m["type"] == "keyDown"])
        self.assertEqual(3, len(c.strokes))

    def test_a_letter_a_combo_also_types_is_its_key_unless_combos_are_asked_for(self):
        # z on the Diamond: Alpha 2's key, reached by tapping the Alpha 2 key -- or the r+a chord.
        alpha2 = next(int(i) for i, z in DIAMOND["zmk_layers"].items() if z and z["drawer"] == "alpha2")
        keys = compiled([{"type": "z"}])
        self.assertEqual([[alpha2], []], [m["ids"] for m in of(keys, "layers")][1:])
        self.assertEqual(1, len(keys.strokes[0]["strike"]))
        chord = compiled([{"type": "z", "combos": True}])
        self.assertEqual(2, len(chord.strokes[0]["strike"]))
        self.assertTrue(chord.strokes[0]["combo"])
        self.assertEqual([[]], [m["ids"] for m in of(chord, "layers")])

    def test_a_character_only_a_combo_types_is_that_combo(self):
        by = {}
        for case in ways.cases(ways.Keymap(DIAMOND)):
            by.setdefault(case["legend"], []).append(case)
        only = next(l for l, cs in by.items() if len(l) == 1 and all(c["combo"] for c in cs) and l.isprintable() and l != " ")
        c = compiled([{"type": only}])
        self.assertTrue(c.strokes and c.strokes[0]["combo"], only)

    def test_a_capital_is_shift_held_and_its_key(self):
        c = compiled([{"type": "H"}])
        s = c.strokes[0]
        self.assertEqual(2, len(s["down"]))           # the Shift home-row key, and the H
        times = absolute(c)
        shift = next(t for t, m in times if m["kind"] == "press" and m["pos"] not in s["strike"])
        key = next(t for t, m in times if m["kind"] == "press" and m["pos"] in s["strike"])
        self.assertGreaterEqual(key - shift, floor(DIAMOND))
        self.assertEqual("H", typed(c))

    def test_a_macro_legend_is_one_keystroke(self):
        self.assertEqual(3, len(compiled([{"type": "ação"}]).strokes))     # a, ç, ão

    def test_a_character_no_key_types_is_skipped_and_said(self):
        c = compiled([{"type": "h☃llo"}])
        self.assertEqual([(1, "☃")], [(p["at"], p["char"]) for p in c.problems if p["level"] == "skip"])
        self.assertEqual("hllo", typed(c))

    def test_a_board_that_draws_capitals_types_lowercase_on_them(self):
        c = compiled([{"type": "hello"}], SMALL)
        self.assertEqual("hello", typed(c))
        self.assertEqual(5, len(c.strokes))
        space = compiled([{"type": " "}], SMALL)
        self.assertEqual(["space"], [m["name"] for m in of(space, "key") if m["type"] == "keyDown"])
        # The drawer's word for a key is the key, not the letters of the word.
        word = compiled([{"type": "Space"}], SMALL)
        self.assertEqual(["S", "p", "a", "c", "e"], [s["text"] for s in word.strokes])


class Timing(unittest.TestCase):
    def setUp(self):
        self.c = compiled([{"type": "hello, world"}, {"wait": 700}, {"type": "zebra quick"}], wpm=90)
        self.term = (DIAMOND.get("combo_term") or 50) + DIAMOND["hud"]["combo_slack_ms"]

    def test_waits_are_whole_ms_and_never_back(self):
        self.assertTrue(all(isinstance(w, int) and w >= 0 for w, _ in self.c.timeline))

    def test_only_what_a_client_may_send(self):
        self.assertLessEqual({m["kind"] for _, m in self.c.timeline}, set(INJECTABLE))

    def test_a_chord_goes_down_inside_the_combo_term(self):
        times = absolute(self.c)
        for s in (s for s in self.c.strokes if s["combo"]):
            ts = [t for t, m in times if m["kind"] == "press" and m["pos"] in s["strike"] and t >= s["at_ms"]][:len(s["strike"])]
            self.assertLess(max(ts) - min(ts), self.term)

    def test_keystrokes_are_never_close_enough_to_be_a_chord(self):
        at = [s["at_ms"] for s in self.c.strokes]
        self.assertTrue(all(b - a >= floor(DIAMOND) for a, b in zip(at, at[1:])), at)

    def test_a_key_is_let_go_before_it_goes_down_again(self):
        for c in (self.c, compiled([{"type": "hello"}], speed=3)):
            down = set()
            for _, m in c.timeline:
                if m["kind"] == "press":
                    self.assertNotIn(m["pos"], down)
                    down.add(m["pos"])
                elif m["kind"] == "release":
                    down.discard(m["pos"])
            self.assertEqual(set(), down)                            # and nothing is left down
        self.assertEqual([], of(self.c, "layers")[-1]["ids"])

    def test_a_combo_waits_out_the_idle_the_keymap_asks_for(self):
        # require-prior-idle-ms: a chord struck sooner than that after a keystroke is its keys.
        def gaps(keymap):
            c = compiled([{"type": "azaz"}], keymap, combos=True)
            ends = [s["at_ms"] + (len(s["strike"]) - 1) * play.CHORD_MS for s in c.strokes]
            return [b["at_ms"] - end for end, b in zip(ends, c.strokes[1:]) if b["combo"]]
        self.assertTrue(gaps(DIAMOND) and all(g < 400 for g in gaps(DIAMOND)))
        self.assertTrue(all(g >= 400 for g in gaps(dict(DIAMOND, combo_idle=400))))

    def test_a_wait_is_its_length(self):
        without = compiled([{"type": "a"}, {"type": "b"}])
        with_ = compiled([{"type": "a"}, {"wait": 700}, {"type": "b"}])
        self.assertEqual(700, with_.strokes[1]["at_ms"] - without.strokes[1]["at_ms"])

    def test_speed_quickens_the_pace_not_the_chords(self):
        slow, fast = compiled([{"type": "hello"}]), compiled([{"type": "hello"}], speed=2)
        gap = lambda c: c.strokes[1]["at_ms"] - c.strokes[0]["at_ms"]   # noqa: E731
        self.assertLess(gap(fast), gap(slow))
        self.assertGreaterEqual(gap(fast), floor(DIAMOND))


class Scripts(unittest.TestCase):
    def test_demo_vim_types_what_its_frames_show(self):
        c = play.compile(script("docs/demo-vim.json"), DIAMOND)
        self.assertEqual("notehjklveypihi", typed(c))
        self.assertIn("escape", [m["name"] for m in of(c, "key") if m["type"] == "keyDown"])

    def test_a_key_in_two_frames_in_a_row_is_two_keystrokes(self):
        # i opens INSERT, then h is typed with the same key on the layer that came up.
        presses = [m["pos"] for m in of(play.compile(script("docs/demo-vim.json"), DIAMOND), "press")]
        self.assertEqual(3, presses.count(26))

    def test_a_thumb_holding_its_layer_stays_down_across_frames(self):
        c = play.compile(script("docs/demo-3x5.json"), SMALL)
        presses = [m["pos"] for m in of(c, "press")]
        self.assertEqual((1, 1), (presses.count(32), presses.count(33)))

    def test_a_script_of_frames_is_its_own_stills(self):
        s = script("docs/demo-vim.json")
        stills = play.compile(s, DIAMOND).stills
        self.assertEqual(s["steps"], stills["steps"])
        self.assertEqual((s["device"], s["opacity"]), (stills["device"], stills["opacity"]))

    def test_text_is_a_frame_per_keystroke_or_as_asked(self):
        each = compiled([{"type": "hi"}]).stills["steps"]
        self.assertEqual(2, len(each))
        self.assertEqual(["h", "i"], [k["chars"] for k in each[-1]["keys"]])
        self.assertEqual(1, len(compiled([{"type": "hi", "stills": "last"}]).stills["steps"]))
        self.assertEqual(0, len(compiled([{"type": "hi", "stills": "none"}, {"wait": 500}]).stills["steps"]))

    def test_every_committed_script_plays_clean(self):
        for path, keymap in (("docs/demo-3x5.json", SMALL), ("docs/demo-type.json", SMALL), ("docs/demo-vim.json", DIAMOND)):
            c = play.compile(script(path), keymap)
            self.assertEqual([], [p for p in c.problems if p["level"] == "skip"], path)
            self.assertTrue(c.strokes, path)


class Player(unittest.TestCase):
    def test_it_keeps_to_the_clock_it_started_on(self):
        c = compiled([{"type": "hi"}, {"wait": 300}, {"type": "yo"}])
        clock, sent = [0.0], []

        async def sleep(s):
            clock[0] += s

        async def send(m):
            sent.append((round(clock[0] * 1000), m))
        n = asyncio.run(play.play(c.timeline, send, duration_ms=c.duration_ms, now=lambda: clock[0], sleep=sleep))
        self.assertEqual(len(c.timeline), n)
        self.assertEqual([t for t, _ in absolute(c)], [t for t, _ in sent])
        self.assertEqual(c.duration_ms, round(clock[0] * 1000))


if __name__ == "__main__":
    unittest.main()
