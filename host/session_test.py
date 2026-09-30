"""Tests for host/session.py: the session files, what the commands do to them, and the feed's
store that adds the page's counts to them. Standard library only, like the module."""

import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import session  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def counts(presses=None, combos=None, **totals):
    return dict({"presses": presses or {}, "combos": combos or {}}, **totals)


def batch(seq, page="p1", sid=None, gen=None, presses=None, combos=None, **totals):
    msg = {"kind": "tally", "v": 1, "page": page, "seq": seq, "session": sid, "gen": gen,
           "presses": presses or {}, "combos": combos or {}}
    msg.update({k: 0 for k in session.TOTALS + ("peak_wpm",)})
    msg.update(totals)
    return msg


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "sessions")

    def tearDown(self):
        self.tmp.cleanup()

    def active(self):
        return session.status(self.dir)[1]


class Files(Base):
    def test_the_first_run_makes_one_active_session(self):
        st, s = session.status(self.dir)
        self.assertEqual(st["active"], s["name"])
        self.assertFalse(s["named"])
        self.assertTrue(session.is_empty(s))
        self.assertEqual(0o700, os.stat(self.dir).st_mode & 0o777)
        self.assertEqual(0o600, os.stat(session.path_of(self.dir, s["name"])).st_mode & 0o777)

    def test_a_write_is_whole_or_not_at_all(self):
        os.makedirs(self.dir)
        path = os.path.join(self.dir, "x.json")
        session.write_json(path, {"a": 1})
        real = os.replace

        def full(*a):
            raise OSError("no space left on device")
        os.replace = full
        try:
            with self.assertRaises(OSError):
                session.write_json(path, {"a": 2})
        finally:
            os.replace = real
        with open(path) as f:
            self.assertEqual({"a": 1}, json.load(f))
        self.assertEqual(["x.json"], os.listdir(self.dir))   # nothing half-written left beside it

    def test_counts_are_added_to_the_session_they_were_typed_in(self):
        s = self.active()
        session.add_counts(self.dir, s["id"], s["gen"], counts({"base": {"3": 2}}, {"base": {"1,2": 1}}, chars=5))
        session.add_counts(self.dir, s["id"], s["gen"], counts({"base": {"3": 1, "4": 1}}, chars=2, peak_wpm=70))
        s = self.active()
        self.assertEqual({"3": 3, "4": 1}, s["presses"]["base"])
        self.assertEqual({"1,2": 1}, s["combos"]["base"])
        self.assertEqual((7, 70), (s["totals"]["chars"], s["totals"]["peak_wpm"]))

    def test_a_file_that_does_not_parse_is_kept_aside(self):
        s = self.active()
        with open(session.path_of(self.dir, s["name"]), "w") as f:
            f.write("{not json")
        logged = []
        fresh = session.status(self.dir, log=logged.append)[1]
        self.assertNotEqual(s["id"], fresh["id"])
        self.assertTrue(any(n.startswith(s["name"] + ".json.corrupt-") for n in os.listdir(self.dir)))
        self.assertTrue(logged)

    def test_names(self):
        for good in ("week1", "2026-09-30-1012", "colemak.dh_v2"):
            self.assertEqual(good, session.check_name(good))
        for bad in ("", ".hidden", "a/b", "../up", "x" * 65, "sp ace", "é", "a b"):
            with self.assertRaises(session.SessionError):
                session.check_name(bad)

    def test_it_all_runs_where_the_venv_does_not(self):
        # `zmk-layer-hud session` runs on Apple's Python 3.9 like `doctor`.
        for name in ("session.py", "cli.py"):
            with open(os.path.join(HERE, name), encoding="utf-8") as f:
                ast.parse(f.read(), filename=name, feature_version=(3, 9))

    def test_two_writers_at_once_lose_nothing(self):
        s = self.active()
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import session\n"
                "for _ in range(40):\n"
                "    session.add_counts(sys.argv[2], sys.argv[3], 0, {'presses': {'base': {'1': 1}}, 'combos': {}})\n")
        procs = [subprocess.Popen([sys.executable, "-c", code, HERE, self.dir, s["id"]]) for _ in range(2)]
        for p in procs:
            self.assertEqual(0, p.wait(timeout=60))
        self.assertEqual(80, self.active()["presses"]["base"]["1"])


