"""hud/tests/fixtures/zmk_vectors.json, from ZMK's own behaviour tests: the touch keyboard's virtual
firmware (hud/engine.js) held to what the real one does.

ZMK tests each behaviour on a simulated board: a keymap, a script of key presses and releases with
the milliseconds between them (ZMK_MOCK_PRESS(row, col, ms)), and the keycode events the firmware
raised in answer (keycode_events.snapshot). This turns each test into a vector the engine can run:
the keymap compiled the way the HUD compiles a keyboard's (sync.capture_keymap, actions.build), the
script as positions and times, and the keycode events expected. hud/tests/engine_test.js runs them
with `touch: null`, the engine as firmware.

A test is left out when its keymap cannot be read that far, when it uses a behaviour the engine
does not run (the combos, which glass does not chord), or when its snapshot has no keycode events
to compare. ZMK is MIT licensed (host/vendor/zmk/NOTICE).

    .venv/bin/python3 host/zmk_vectors_gen.py --zmk DIR           print a summary
    .venv/bin/python3 host/zmk_vectors_gen.py --zmk DIR --write   write the fixture

Needs keymap-drawer's parser (the venv) and a ZMK checkout (--zmk or $ZMK_DIR).
"""

import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import actions  # noqa: E402
import sync  # noqa: E402
import zmk_tables  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "hud", "tests", "fixtures", "zmk_vectors.json")
SUITES = ("hold-tap", "sticky-keys", "mod-morph", "tap-dance", "macros", "key-repeat", "caps-word",
          "momentary-layer", "toggle-layer", "to-layer", "transparent", "none", "conditional-layer",
          "modifiers", "keypress", "keytoggle", "gresc")
MOCK = re.compile(r"ZMK_MOCK_(PRESS|RELEASE)\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)")
LINE = re.compile(r"(pressed|released): (?:usage_page 0x([0-9A-Fa-f]+) )?keycode 0x([0-9A-Fa-f]+) "
                  r"implicit_mods 0x([0-9A-Fa-f]+) explicit_mods 0x([0-9A-Fa-f]+)")
COLUMNS = 2   # app/boards/native/native_sim/native_sim_zmk_test_mock.dts: a 2x2 mock matrix


def script(text):
    """The kscan mock's events, [{pos, down, t}], each at the time the waits before it add up to,
    and when the run ends: the mock exits once the last event's wait is over (`exit-after`), so a
    timer due later never shows in the snapshot."""
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"//.*", "", text)
    m = re.search(r"&kscan\s*\{(.*?)\};", text, re.S)
    if not m:
        return None, None
    t, out = 0, []
    for kind, row, col, wait in MOCK.findall(m.group(1)):
        out.append({"pos": int(row) * COLUMNS + int(col), "down": kind == "PRESS", "t": t})
        t += int(wait)
    return out, t


def expected(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = LINE.search(line)
            if m:
                state, page, kc, imp, exp = m.groups()
                out.append([state == "pressed", int(page, 16) if page else None, int(kc, 16), int(imp, 16), int(exp, 16)])
    return out


def vector(test_dir, zmk):
    keymap_path = os.path.join(test_dir, "native_sim.keymap")
    snapshot = os.path.join(test_dir, "keycode_events.snapshot")
    name = os.path.relpath(test_dir, os.path.join(zmk, "app", "tests"))
    if not os.path.isfile(snapshot):
        return None, f"{name}: no snapshot"
    with open(keymap_path, encoding="utf-8") as f:
        text = f.read()
    events, end = script(text)
    if not events:
        return None, f"{name}: no script"
    want = expected(snapshot)
    if not want:
        return None, f"{name}: no keycode events to compare"
    captured = sync.capture_keymap(sync.device_tree(keymap_path), False)
    if not captured:
        return None, f"{name}: the keymap could not be read"
    built = actions.build({}, {}, {}, None, {}, raw=captured)
    if built["coverage"]["unsupported"]:
        return None, f"{name}: unsupported {sorted(built['coverage']['why'])}"
    if captured.get("combos") and "combo" in name:
        return None, f"{name}: combos"   # a keymap that defines one but never strikes it still runs
    return {"name": name, "actions": built, "events": events, "end": end, "expect": want}, None


def generate(zmk):
    vectors, skipped = [], []
    for suite in SUITES:
        for keymap_path in sorted(glob.glob(os.path.join(zmk, "app", "tests", suite, "**", "native_sim.keymap"),
                                            recursive=True)):
            v, why = vector(os.path.dirname(keymap_path), zmk)
            if v:
                vectors.append(v)
            else:
                skipped.append(why)
    return {"zmk": zmk_tables.ZMK_COMMIT, "vectors": vectors}, skipped


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    zmk = os.environ.get("ZMK_DIR")
    if "--zmk" in argv:
        zmk = argv[argv.index("--zmk") + 1]
    if not zmk or not os.path.isdir(os.path.join(zmk, "app", "tests")):
        print("zmk_vectors_gen: give a ZMK checkout (--zmk DIR or $ZMK_DIR)", file=sys.stderr)
        return 2
    data, skipped = generate(os.path.expanduser(zmk))
    print(f"{len(data['vectors'])} vectors; {len(skipped)} tests left out", file=sys.stderr)
    for why in skipped:
        print(f"  {why}", file=sys.stderr)
    if "--write" in argv:
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            f.write("\n")
        print(f"wrote {os.path.relpath(OUT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
