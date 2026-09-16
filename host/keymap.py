#!/usr/bin/env python3
"""Turn a keymap-drawer YAML into the HUD's keymap message, at runtime.

The HUD pages carry no keymap of their own. host/hudfeed.py loads a small config
(~/.config/zmk-layer-hud/config.yaml by default), converts the keymap-drawer file it names with
this module, sends {"kind":"keymap", ...} to the pages, and re-sends it whenever the YAML, the
config or the layer dtsi changes. Edit the drawer file, the HUD redraws.

The physical layout (key positions, sizes, rotation) comes from keymap-drawer itself
(`pip install keymap-drawer`), so every layout kind it supports works: cols_thumbs_notation,
ortho_layout, qmk_keyboard / zmk_keyboard / zmk_shared_layout from its database, qmk_info_json and
dts_layout files. Without the library only cols_thumbs_notation is understood.

Config keys (all paths may use ~):
  keymap:         path to the keymap-drawer YAML                                   (required)
  title:          text in the panel's corner (default: the YAML file name)        (optional)
  drawer_config:  keymap-drawer config YAML (key sizes, glyphs); defaults otherwise (optional)
  keyboard:       {vid, pid, name} of the keyboard to read the layer signal from    (optional)
  signal:         {base, commit} usages of the firmware node                        (optional)
  layers:         how ZMK layer ids map to drawer layers                            (optional)
      dtsi:  a devicetree header whose `// Layers` block has `#define NAME n` lines; the names
             become the ids' names. Without it, id n is the n-th layer of the YAML (what
             `keymap parse` produces from a .keymap).
      map:   per layer (by define name or id): a drawer layer name, null for a layer that is
             transparent or not drawn, or {drawer, label, class}.
  base:           the drawer layer that is always active (default: the layer for id 0)         (optional)
  combos:         [{positions, layers}] overrides for combos the drawer lists on fewer layers
                  than the firmware has them                                                    (optional)
  extras:         inference hints used only while a key cannot be placed on the live stack:
      sticky:            layers entered for one key (shown as one-shot)
      alpha2:            a secondary alpha layer typed letters may come from
      letter_combos_on:  layers on which letter-producing base-layer combos count
      search:            layer search order for unplaced keys (default: YAML order)

`python3 host/keymap.py [--config PATH] [--dump]` prints the message (or an error) for checking.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from itertools import chain

DEFAULT_CONFIG = os.path.expanduser("~/.config/zmk-layer-hud/config.yaml")
SIGNAL = {"base": 0xC0, "commit": 0xDF}

# keymap-drawer glyph names -> text the HUD can show. The glyph id is kept too.
GLYPHS = {
    "mdi:keyboard-space": "␣", "mdi:repeat": "↻", "mdi:apple-keyboard-shift": "⇧",
    "mdi:apple-keyboard-control": "⌃", "mdi:apple-keyboard-option": "⌥", "mdi:apple-keyboard-command": "⌘",
    "shift_command": "⇧⌘", "mdi:backspace-outline": "⌫", "mdi:backspace-reverse-outline": "⌦",
    "mdi:keyboard-return": "↵", "mdi:keyboard-tab": "⇥", "mdi:arrow-left": "←", "mdi:arrow-right": "→",
    "mdi:arrow-up": "↑", "mdi:arrow-down": "↓", "mdi:format-horizontal-align-left": "⇱",
    "mdi:format-horizontal-align-right": "⇲", "mdi:numeric": "#", "mdi:bluetooth": "BT",
    "mdi:bluetooth-off": "BT✕", "mdi:lock": "🔒", "mdi:power-sleep": "zz", "mdi:flash": "⚡",
    "mdi:mouse-left-click": "🖱L", "mdi:mouse-right-click": "🖱R", "mdi:volume-medium": "🔉",
    "mdi:volume-high": "🔊", "mdi:microphone": "🎤", "mdi:camera": "📷", "mdi:play-pause": "⏯",
    "mdi:skip-backward": "⏮", "mdi:skip-forward": "⏭", "mdi:content-copy": "⎘", "mdi:content-paste": "📋",
    "mdi:magnify": "🔍", "mdi:undo": "↶", "mdi:select-all": "⊞", "mdi:content-save": "💾",
    "mdi:content-save-check": "💾✓", "mdi:fullscreen": "⛶", "mdi:calculator": "🧮", "mdi:folder-open": "📁",
    "mdi:web": "🌐", "mdi:microsoft-visual-studio-code": "⌨", "mdi:console-line": ">_",
    "mdi:note-multiple-outline": "🗒", "mdi:ray-start-arrow": "⇢", "mdi:emoticon-happy-outline": "☺",
    "mdi:open-in-new": "⧉", "mdi:close": "✕", "mdi:refresh": "⟳", "mdi:backup-restore": "⟲",
    "mdi:content-cut": "✂", "mdi:docker": "🐳", "mdi:arrow-down-circle-outline": "▽",
    "mdi:hand-back-right-outline": "✋", "mdi:loupe": "⌕", "mdi:arrow-up-bold-outline": "⇞",
    "mdi:arrow-down-bold-outline": "⇟", "mdi:keyboard-esc": "⎋", "mdi:apple-keyboard-caps": "⇪",
    "mdi:keyboard-caps": "⇪", "tabler:letter-case": "Aa", "mdi:home": "⇱", "mdi:page-first": "⇱",
    "mdi:page-last": "⇲", "mdi:chevron-double-up": "⇞", "mdi:chevron-double-down": "⇟",
    "mdi:apps": "⊞", "mdi:cog": "⚙", "mdi:tab-search": "⌕⇥", "mdi:layers-search-outline": "⌕▤",
    "mdi:toggle-switch": "⏻", "mdi:controller": "🎮", "mdi:wrench": "🔧", "mdi:camera-flip": "📷⇄",
    "mdi:window-maximize": "⛶", "mdi:window-restore": "❐", "mdi:monitor": "🖥", "mdi:keyboard": "⌨",
    "mdi:brightness-6": "☼", "mdi:music": "♫", "mdi:clock-outline": "⏱", "mdi:calendar": "📅",
}
GLYPH_RE = re.compile(r"^\$\$(.+?)\$\$$")
DEFINE_RE = re.compile(r"^\s*#define\s+([A-Za-z_][A-Za-z0-9_]*)\s+(\d+)\s*$")


class KeymapError(Exception):
    pass


# ---------- small pieces (pure, tested) ----------

def expand(path):
    return os.path.expanduser(os.path.expandvars(path)) if isinstance(path, str) else path


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
    """One drawer key spec (string, number, null or dict with keymap-drawer's field names or
    aliases) -> {tap, hold, shifted, type[, glyph, left, right]}."""
    if raw is None:
        return {"tap": "", "hold": "", "shifted": "", "type": "blank"}
    if isinstance(raw, (str, int, float)):
        raw = {"t": raw}
    if not isinstance(raw, dict):
        raise KeymapError(f"invalid key spec {raw!r}")
    tap, glyph = legend(raw.get("t", raw.get("tap", raw.get("center"))))
    hold, _ = legend(raw.get("h", raw.get("hold", raw.get("bottom"))))
    shifted, _ = legend(raw.get("s", raw.get("shifted")))
    ktype = raw.get("type", "") or ""
    if tap == "▽":
        tap, ktype = "", "trans"
    key = {"tap": tap, "hold": hold, "shifted": shifted, "type": ktype or "key"}
    if glyph:
        key["glyph"] = glyph
    for side in ("left", "right"):
        if raw.get(side):
            key[side] = legend(raw[side])[0]
    return key


def flatten(rows):
    """Layer rows (lists, or single keys one per line) -> flat list of normalised keys."""
    out = []
    for row in rows:
        if isinstance(row, list):
            out.extend(norm_key(k) for k in row)
        else:
            out.append(norm_key(row))
    return out


def parse_layer_ids(text):
    """The `// Layers` block of a config.dtsi (consecutive `#define NAME n` lines, blank lines
    allowed) -> {NAME: n}."""
    lines = text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip().startswith("//") and "layers" in l.lower())
    except StopIteration:
        raise KeymapError("no '// Layers' block in the dtsi")
    ids = {}
    for line in lines[start + 1:]:
        if not line.strip():
            continue
        m = DEFINE_RE.match(line)
        if not m:
            break
        ids[m.group(1)] = int(m.group(2))
    if not ids:
        raise KeymapError("the '// Layers' block has no #define lines")
    return ids


def prettify(name):
    return name.replace("_", " ").strip().capitalize()


def cpt_layout(spec, key_w=70.0, key_h=68.0, split_gap=30.0):
    """Fallback for keymap-drawer's cols_thumbs_notation ("1333+2> 2<+3331"): centred key
    rectangles in the drawer's coordinate system. Used only when keymap_drawer is not installed."""
    parts = [p for p in re.split(r"[ _]+", spec) if p]
    # Column heights across every part decide the row grid (the drawer's max_rows).
    thumbs_re = re.compile(r"^\d[><lr]*$")

    def split_part(part):
        """-> (column specs, thumbs spec or None, thumbs after the columns?)"""
        if "+" not in part:
            return re.findall(r"\d[v^ud]*", part), None, True
        a, b = part.split("+", 1)
        if thumbs_re.match(a):  # "2<+3331": thumbs before the columns (right hand)
            return re.findall(r"\d[v^ud]*", b), a, False
        return re.findall(r"\d[v^ud]*", a), b, True  # "1333+2>": thumbs after the columns (left hand)

    parsed = [split_part(p) for p in parts]
    max_rows = max(int(c[0]) for cols, _, _ in parsed for c in cols)
    keys = []  # (row, part index, x, y) in key units, mirroring CPTLayout.generate
    x_offset = 0.0
    for part_ind, (cols, thumbs_spec, thumbs_left) in enumerate(parsed):
        pts = []
        for col, c_spec in enumerate(cols):
            count = int(c_spec[0])
            shift = c_spec.count("v") + c_spec.count("d") - c_spec.count("^") - c_spec.count("u")
            y_top = (max_rows - count + shift) / 2  # short columns are centred (half rows allowed)
            pts += [(col, y_top + i) for i in range(count)]
        if thumbs_spec:
            n = int(re.match(r"\d", thumbs_spec).group())
            shift = (thumbs_spec.count(">") + thumbs_spec.count("r") - thumbs_spec.count("<") - thumbs_spec.count("l")) / 2
            x_left = (len(cols) - n if thumbs_left else 0) + shift
            pts += [(x_left + i, max_rows) for i in range(n)]
        # Each part is normalised to start at (0, 0), so a thumb shifted past the edge moves the
        # whole hand; parts are then placed one key apart.
        min_x, min_y = min(p[0] for p in pts), min(p[1] for p in pts)
        pts = [(x - min_x, y - min_y) for x, y in pts]
        for x, y in pts:
            keys.append((int(y), part_ind, (x + 0.5 + x_offset) * key_w + part_ind * split_gap, (y + 0.5) * key_h))
        x_offset += max(p[0] for p in pts) + 1
    # The drawer's order: row, then part (hand), then column.
    keys.sort(key=lambda k: (k[0], k[1], k[2]))
    ordered = [{"x": x, "y": y, "w": key_w, "h": key_h, "r": 0} for _, _, x, y in keys]
    width = max(k["x"] + k["w"] / 2 for k in ordered)
    height = max(k["y"] + k["h"] / 2 for k in ordered)
    return {"width": width, "height": height, "keys": ordered}


