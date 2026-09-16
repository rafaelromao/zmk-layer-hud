#!/usr/bin/env python3
"""Build hud/keymap.json (+ keymap.js) from the keyboards repo.

Sources of truth (set KEYBOARDS_REPO if the clone is not at ~/projects/keyboards):
  docs/img/diagrams/keymap-drawer/keymap-drawer.yaml   the hand-curated drawer input that renders
                                                       https://rafaelromao.github.io/keyboards
  src/definitions/config.dtsi                          the layer ids the firmware signals

The drawer YAML is a curated view (six vim layers drawn as one, copy layers folded into their
originals, a few pseudo-layers), so ZMK_LAYERS below maps every config.dtsi layer id to the drawer
layer that shows it and to a banner label. The HUD consumes the JSON this emits.

Only `yq` (Mike Farah v4 or the Python/jq wrapper) is needed: it converts the YAML
to JSON, the rest is stdlib.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KEYBOARDS = os.environ.get("KEYBOARDS_REPO", os.path.expanduser("~/projects/keyboards"))
SRC = os.path.join(KEYBOARDS, "docs/img/diagrams/keymap-drawer/keymap-drawer.yaml")
CONFIG = os.path.join(KEYBOARDS, "src/definitions/config.dtsi")
OUT = os.path.join(HERE, "..", "keymap.json")

# The firmware's layer signal (firmware/dts/bindings/zmk,layer-signal.yaml defaults).
SIGNAL = {"base": 0xC0, "commit": 0xDF}

# config.dtsi layer define -> (drawer layer that shows it or None when it is transparent /
# undrawn, banner label, banner class). Every define in config.dtsi's layer block must be here.
ZMK_LAYERS = {
    "ALPHA1": ("alpha1", "Alpha 1", "off"),
    "ALT_OS": (None, "Alt OS", "momentary"),
    "VIM_NORMAL": ("vim", "Vim normal", "vim"),
    "VIM_VISUAL": ("vim", "Vim visual", "vim"),
    "VIM_CHANGE": ("vim", "Vim change", "vim"),
    "VIM_INSERT": (None, "Vim insert", "vim-insert"),
    "VIM_REPLACE": ("vim", "Vim replace", "vim"),
    "VIM_CMDLINE": (None, "Vim cmdline", "vim-cmdline"),
    "CASE_A1": ("shifted1", "Caps word", "momentary"),
    "CPLK_A1": ("shifted1", "Caps line", "momentary"),
    "SHORTS": ("shortcuts", "Shortcuts", "momentary"),
    "TOGS": ("toggles", "Toggles", "momentary"),
    "MEHS": ("mehs", "Mehs", "momentary"),
    "ALPHA2": ("alpha2", "Alpha 2", "momentary"),
    "NUM": ("numbers", "Numbers", "momentary"),
    "SYM": ("symbols", "Symbols", "momentary"),
    "SEN_CASE": ("shifted1", "Sentence case", "momentary"),
    "CCEDIL": ("ç-extension", "Ç extension", "momentary"),
    "SFT_A2": ("shifted2", "Shift · Alpha 2", "momentary"),
    "CASE_A2": ("shifted2", "Caps · Alpha 2", "momentary"),
    "CASE_CCEDIL": ("ç-extension", "Caps · Ç", "momentary"),
    "ALTREP2": (None, "Alt repeat", "momentary"),
    "NAV": ("nav", "Navigation", "momentary"),
    "NUM_CP": ("numbers", "Numbers", "momentary"),
    "SYM_CP": ("symbols", "Symbols", "momentary"),
    "MEDIA": ("media", "Media / mouse", "momentary"),
    "NAV_CP": ("nav", "Navigation", "momentary"),
    "TEXT": ("text", "Text navigation", "momentary"),
    "FUN": ("func", "Function keys", "momentary"),
    "MACROS": ("macros", "Macros", "momentary"),
}

DEFINE_RE = re.compile(r"^\s*#define\s+([A-Z][A-Z0-9_]*)\s+(\d+)\s*$")


def parse_layer_ids(path: str) -> dict:
    """config.dtsi -> {define: id} for the '// Layers' block (consecutive #define NAME n lines,
    blank lines allowed, stopping at the first other line)."""
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip().startswith("//") and "layers" in l.lower())
    except StopIteration:
        sys.exit(f"error: no '// Layers' block in {path}")
    ids = {}
    for line in lines[start + 1:]:
        if not line.strip():
            continue
        m = DEFINE_RE.match(line)
        if not m:
            break
        ids[m.group(1)] = int(m.group(2))
    return ids

# keymap-drawer glyph names -> text the HUD can show. The glyph id is kept too,
# so the CSS can style it if a real icon is ever wanted.
GLYPHS = {
    "mdi:keyboard-space": "␣",
    "mdi:repeat": "↻",
    "mdi:apple-keyboard-shift": "⇧",
    "mdi:apple-keyboard-control": "⌃",
    "mdi:apple-keyboard-option": "⌥",
    "mdi:apple-keyboard-command": "⌘",
    "shift_command": "⇧⌘",
    "mdi:backspace-outline": "⌫",
    "mdi:backspace-reverse-outline": "⌦",
    "mdi:keyboard-return": "↵",
    "mdi:keyboard-tab": "⇥",
    "mdi:arrow-left": "←",
    "mdi:arrow-right": "→",
    "mdi:arrow-up": "↑",
    "mdi:arrow-down": "↓",
    "mdi:format-horizontal-align-left": "⇱",
    "mdi:format-horizontal-align-right": "⇲",
    "mdi:numeric": "#",
    "mdi:bluetooth": "BT",
    "mdi:bluetooth-off": "BT✕",
    "mdi:lock": "🔒",
    "mdi:power-sleep": "zz",
    "mdi:flash": "⚡",
    "mdi:mouse-left-click": "🖱L",
    "mdi:mouse-right-click": "🖱R",
    "mdi:volume-medium": "🔉",
    "mdi:volume-high": "🔊",
    "mdi:microphone": "🎤",
    "mdi:camera": "📷",
    "mdi:play-pause": "⏯",
    "mdi:skip-backward": "⏮",
    "mdi:skip-forward": "⏭",
    "mdi:content-copy": "⎘",
    "mdi:content-paste": "📋",
    "mdi:magnify": "🔍",
    "mdi:undo": "↶",
    "mdi:select-all": "⊞",
    "mdi:content-save": "💾",
    "mdi:content-save-check": "💾✓",
    "mdi:fullscreen": "⛶",
    "mdi:calculator": "🧮",
    "mdi:folder-open": "📁",
    "mdi:web": "🌐",
    "mdi:microsoft-visual-studio-code": "⌨",
    "mdi:console-line": ">_",
    "mdi:note-multiple-outline": "🗒",
    "mdi:ray-start-arrow": "⇢",
    "mdi:emoticon-happy-outline": "☺",
    "mdi:open-in-new": "⧉",
    "mdi:close": "✕",
    "mdi:refresh": "⟳",
    "mdi:backup-restore": "⟲",
    "mdi:content-cut": "✂",
    "mdi:docker": "🐳",
    "mdi:chat-processing-outline": "💬",
    "mdi:poll": "📊",
    "mdi:magnify-minus-outline": "🔍-",
    "mdi:magnify-plus-outline": "🔍+",
    "mdi:cog": "⚙",
    "mdi:apps": "⊞",
    "mdi:tab-search": "⌕",
    "mdi:layers-search-outline": "⌕",
    "mdi:select-compare": "⇕",
    "mdi:arrow-left-circle": "◀",
    "mdi:arrow-right-circle": "▶",
    "mdi:arrow-up-circle": "▲",
    "mdi:arrow-down-circle": "▼",
    "mdi:arrow-up-circle-outline": "△",
    "mdi:arrow-down-circle-outline": "▽",
    "mdi:hand-back-right-outline": "✋",
    "mdi:loupe": "⌕",
    "mdi:arrow-up-bold-outline": "⇞",
    "mdi:arrow-down-bold-outline": "⇟",
}

# How the daemon's 3-bit code shows on the HUD. Layer names are drawer layers.
CODES = {
    0: {"mode": "off", "chip": "OFF", "layers": ["alpha1"], "vim": False},
    1: {"mode": "normal", "chip": "NORMAL", "layers": ["alpha1", "vim"], "vim": True},
    2: {"mode": "insert", "chip": "INSERT", "layers": ["alpha1"], "vim": True},
    3: {"mode": "visual", "chip": "VISUAL", "layers": ["alpha1", "vim"], "vim": True},
    4: {"mode": "legacy", "chip": "VIM (inferred)", "layers": ["alpha1", "vim"], "vim": True},
    5: {"mode": "cmdline", "chip": "CMDLINE", "layers": ["alpha1"], "vim": True},
    6: {"mode": "raw", "chip": "RAW", "layers": ["alpha1"], "vim": False},
    7: {"mode": "legacy", "chip": "VIM (inferred)", "layers": ["alpha1", "vim"], "vim": True},
}

# ZMK key positions (src/definitions/config.dtsi) per hand column and row.
# Left hand columns: pinky, ring, middle, index. Right: index, middle, ring, pinky.
ZMK_LEFT = [[None, 10, None], [1, 11, 21], [2, 12, 22], [3, 13, 23]]
ZMK_RIGHT = [[6, 16, 26], [7, 17, 27], [8, 18, 28], [None, 19, None]]
ZMK_THUMBS_LEFT = [31, 32]   # L1, L0
ZMK_THUMBS_RIGHT = [33, 34]  # R0, R1
ZMK_NAMES = {
    1: "LTR", 2: "LTM", 3: "LTI", 6: "RTI", 7: "RTM", 8: "RTR",
    10: "LHP", 11: "LHR", 12: "LHM", 13: "LHI", 16: "RHI", 17: "RHM", 18: "RHR", 19: "RHP",
    21: "LBR", 22: "LBM", 23: "LBI", 26: "RBI", 27: "RBM", 28: "RBR",
    31: "L1", 32: "L0", 33: "R0", 34: "R1",
}

# Expected base-layer letter combos (keyboards docs: "ns=q mg=k st=w cp=v lo=x ra=z h,=j ae=y").
COMBO_SANITY = {
    "q": ["n", "s"], "k": ["m", "g"], "w": ["s", "t"], "v": ["c", "p"],
    "x": ["l", "o"], "z": ["r", "a"], "y": ["a", "e"], "j": ["h|v", ","],
}


def load_yaml(path: str) -> dict:
    version = subprocess.run(["yq", "--version"], capture_output=True, text=True, check=True)
    # Arch's `yq` package wraps jq; `go-yq` is Mike Farah's implementation.
    args = ["-o=json"] if "mikefarah" in version.stdout.lower() else []
    res = subprocess.run(["yq", *args, ".", path], capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


def parse_notation(notation: str):
    """'1333+2> 2<+3331' -> geometry for every drawer key index, row-major like the YAML."""
    left, right = notation.split()
    lcols, lthumbs = left.split("+")
    rthumbs, rcols = right.split("+")
    lcols = [int(c) for c in lcols]
    rcols = [int(c) for c in rcols]
    lt = int(lthumbs.rstrip("<>"))
    rt = int(rthumbs.rstrip("<>"))
    rows = max(max(lcols), max(rcols))

    def col_rows(count):
        top = (rows - count) // 2
        return set(range(top, top + count))

    keys = []
    for row in range(rows):
        for hand, cols, zmk in (("L", lcols, ZMK_LEFT), ("R", rcols, ZMK_RIGHT)):
            for col, count in enumerate(cols):
                if row in col_rows(count):
                    keys.append({"hand": hand, "row": row, "col": col, "zmk": zmk[col][row]})
    # thumbs: '2>' hugs the hand's inner edge (right side of the left hand), '2<' the left side
    lstart = len(lcols) - lt if lthumbs.endswith(">") else 0
    for i in range(lt):
        keys.append({"hand": "L", "row": rows, "col": lstart + i, "zmk": ZMK_THUMBS_LEFT[i], "thumb": True})
    rstart = 0 if rthumbs.endswith("<") else len(rcols) - rt
    for i in range(rt):
        keys.append({"hand": "R", "row": rows, "col": rstart + i, "zmk": ZMK_THUMBS_RIGHT[i], "thumb": True})
    for i, k in enumerate(keys):
        k["idx"] = i
        k["name"] = ZMK_NAMES.get(k["zmk"], "")
    return {"rows": rows, "left_cols": len(lcols), "right_cols": len(rcols), "keys": keys}


GLYPH_RE = re.compile(r"^\$\$(.+?)\$\$$")


def legend(value):
    """A legend is text or a $$glyph$$; return (text, glyph_id)."""
    if value is None:
        return "", None
    s = str(value)
    m = GLYPH_RE.match(s)
    if m:
        gid = m.group(1)
        return GLYPHS.get(gid, gid.split(":")[-1]), gid
    return s, None


def norm_key(raw):
    """Normalise one drawer key spec to {tap, hold, shifted, type, glyph}."""
    if raw is None:
        return {"tap": "", "hold": "", "shifted": "", "type": "blank"}
    if isinstance(raw, (str, int, float)):
        raw = {"t": raw}
    tap, glyph = legend(raw.get("t"))
    hold, _ = legend(raw.get("h"))
    shifted, _ = legend(raw.get("s"))
    ktype = raw.get("type", "")
    if tap == "▽":
        tap, ktype = "", "trans"
    key = {"tap": tap, "hold": hold, "shifted": shifted, "type": ktype or "key"}
    if glyph:
        key["glyph"] = glyph
    return key


def flatten(layer_rows):
    keys = []
    for row in layer_rows:
        if isinstance(row, list):
            keys.extend(norm_key(k) for k in row)
        else:
            keys.append(norm_key(row))  # thumbs are listed one per line
    return keys


def main() -> int:
    if not os.path.exists(SRC):
        print(f"error: {SRC} not found (set KEYBOARDS_REPO)", file=sys.stderr)
        return 1
    doc = load_yaml(SRC)
    layout = parse_notation(doc["layout"]["cols_thumbs_notation"])
    n = len(layout["keys"])

    layers = {}
    for name, rows in doc["layers"].items():
        keys = flatten(rows)
        if len(keys) != n:
            print(f"error: layer {name} has {len(keys)} keys, layout has {n}", file=sys.stderr)
            return 1
        layers[name] = keys

    # The drawer lists the Enter and Tab combos under "shortcuts" only (drawn separately),
    # but the firmware has them on nearly every layer: cb_enter (RHM RHR R0) on
    # ALL_LAYERS_WITH_ENTER, cb_tab (RTM RTR R0) on ALL_LAYERS (src/features/combos.dtsi).
    EVERYWHERE = ["alpha1", "alpha2", "shifted1", "shifted2", "ç-extension", "vim", "numbers",
                  "symbols", "nav", "media", "text", "shortcuts", "mehs", "func", "macros", "toggles"]
    COMBO_LAYERS = {(11, 12, 22): EVERYWHERE, (4, 5, 22): EVERYWHERE}
    combos = []
    for c in doc.get("combos", []):
        k = norm_key(c["k"])
        layers_ = COMBO_LAYERS.get(tuple(c["p"]), c.get("layers", []))
        combos.append({"positions": c["p"], "key": k, "layers": [l for l in layers_ if l in doc["layers"]]})

    # Activators: the key held (or tapped, for sticky layers) to reach a layer.
    activators = []
    seen = set()

    def add(layer, idx, kind):
        if layer in layers and (layer, idx, kind) not in seen:
            seen.add((layer, idx, kind))
            activators.append({"layer": layer, "idx": idx, "kind": kind})

    for idx, key in enumerate(layers["alpha1"]):
        if key["hold"] in layers:
            add(key["hold"], idx, "hold")
        if key["shifted"] == "sticky" and key["tap"] in layers:
            add(key["tap"], idx, "sticky")
        if key["shifted"] == "sticky" and key["tap"] == "⇧":
            add("shifted1", idx, "sticky")
    for name, keys in layers.items():
        for idx, key in enumerate(keys):
            if key["type"].startswith("held"):
                add(name, idx, "hold")
    # alpha2's own hold legend chain: ç-extension after ç
    for idx, key in enumerate(layers.get("alpha2", [])):
        if key["hold"] in layers:
            add(key["hold"], idx, "auto-sticky")

    # Sanity: base-layer letter combos match the documented ones.
    alpha1 = layers["alpha1"]
    for letter, expected in COMBO_SANITY.items():
        match = [c for c in combos if c["key"]["tap"] == letter and "alpha1" in c["layers"]]
        assert match, f"combo for {letter} not found"
        got = [alpha1[p]["tap"] for p in match[0]["positions"]]
        assert got == expected, f"combo {letter}: positions {match[0]['positions']} are {got}, expected {expected}"
    assert n == 24, n
    assert {k["zmk"] for k in layout["keys"]} == set(ZMK_NAMES), "zmk position map incomplete"
    for code, spec in CODES.items():
        for l in spec["layers"]:
            assert l in layers, (code, l)

    # ZMK layer ids (what the firmware signals) -> drawer layer + banner label.
    ids = parse_layer_ids(CONFIG)
    missing = sorted(set(ids) - set(ZMK_LAYERS))
    assert not missing, f"config.dtsi layers without a ZMK_LAYERS entry: {missing}"
    stale = sorted(set(ZMK_LAYERS) - set(ids))
    assert not stale, f"ZMK_LAYERS entries no longer in config.dtsi: {stale}"
    assert sorted(ids.values()) == list(range(len(ids))), f"layer ids are not 0..{len(ids) - 1}: {ids}"
    assert max(ids.values()) < SIGNAL["commit"] - SIGNAL["base"], "more layers than the signal can carry"
    zmk_layers = {}
    for name, lid in sorted(ids.items(), key=lambda kv: kv[1]):
        drawer, label, cls = ZMK_LAYERS[name]
        assert drawer is None or drawer in layers, (name, drawer)
        zmk_layers[str(lid)] = {"id": lid, "name": name, "drawer": drawer, "label": label, "cls": cls}

    out = {
        "source": os.path.relpath(SRC, os.path.expanduser("~")),
        "notation": doc["layout"]["cols_thumbs_notation"],
        "layout": layout,
        "layers": layers,
        "layer_order": list(layers.keys()),
        "combos": combos,
        "activators": activators,
        "codes": {str(k): v for k, v in CODES.items()},
        "zmk_layers": zmk_layers,
        "signal": SIGNAL,
    }
    os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    # Same data as a script, so index.html works from file:// (WKWebView, browsers) without fetch.
    with open(OUT[:-5] + ".js", "w", encoding="utf-8") as f:
        f.write("window.KEYMAP = " + json.dumps(out, ensure_ascii=False) + ";\n")
    print(f"wrote {os.path.relpath(OUT)}: {n} keys, {len(layers)} drawer layers, {len(zmk_layers)} ZMK layers, "
          f"{len(combos)} combos, {len(activators)} activators")
    return 0


if __name__ == "__main__":
    sys.exit(main())