class Commands(Base):
    def test_save_names_a_session_that_had_no_name(self):
        s = self.active()
        session.add_counts(self.dir, s["id"], 0, counts({"base": {"3": 2}}))
        named = session.save(self.dir, "week1")
        self.assertEqual((s["id"], "week1", True), (named["id"], named["name"], named["named"]))
        self.assertFalse(os.path.exists(session.path_of(self.dir, s["name"])))
        self.assertEqual(2, self.active()["presses"]["base"]["3"])

    def test_save_as_keeps_the_named_one_as_it_was(self):
        session.save(self.dir, "week1")
        s = self.active()
        session.add_counts(self.dir, s["id"], 0, counts({"base": {"3": 2}}))
        copy = session.save(self.dir, "week2")
        self.assertNotEqual(s["id"], copy["id"])
        self.assertEqual("week2", self.active()["name"])
        session.add_counts(self.dir, copy["id"], 0, counts({"base": {"3": 1}}))
        every = session.sessions(self.dir)
        self.assertEqual((2, 3), (every["week1"]["presses"]["base"]["3"], every["week2"]["presses"]["base"]["3"]))

    def test_load_resumes_a_saved_session(self):
        session.save(self.dir, "week1")
        session.add_counts(self.dir, self.active()["id"], 0, counts({"base": {"3": 2}}))
        session.new(self.dir, "week2")
        self.assertTrue(session.is_empty(self.active()))
        session.load(self.dir, "week1")
        s = self.active()
        self.assertEqual(("week1", 2), (s["name"], s["presses"]["base"]["3"]))
        with self.assertRaises(session.SessionError):
            session.load(self.dir, "nope")

    def test_an_empty_session_nobody_named_is_not_kept(self):
        first = self.active()
        session.new(self.dir)
        # (The new one may take the same name: both began in this minute.)
        self.assertEqual([self.active()["id"]], [s["id"] for s in session.sessions(self.dir).values()])
        self.assertNotEqual(first["id"], self.active()["id"])
        session.add_counts(self.dir, self.active()["id"], 0, counts(chars=3))
        kept = self.active()
        session.new(self.dir)
        self.assertTrue(os.path.exists(session.path_of(self.dir, kept["name"])))
        self.assertEqual(2, len(session.sessions(self.dir)))

    def test_new_will_not_take_a_name_in_use(self):
        session.save(self.dir, "week1")
        with self.assertRaises(session.SessionError):
            session.new(self.dir, "week1")

    def test_the_active_session_cannot_be_deleted(self):
        session.save(self.dir, "week1")
        session.new(self.dir, "week2")
        with self.assertRaises(session.SessionError):
            session.delete(self.dir, "week2")
        session.delete(self.dir, "week1")
        self.assertEqual(["week2"], list(session.sessions(self.dir)))

    def test_reset_empties_it_and_turns_away_what_was_on_the_way(self):
        s = self.active()
        session.add_counts(self.dir, s["id"], 0, counts({"base": {"3": 2}}))
        fresh = session.reset(self.dir)
        self.assertEqual((s["id"], 1), (fresh["id"], fresh["gen"]))
        self.assertTrue(session.is_empty(self.active()))
        # Counted before the reset, written after it: typed into what was thrown away.
        self.assertIsNone(session.add_counts(self.dir, s["id"], 0, counts({"base": {"3": 5}})))
        self.assertTrue(session.is_empty(self.active()))

    def with_counts(self, layers, presses, combos=None):
        _, s = session.status(self.dir)
        session.add_counts(self.dir, s["id"], s["gen"], counts(presses, combos), layers=layers)
        return self.active()

    def test_counts_on_a_layer_the_keymap_no_longer_has_are_told(self):
        s = self.with_counts(["base", "symbols"], {"base": {"3": 5}, "sym": {"4": 2, "5": 1}}, {"sym": {"1,2": 3}})
        self.assertEqual({"sym": (3, 3)}, session.orphans(s))
        s["layers"] = []                             # a session from before layers were written down
        self.assertEqual({}, session.orphans(s))

    def test_a_renamed_layer_takes_its_counts_along(self):
        self.with_counts(["base", "symbols"], {"sym": {"4": 2}, "symbols": {"4": 1, "6": 1}}, {"sym": {"1,2": 3}})
        self.assertEqual([(self.active()["name"], 2, 3)], session.rename_layer(self.dir, "sym", "symbols"))
        s = self.active()
        self.assertEqual(({"symbols": {"4": 3, "6": 1}}, {"symbols": {"1,2": 3}}), (s["presses"], s["combos"]))
        self.assertEqual({}, session.orphans(s))
        with self.assertRaises(session.SessionError):
            session.rename_layer(self.dir, "sym", "symbols")          # nothing is left there

    def test_every_session_or_only_the_active_one(self):
        self.with_counts(["base"], {"sym": {"4": 2}})
        session.save(self.dir, "week1")
        session.new(self.dir, "week2")
        self.with_counts(["base"], {"sym": {"4": 1}})
        self.assertEqual([("week2", 1, 0)], session.rename_layer(self.dir, "sym", "symbols"))
        self.assertEqual(2, session.sessions(self.dir)["week1"]["presses"]["sym"]["4"])
        session.rename_layer(self.dir, "sym", "symbols", every=True)
        self.assertEqual(2, session.sessions(self.dir)["week1"]["presses"]["symbols"]["4"])

    def test_the_heatmap_is_kept(self):
        session.set_heatmap(self.dir, "session")
        self.assertEqual("session", session.status(self.dir)[0]["heatmap"])
        with self.assertRaises(session.SessionError):
            session.set_heatmap(self.dir, "sometimes")