def physical_layout(layout_spec, drawer_cfg):
    """keymap-drawer `layout` mapping -> {width, height, keys:[{x,y,w,h,r}]} with centred keys.
    Uses keymap_drawer when importable; else handles cols_thumbs_notation only."""
    if not isinstance(layout_spec, dict) or not layout_spec:
        raise KeymapError("the keymap YAML needs a `layout` mapping (cols_thumbs_notation, ortho_layout, "
                          "qmk_keyboard, zmk_keyboard, ...)")
    try:
        from keymap_drawer.config import Config
        from keymap_drawer.physical_layout import PhysicalLayoutGenerator
    except ImportError:
        if "cols_thumbs_notation" in layout_spec:
            dc = (drawer_cfg or {}).get("draw_config", {}) if drawer_cfg else {}
            return cpt_layout(layout_spec["cols_thumbs_notation"], dc.get("key_w", 70), dc.get("key_h", 68),
                              dc.get("split_gap", 30))
        raise KeymapError("this layout kind needs keymap-drawer: pip install keymap-drawer")
    cfg = Config.model_validate(drawer_cfg) if drawer_cfg else Config()
    try:
        layout = PhysicalLayoutGenerator(config=cfg, **layout_spec).generate().normalize()
    except Exception as e:  # pydantic / lookup errors carry the useful text
        raise KeymapError(f"keymap-drawer could not build the physical layout: {e}") from e
    keys = [{"x": k.pos.x, "y": k.pos.y, "w": k.width, "h": k.height, "r": k.rotation} for k in layout.keys]
    return {"width": layout.width, "height": layout.height, "keys": keys}


