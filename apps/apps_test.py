"""The tablets' keyboards against what they are built from: the HUD files each one ships
(hud-files.json) are the ones its build takes and the ones its page or JavaScriptCore loads, in that
order, and the key names its text sink knows are the ones the page sends. Stdlib only; runs with
`make test-apps`, without the Android SDK or Xcode."""

import json
import os
import re
import sys
import unittest

APPS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(APPS)
sys.path.insert(0, os.path.join(REPO, "site"))

import build  # noqa: E402  (site/build.py: what hud/index.html loads)


def read(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as f:
        return f.read()


FILES = json.loads(read("apps", "hud-files.json"))


def text_keys():
    """The key names a text op carries (hud/hidtext.js TEXT_KEYS), F1 to F24 with them."""
    table = re.search(r"TEXT_KEYS = \{([^}]*)\}", read("hud", "hidtext.js")).group(1)
    return set(re.findall(r':\s*"(\w+)"', table)) | {f"f{i}" for i in range(1, 25)}


class Android(unittest.TestCase):
    def test_it_ships_what_the_page_loads(self):
        self.assertEqual(sorted(build.hud_files(read("hud", "index.html"))), sorted(FILES["android"]))
        for name in FILES["android"]:
            self.assertTrue(os.path.isfile(os.path.join(REPO, "hud", name)), name)

    def test_its_build_copies_that_list(self):
        gradle = read("apps", "android", "app", "build.gradle.kts")
        self.assertIn('rootDir.resolve("../hud-files.json")', gradle)
        self.assertIn('["android"]', gradle)

    def test_its_sink_knows_every_key_the_page_sends_that_android_has(self):
        names = set(re.findall(r'put\("(\w+)", KeyEvent\.', read("apps", "android", "sink", "src", "main", "kotlin", "io",
                                                                   "github", "rafaelromao", "zmkhud", "sink", "ImeSink.kt")))
        names |= {f"f{i}" for i in range(1, 13)}          # put("f$i", ...) in a loop
        self.assertEqual(text_keys() - {f"f{i}" for i in range(13, 25)}, names)

    def test_its_page_types_text_into_the_bridge_the_keyboard_listens_on(self):
        boot = read("apps", "android", "app", "src", "main", "assets", "ime-boot.js")
        ime = read("apps", "android", "app", "src", "main", "kotlin", "io", "github", "rafaelromao", "zmkhud", "HudIme.kt")
        bridge = re.search(r'BRIDGE = "(\w+)"', ime).group(1)
        self.assertIn(f"window.{bridge}.postMessage", boot)
        self.assertIn('output: "text"', boot)


class IOS(unittest.TestCase):
    def test_it_ships_what_javascriptcore_runs_in_that_order(self):
        yml = read("apps", "ios", "project.yml")
        self.assertEqual(re.findall(r"path: \.\./\.\./hud/([\w.]+)", yml), FILES["ios"])
        swift = read("apps", "ios", "Core", "Sources", "ZmkHudCore", "JSKbd.swift")
        listed = re.search(r"static let scripts = \[([^\]]*)\]", swift).group(1)
        self.assertEqual(re.findall(r'"([\w.]+)"', listed), FILES["ios"])
        for name in FILES["ios"]:
            self.assertTrue(os.path.isfile(os.path.join(REPO, "hud", name)), name)

    def test_what_its_sink_does_is_keys_the_page_sends(self):
        sink = read("apps", "ios", "Core", "Sources", "ZmkHudCore", "TextSink.swift")
        names = set(re.findall(r'case "(\w+)":', sink))
        self.assertTrue(names)
        self.assertLessEqual(names, text_keys())

    def test_the_app_opens_the_link_the_command_line_prints(self):
        plist = read("apps", "ios", "App", "Info.plist")
        self.assertRegex(plist, r"<key>CFBundleURLSchemes</key>\s*<array>\s*<string>zmkhud</string>")
        swift = read("apps", "ios", "Core", "Sources", "ZmkHudCore", "RemoteImport.swift")
        self.assertIn('url.scheme == "zmkhud", url.host == "import"', swift)
        self.assertIn('"zmkhud://import?from="', read("host", "cli.py"))

    def test_the_android_keyboard_reaches_no_network(self):
        manifest = read("apps", "android", "app", "src", "main", "AndroidManifest.xml")
        self.assertNotIn("android.permission.INTERNET", manifest)

    def test_the_keyboard_asks_for_no_full_access(self):
        plist = read("apps", "ios", "Keyboard", "Info.plist")
        self.assertRegex(plist, r"<key>RequestsOpenAccess</key>\s*<false/>")


class Fixtures(unittest.TestCase):
    def test_the_golden_answers_are_made_on_the_keymap_beside_them(self):
        golden = json.loads(read("apps", "fixtures", "kbd-golden.json"))
        self.assertEqual("kbd-keymap.json", golden["keymap"])
        keymap = json.loads(read("apps", "fixtures", "kbd-keymap.json"))
        self.assertEqual(len(keymap["layout"]["keys"]), len(golden["steps"][0]["board"]["keys"]))
        self.assertTrue(any(s["answer"]["ops"] for s in golden["steps"]))


if __name__ == "__main__":
    unittest.main()