class StoreTest(Base):
    def store(self, directory=None):
        self.sent = []
        st = session.Store(self.sent.append, directory=self.dir if directory is None else directory, log=lambda m: None)
        st.reload(announce=True)
        return st

    def test_what_the_page_reports_reaches_the_file(self):
        st = self.store()
        st.apply(batch(1, presses={"base": {"3": 2}}, chars=4))
        self.assertEqual(2, self.sent[-1]["presses"]["base"]["3"])       # shown before it is written
        self.assertEqual({"p1": 1}, self.sent[-1]["acks"])
        self.assertTrue(session.is_empty(self.active()))
        st.flush()
        self.assertEqual((2, 4), (self.active()["presses"]["base"]["3"], self.active()["totals"]["chars"]))
        self.assertEqual(2, st.message()["presses"]["base"]["3"])         # and not twice after

    def test_a_batch_seen_before_is_not_added_again(self):
        st = self.store()
        st.apply(batch(1, presses={"base": {"3": 1}}))
        st.apply(batch(1, presses={"base": {"3": 1}}))
        st.flush()
        self.assertEqual(1, self.active()["presses"]["base"]["3"])

    def test_a_batch_that_does_not_add_up_is_dropped(self):
        st = self.store()
        for bad in (batch(1, presses={"base": {"3": -1}}), batch(2, presses={"base": {"x": 1}}),
                    batch(3, presses={"base": {"3": True}}), batch(4, chars="many")):
            st.apply(bad)
        st.flush()
        self.assertTrue(session.is_empty(self.active()))

    def test_a_reset_while_it_counts_wins(self):
        st = self.store()
        st.apply(batch(1, presses={"base": {"3": 5}}))
        session.reset(self.dir)                  # `zmk-layer-hud session reset`, from a terminal
        st.flush()
        self.assertTrue(session.is_empty(self.active()))
        st.poll()
        self.assertEqual(1, self.sent[-1]["gen"])

    def test_a_late_batch_lands_in_the_session_it_was_typed_in(self):
        session.save(self.dir, "week1")
        st = self.store()
        old = st.session["id"]
        session.new(self.dir, "week2")           # `session new`, while the page still counts
        st.poll()
        self.assertEqual("week2", self.sent[-1]["name"])
        st.apply(batch(1, sid=old, gen=0, presses={"base": {"3": 2}}))
        st.flush()
        every = session.sessions(self.dir)
        self.assertEqual(2, every["week1"]["presses"]["base"]["3"])
        self.assertTrue(session.is_empty(every["week2"]))

    def test_the_heatmap_it_is_told_is_kept_and_said(self):
        st = self.store()
        st.set_heatmap("session")
        self.assertEqual("session", self.sent[-1]["heatmap"])
        self.assertEqual("session", session.status(self.dir)[0]["heatmap"])

    def test_the_keymaps_layers_go_into_the_session(self):
        st = self.store()
        st.set_keymap({"source": "/home/me/zmk/board.yaml", "layers": {"base": {}, "sym": {}}})
        st.apply(batch(1, presses={"base": {"3": 2}}))
        st.flush()
        s = self.active()
        self.assertEqual((["base", "sym"], "board.yaml"), (s["layers"], s["keymap"]))
        # A keymap edited while it runs: its layers are written even before anything is typed.
        updated = s["updated"]
        st.set_keymap({"source": "/home/me/zmk/board.yaml", "layers": {"base": {}, "symbols": {}}})
        st.flush()
        s = self.active()
        self.assertEqual((["base", "symbols"], updated), (s["layers"], s["updated"]))

    def test_the_demo_keeps_its_session_in_memory(self):
        st = self.store(directory=False)
        st.apply(batch(1, presses={"base": {"3": 2}}))
        st.flush()
        self.assertEqual(2, st.message()["presses"]["base"]["3"])
        self.assertFalse(os.path.exists(self.dir))


if __name__ == "__main__":
    unittest.main()