def parse_layers_and_combos(doc, n_keys):
    """Layers and combos from the YAML dict. With keymap_drawer, combos given as trigger keys are
    resolved to positions; without it they are skipped."""
    raw_layers = doc.get("layers") or {}
    if not raw_layers:
        raise KeymapError("the keymap YAML has no layers")
    layers = {}
    for name, rows in raw_layers.items():
        keys = flatten(rows)
        if len(keys) != n_keys:
            raise KeymapError(f"layer {name} has {len(keys)} keys, the layout has {n_keys}")
        layers[name] = keys
    combos = []
    raw_combos = doc.get("combos") or []
    resolved = None
    try:
        from keymap_drawer.keymap import KeymapData
        kd = KeymapData(layers=raw_layers, combos=raw_combos, layout=None, config=None)
        resolved = [(c.key_positions, {"t": c.key.tap, "h": c.key.hold, "s": c.key.shifted, "type": c.key.type},
                     c.layers) for c in kd.combos]
    except ImportError:
        pass
    except Exception as e:
        raise KeymapError(f"keymap-drawer rejected the combos: {e}") from e
    if resolved is None:
        resolved = []
        for c in raw_combos:
            positions = c.get("p", c.get("key_positions"))
            if not positions:
                continue  # trigger-key combos need keymap-drawer to resolve
            resolved.append((positions, c.get("k", c.get("key")), c.get("l", c.get("layers")) or []))
    for positions, key, lyrs in resolved:
        if any(p >= n_keys for p in positions):
            raise KeymapError(f"combo positions {positions} exceed the {n_keys} keys of the layout")
        combos.append({"positions": list(positions), "key": norm_key(key),
                       "layers": [l for l in (lyrs or list(layers)) if l in layers]})
    return layers, combos


