"""Tests for site/build.py: what the landing page is built from, and what it is allowed to publish.

The steps are tested as functions on the repo's own files, with the standard library alone, since
`make test-site` runs a Python without the venv. The whole build needs keymap-drawer and runs only
where it is installed, the way keymap_test.py does."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build  # noqa: E402

try:
    import keymap_drawer  # noqa: F401
    HAVE_DRAWER = True
except ImportError:
    HAVE_DRAWER = False

MESSAGE = {"kind": "keymap", "source": "projects/keyboards/docs/keymap.yaml", "title": "",
           "hud": {"opacity": 86, "press_ms": 320}, "layers": {"base": [{"tap": "A"}]}, "combos": [],
           "glyphs": {}}


class Publish(unittest.TestCase):
    def test_the_source_is_the_boards_public_one_and_the_panel_is_solid(self):
        out = build.publish(MESSAGE, {"source": "examples/3x5.yaml"})
        self.assertEqual(out["source"], "examples/3x5.yaml")
        self.assertEqual(out["hud"], {"opacity": 100, "press_ms": 320})

    def test_the_message_it_was_given_is_left_alone(self):
        build.publish(MESSAGE, {"source": "examples/3x5.yaml"})
        self.assertEqual(MESSAGE["source"], "projects/keyboards/docs/keymap.yaml")
        self.assertEqual(MESSAGE["hud"]["opacity"], 86)


class Glyphs(unittest.TestCase):
    def test_a_glyph_the_message_lacks_is_named(self):
        msg = dict(MESSAGE, layers={"base": [{"tap": "", "glyph": "mdi:repeat"}, {"tap": "", "glyph": "mdi:apple"}]},
                   glyphs={"mdi:apple": "<svg/>"})
        self.assertEqual(build.missing_glyphs(msg), ["mdi:repeat"])

    def test_none_missing_is_none(self):
        self.assertEqual(build.missing_glyphs(MESSAGE), [])


class Manifest(unittest.TestCase):
    def test_the_committed_manifest_is_whole(self):
        boards = build.load_manifest()
        self.assertEqual(build.manifest_problems(boards), [])
        self.assertEqual([b["id"] for b in boards][0], "3x5")   # the one the page opens on

    def test_what_a_broken_one_is_told(self):
        boards = [{"id": "3x5", "label": "a", "blurb": "b", "config": "config/example-3x5.yaml", "source": "s"},
                  {"id": "3x5", "label": "a", "blurb": "b", "config": "config/nope.yaml", "source": "s"},
                  {"id": "Big Board", "label": "a", "blurb": "b", "config": "config/example-3x5.yaml", "source": "s",
                   "demo": "docs/nope.json"}]
        problems = build.manifest_problems(boards)
        self.assertIn("board id '3x5' is used twice", problems)
        self.assertIn("3x5: config config/nope.yaml is not in the repo", problems)
        self.assertIn("board id 'Big Board' is not a lowercase slug", problems)
        self.assertIn("Big Board: demo docs/nope.json is not in the repo", problems)


class HudFiles(unittest.TestCase):
    def test_what_the_page_names_is_what_is_copied(self):
        with open(os.path.join(build.REPO, "hud", "index.html"), encoding="utf-8") as f:
            names = build.hud_files(f.read())
        self.assertEqual(names[0], "index.html")
        self.assertEqual(sorted(names[1:]), ["hud.css", "hud.js", "keys.js"])
        for name in names:
            self.assertTrue(os.path.isfile(os.path.join(build.REPO, "hud", name)), name)

    def test_links_elsewhere_are_not_files(self):
        self.assertEqual(build.hud_files('<link href="https://x/y.css"><script src="/abs.js"></script>'
                                         '<script src="a.js?v=1"></script>'), ["index.html"])


class Missing(unittest.TestCase):
    """A board whose files are not on this machine: skipped with a line saying so, unless --strict."""

    def board(self, tmp):
        config = os.path.join(tmp, "config.yaml")
        with open(config, "w", encoding="utf-8") as f:
            f.write("keymap: " + os.path.join(tmp, "not-here.yaml") + "\n")
        return {"id": "gone", "label": "Gone", "blurb": "b", "config": config, "source": "s", "demo": None}

    def test_skipped_when_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            said = []
            build.log, log = said.append, build.log
            try:
                self.assertIsNone(build.build_board(self.board(tmp), strict=False))
            finally:
                build.log = log
            self.assertTrue(any("gone skipped" in s for s in said), said)

    def test_fatal_when_strict(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(build.BuildError):
                build.build_board(self.board(tmp), strict=True)

    def test_a_config_that_is_not_there_never_falls_back_to_the_users_own(self):
        with self.assertRaises(build.BuildError) as e:
            build.build_board({"id": "x", "config": "config/nope.yaml"}, strict=True)
        self.assertIn("no config/nope.yaml", str(e.exception))


class Prepare(unittest.TestCase):
    def test_it_does_not_clear_what_it_did_not_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "notes.txt"), "w") as f:
                f.write("mine")
            with self.assertRaises(build.BuildError):
                build.prepare(tmp)
            self.assertTrue(os.path.exists(os.path.join(tmp, "notes.txt")))

    def test_it_clears_a_page_it_built(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "site")
            os.makedirs(os.path.join(out, "boards"))
            with open(os.path.join(out, "index.html"), "w") as f:
                f.write("old")
            build.prepare(out)
            self.assertEqual(sorted(os.listdir(out)), ["boards", "hud"])


@unittest.skipUnless(HAVE_DRAWER, "keymap-drawer is not installed for this Python (make venv)")
class WholeBuild(unittest.TestCase):
    def test_the_page_is_built(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "site")
            build.log, log = (lambda msg: None), build.log
            try:
                built = build.build(out)
            finally:
                build.log = log
            ids = [b["id"] for b in built]
            self.assertEqual(ids[:1], ["3x5"])
            manifest = {b["id"]: b for b in build.load_manifest()}
            for b in built:
                with open(os.path.join(out, b["keymap"]), encoding="utf-8") as f:
                    msg = json.load(f)
                self.assertEqual(msg["source"], manifest[b["id"]]["source"])
                self.assertEqual(msg["hud"]["opacity"], 100)
                if b["demo"]:
                    with open(os.path.join(out, b["demo"]), encoding="utf-8") as f:
                        self.assertTrue(json.load(f)["timeline"])
            self.assertEqual(sorted(os.listdir(os.path.join(out, "hud"))), ["hud.css", "hud.js", "index.html", "keys.js"])
            with open(os.path.join(out, "index.html"), encoding="utf-8") as f:
                self.assertNotIn("<!--commit-->", f.read())


if __name__ == "__main__":
    unittest.main()