def zmk_layer_table(layers_cfg, layer_names, dtsi_text):
    """ZMK layer id -> {id, name, drawer, label, cls}. Ids come from the dtsi when given, else from
    the YAML order (name = drawer layer). The config's `map` refines drawer/label/class."""
    if dtsi_text:
        ids = parse_layer_ids(dtsi_text)
    else:
        ids = {name: i for i, name in enumerate(layer_names)}
    lower = {n.lower(): n for n in layer_names}
    overrides = {}
    for key, val in ((layers_cfg or {}).get("map") or {}).items():
        overrides[str(key)] = val
    table = {}
    for name, lid in sorted(ids.items(), key=lambda kv: kv[1]):
        spec = overrides.get(name, overrides.get(str(lid), "__unset__"))
        drawer = lower.get(name.lower())
        label, cls = prettify(name), ("off" if lid == 0 else "momentary")
        if spec != "__unset__":
            if spec is None or isinstance(spec, str):
                drawer = spec
            elif isinstance(spec, dict):
                drawer = spec.get("drawer", drawer)
                label = spec.get("label", label)
                cls = spec.get("class", spec.get("cls", cls))
            else:
                raise KeymapError(f"layers.map.{name}: expected a layer name, null or a mapping")
        if drawer is not None and drawer not in layer_names:
            raise KeymapError(f"layers.map.{name}: drawer layer {drawer!r} is not in the keymap YAML")
        table[str(lid)] = {"id": lid, "name": name, "drawer": drawer, "label": label, "cls": cls}
    return table


def activators(layers, extras):
    """Keys that reach a layer: a hold legend naming a layer, a `type: held` key on the layer
    itself, and (Diamond convention) `s: sticky` keys whose tap legend names a layer."""
    out, seen = [], set()

    def add(layer, idx, kind):
        if layer in layers and (layer, idx, kind) not in seen:
            seen.add((layer, idx, kind))
            out.append({"layer": layer, "idx": idx, "kind": kind})

    for name, keys in layers.items():
        for idx, key in enumerate(keys):
            if key["hold"] in layers and key["hold"] != name:
                add(key["hold"], idx, "auto-sticky" if name != (extras or {}).get("base") and name in ((extras or {}).get("sticky") or []) else "hold")
            if key["shifted"] == "sticky" and key["tap"] in layers:
                add(key["tap"], idx, "sticky")
            if key["type"].startswith("held"):
                add(name, idx, "hold")
    return out


def build_message(cfg, doc, drawer_cfg=None, dtsi_text=None, source=""):
    """Everything the page needs, from parsed config + keymap YAML dicts."""
    layout = physical_layout(doc.get("layout"), drawer_cfg)
    n = len(layout["keys"])
    layers, combos = parse_layers_and_combos(doc, n)
    layer_names = list(layers)
    for override in cfg.get("combos") or []:
        positions = list(override.get("positions", []))
        for c in combos:
            if c["positions"] == positions:
                c["layers"] = [l for l in override.get("layers", []) if l in layers]
    zmk_layers = zmk_layer_table(cfg.get("layers"), layer_names, dtsi_text)
    base = cfg.get("base") or (zmk_layers.get("0") or {}).get("drawer") or layer_names[0]
    if base not in layers:
        raise KeymapError(f"base layer {base!r} is not in the keymap YAML")
    extras = dict(cfg.get("extras") or {})
    extras.setdefault("base", base)
    extras.setdefault("sticky", [])
    extras.setdefault("search", [l for l in layer_names if l != base])
    for key in ("sticky", "search", "letter_combos_on"):
        bad = [l for l in extras.get(key) or [] if l not in layers]
        if bad:
            raise KeymapError(f"extras.{key}: unknown layers {bad}")
    if extras.get("alpha2") and extras["alpha2"] not in layers:
        raise KeymapError(f"extras.alpha2: unknown layer {extras['alpha2']!r}")
    signal = dict(SIGNAL)
    signal.update({k: int(v) for k, v in (cfg.get("signal") or {}).items()})
    return {
        "kind": "keymap",
        "source": source,
        "title": cfg.get("title") or "",
        "layout": layout,
        "layers": layers,
        "layer_order": layer_names,
        "combos": combos,
        "activators": activators(layers, extras),
        "zmk_layers": zmk_layers,
        "base": base,
        "extras": extras,
        "signal": signal,
    }


# ---------- files ----------

def load_yaml(path):
    """PyYAML when available (it comes with keymap-drawer), else Mike Farah's yq or the jq wrapper."""
    try:
        import yaml
    except ImportError:
        yaml = None
    if yaml is not None:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    import shutil
    import subprocess
    if not shutil.which("yq"):
        raise KeymapError("PyYAML is required (pip install keymap-drawer brings it, or pip install pyyaml); "
                          "yq would also do")
    version = subprocess.run(["yq", "--version"], capture_output=True, text=True).stdout.lower()
    args = ["-o=json"] if "mikefarah" in version else []
    res = subprocess.run(["yq", *args, ".", path], capture_output=True, text=True)
    if res.returncode != 0:
        raise KeymapError(f"yq could not read {path}: {res.stderr.strip()}")
    return json.loads(res.stdout) or {}


def find_config(path=None):
    for candidate in (path, os.environ.get("ZMKHUD_CONFIG"), DEFAULT_CONFIG):
        if candidate and os.path.exists(expand(candidate)):
            return expand(candidate)
    raise KeymapError(f"no config: pass --config, set ZMKHUD_CONFIG, or create {DEFAULT_CONFIG} "
                      "(see config/example.yaml and config/diamond.yaml in zmk-layer-hud)")


class KeymapSource:
    """Loads config + keymap files and knows when any of them changed."""

    def __init__(self, config_path=None):
        self.config_path = find_config(config_path)
        self.cfg = {}
        self.paths = []
        self.mtimes = {}
        self.message = None

    def _watch(self):
        cfg = load_yaml(self.config_path)
        if not isinstance(cfg, dict) or not cfg.get("keymap"):
            raise KeymapError(f"{self.config_path}: `keymap:` (path to a keymap-drawer YAML) is required")
        paths = [self.config_path, expand(cfg["keymap"])]
        if cfg.get("drawer_config"):
            paths.append(expand(cfg["drawer_config"]))
        if (cfg.get("layers") or {}).get("dtsi"):
            paths.append(expand(cfg["layers"]["dtsi"]))
        return cfg, paths

    def changed(self):
        try:
            _, paths = self._watch()
        except Exception:
            paths = self.paths
        now = {}
        for p in paths:
            try:
                now[p] = os.stat(p).st_mtime_ns
            except OSError:
                now[p] = None
        if now != self.mtimes:
            self.mtimes = now
            return True
        return False

    def load(self):
        """(Re)build the message; raises KeymapError with a readable reason."""
        cfg, paths = self._watch()
        self.cfg, self.paths = cfg, paths
        doc = load_yaml(paths[1])
        drawer_cfg = load_yaml(expand(cfg["drawer_config"])) if cfg.get("drawer_config") else None
        dtsi_text = None
        if (cfg.get("layers") or {}).get("dtsi"):
            with open(expand(cfg["layers"]["dtsi"]), encoding="utf-8") as f:
                dtsi_text = f.read()
        self.message = build_message(cfg, doc, drawer_cfg, dtsi_text, source=os.path.relpath(paths[1], os.path.expanduser("~")))
        self.changed()  # snapshot mtimes after a successful load
        return self.message


def main(argv=None):
    p = argparse.ArgumentParser(description="Convert the configured keymap-drawer YAML to the HUD's keymap message.")
    p.add_argument("--config", help=f"config file (default: $ZMKHUD_CONFIG or {DEFAULT_CONFIG})")
    p.add_argument("--dump", action="store_true", help="print the full JSON message (default: a summary)")
    args = p.parse_args(argv)
    try:
        src = KeymapSource(args.config)
        msg = src.load()
    except KeymapError as e:
        print(f"keymap: {e}", file=sys.stderr)
        return 1
    if args.dump:
        json.dump(msg, sys.stdout, ensure_ascii=False)
        print()
    else:
        print(f"{src.config_path}: {msg['source']}: {len(msg['layout']['keys'])} keys, {len(msg['layers'])} drawer layers, "
              f"{len(msg['zmk_layers'])} ZMK layers, {len(msg['combos'])} combos, {len(msg['activators'])} activators, "
              f"base {msg['base']!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
