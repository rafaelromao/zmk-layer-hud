"""zmk-layer-hud import / sync — write the HUD's definitions, so the HUD reads nothing else.

At runtime the HUD reads two files: its config, and the definitions written beside it
(`config.yaml` -> `config.definitions.json`). `import` writes the definitions; `sync` writes them
again from the same sources and says what changed. They hold two things:

- the drawing -- every key where it sits, every layer's legends, the combos, the glyphs -- made
  with keymap-drawer from the keymap-drawer YAML the config names as `keymap:`, or, when it names
  none, from the keyboard's own ZMK keymap (`keymap parse`);
- when a ZMK repo was imported, what its keymap says and a drawing does not: the id of every layer,
  the layers each combo really fires on, the combo term and the idle a combo needs before it.

    zmk-layer-hud import github.com/you/zmk-config       # or a path to a working copy
    zmk-layer-hud import ~/zmk-config --keyboard corne   # which keyboard, when it holds several
    zmk-layer-hud import                                 # no repo: draw from `keymap:` alone
    zmk-layer-hud sync                                   # read the recorded sources again

This is the only part of zmk-layer-hud that reads the keymap-drawer file, the drawer config, the ZMK
keymap or the network; the HUD itself reads its config and these definitions. The config stays
yours: anything set there wins over what was imported, and a sync never touches it. What a repo's
keymap says is also written to be read, in `config.imported.yaml`. When the drawing is a
keymap-drawer file, the one thing import cannot know is which drawn layer shows which ZMK layer --
your names, not the keymap's -- so it drafts that mapping there and marks the lines it had to leave
undecided; correct those once in the config.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import keymap as keymap_mod  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Where a repo given by URL is kept between syncs. $ZMKHUD_CACHE moves it.
CACHE = os.path.expanduser(os.environ.get("ZMKHUD_CACHE") or "~/.cache/zmk-layer-hud/repos")
# Where it is all written: keymap.py decides, so the two cannot disagree.
imported_path = keymap_mod.imported_path
definitions_path = keymap_mod.definitions_path
# Directories a keyboard's own files are never in: a zmk-config that carries ZMK or its modules as
# submodules has ZMK's physical layouts for other boards all through them.
NOT_THE_BOARDS = (".git", ".claude", "build", "node_modules", "modules", "zmk", "zephyr")


class SyncError(Exception):
    pass


# ---------- the repo ----------

def resolve_source(spec):
    """A working copy to read. A path is used where it is; a URL is cloned into the cache and
    fetched on every later sync, so a sync sees what was pushed."""
    local = os.path.expanduser(spec)
    if os.path.isdir(local):
        return os.path.abspath(local), "path"
    url = spec if "://" in spec or spec.startswith("git@") else f"https://{spec}"
    if not shutil.which("git"):
        raise SyncError("git is needed to read a repository by URL; pass a path to a working copy instead")
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", url.rstrip("/").removesuffix(".git").split("/")[-1])
    dest = os.path.join(CACHE, name)
    os.makedirs(CACHE, exist_ok=True)
    if os.path.isdir(os.path.join(dest, ".git")):
        run(["git", "-C", dest, "fetch", "--quiet", "--depth", "1", "origin", "HEAD"])
        run(["git", "-C", dest, "checkout", "--quiet", "--force", "FETCH_HEAD"])
    else:
        run(["git", "clone", "--quiet", "--depth", "1", url, dest])
    return dest, "url"


def run(cmd, **kw):
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)
    except FileNotFoundError:
        raise SyncError(f"{cmd[0]} not found")
    except subprocess.CalledProcessError as e:
        raise SyncError(f"{' '.join(cmd[:3])} failed: {(e.stderr or e.stdout or '').strip().splitlines()[-1:] or ['(no output)']}"[:400])


def find_keymap(root, keyboard=None):
    """The board's .keymap. Named with --keyboard, or the only one in the repo."""
    found = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".git", ".claude", "build", "node_modules")]
        for f in files:
            if f.endswith(".keymap"):
                found.append(os.path.join(base, f))
    if keyboard:
        want = keyboard.lower()
        # Exact first: on a repo with diamond, choc_diamond and wired_diamond, "diamond" means the
        # one actually called that, not all three.
        hit = [p for p in found if os.path.basename(p)[: -len(".keymap")].lower() == want] \
            or [p for p in found if want in os.path.basename(p).lower() or want in p.lower()]
        if not hit:
            raise SyncError(f"no .keymap matching {keyboard!r}; the repo has: "
                            + ", ".join(sorted(os.path.basename(p)[:-7] for p in found)))
        if len(hit) > 1:
            raise SyncError(f"{keyboard!r} matches several: " + ", ".join(sorted(os.path.basename(p) for p in hit)))
        return hit[0]
    if not found:
        raise SyncError(f"no .keymap under {root}")
    if len(found) > 1:
        raise SyncError("the repo holds several keyboards; name one with --keyboard: "
                        + ", ".join(sorted(os.path.basename(p)[:-7] for p in found)))
    return found[0]


# ---------- what the keymap says ----------

INCLUDE_RE = re.compile(r'#include\s+"([^"]+)"')
DISPLAY_RE = re.compile(r'display-name\s*=\s*"([^"]*)"')


def layer_names(keymap_path, depth=6):
    """The display name of every layer, in the keymap's own node order, read with a regex: what
    layer_list falls back to when keymap-drawer's devicetree reader cannot read the keymap.

    Read as a list rather than a mapping on purpose: two layers may share a display name (a copy
    of a layer reached another way), and a mapping would drop one and shift every id after it.
    """
    seen = set()

    def walk(path, left):
        if left < 0 or path in seen or not os.path.isfile(path):
            return None
        seen.add(path)
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        at = text.find("keymap {")
        if at >= 0:
            names = DISPLAY_RE.findall(text[at:])
            if names:
                return names
        for rel in INCLUDE_RE.findall(text):
            got = walk(os.path.normpath(os.path.join(os.path.dirname(path), rel)), left - 1)
            if got:
                return got
        return None

    names = walk(os.path.abspath(keymap_path), depth)
    if not names:
        raise SyncError(f"no layers with a display-name reachable from {keymap_path}; "
                        "the HUD needs them to name each ZMK layer")
    return names


DEFINE_RE = re.compile(r"^\s*#define\s+([A-Za-z_][A-Za-z0-9_]*)\s+(\d+)\s*$", re.M)
TIMEOUT_RE = re.compile(r"timeout-ms\s*=\s*<\s*([A-Za-z_][A-Za-z0-9_]*|\d+)\s*>")


def reachable(keymap_path, depth=8):
    """Every file the keymap pulls in, itself first. Whatever is being looked for may be several
    includes away — the combo term is written in one file and defined in another."""
    seen, out = set(), []

    def walk(path, left):
        path = os.path.abspath(path)
        if left < 0 or path in seen or not os.path.isfile(path):
            return
        seen.add(path)
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        out.append((path, text))
        for rel in INCLUDE_RE.findall(text):
            walk(os.path.join(os.path.dirname(path), rel), left - 1)

    walk(keymap_path, depth)
    return out


def combo_term(keymap_path):
    """The keyboard's combo timeout in ms, or None when the keymap does not say.

    The HUD groups the presses that arrive within this into one combo, so it has to be the
    keyboard's own number: too short and a real chord is missed, too long and two quick keystrokes
    become one. ZMK allows a timeout per combo; the HUD has one, so the commonest wins.
    """
    files = reachable(keymap_path)
    defines = {}
    for _, text in files:
        for m in DEFINE_RE.finditer(text):
            defines.setdefault(m.group(1), int(m.group(2)))
    found = {}
    for _, text in files:
        for m in TIMEOUT_RE.finditer(text):
            token = m.group(1)
            value = int(token) if token.isdigit() else defines.get(token)
            if value:
                found[value] = found.get(value, 0) + 1
    if not found:
        return None
    return max(found.items(), key=lambda kv: (kv[1], -kv[0]))[0]


def device_tree(keymap_path):
    """The keymap as keymap-drawer's parser reads it, preprocessor and all, or None when
    keymap-drawer is not installed or cannot read it."""
    try:
        from keymap_drawer.config import ParseConfig
        from keymap_drawer.dts import DeviceTree
        from keymap_drawer.parse import zmk as drawer_zmk
        with open(keymap_path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        with open(drawer_zmk.ZMK_DEFINES_PATH, encoding="utf-8") as f:
            defines = f.read()
        return DeviceTree(text, keymap_path, True, preamble=ParseConfig().zmk_preamble + "\n" + defines)
    except Exception:
        return None


def combo_idle(keymap_path, dts=None):
    """How long a combo needs the keyboard to have been idle before it (ZMK's per-combo
    require-prior-idle-ms), or None when no combo says.

    A chord struck sooner than that after another key is not a combo in ZMK -- its keys are typed
    one by one, which is what makes a fast roll over a combo's keys come out as letters -- and the
    HUD has to draw it the same way. Read from the combo nodes alone, after the preprocessor has
    expanded them: hold-taps take a property of the same name for something else, and a keymap
    that writes its combos through macros only has them once they are expanded. The commonest
    wins, as for the combo term: the HUD keeps one."""
    dts = dts or device_tree(keymap_path)
    if dts is None:
        return None
    try:
        nodes = [n for p in dts.get_compatible_nodes("zmk,combos") for n in p.children]
    except Exception:   # a keymap its parser cannot read: say nothing
        return None
    found = {}
    for node in nodes:
        values = node.get_array("require-prior-idle-ms") or []
        if values and values[0].isdigit() and int(values[0]) > 0:
            found[int(values[0])] = found.get(int(values[0]), 0) + 1
    if not found:
        return None
    return max(found.items(), key=lambda kv: (kv[1], -kv[0]))[0]


def studio(root, keymap_path):
    """Whether the keyboard's firmware keeps reserved layers in place: ZMK Studio, or layer
    reordering, which Studio selects. Only then does a `status = "reserved"` layer keep an id."""
    board = os.path.basename(keymap_path)[: -len(".keymap")].lower()
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in NOT_THE_BOARDS]
        for f in files:
            if f.endswith(".conf") and (board in f.lower() or len([x for x in files if x.endswith(".conf")]) == 1):
                with open(os.path.join(base, f), encoding="utf-8", errors="replace") as fh:
                    if re.search(r"^\s*CONFIG_ZMK_(STUDIO|KEYMAP_LAYER_REORDERING)\s*=\s*y", fh.read(), re.M):
                        return True
    return False


def layer_list(keymap_path, root, dts=None, log=None):
    """The keyboard's ZMK layers in id order, as [{name, drawn}]: `name` as keymap-drawer names a
    layer (its label or display-name, else its node name), and `drawn` false for a reserved layer,
    which `keymap parse` leaves out. A reserved layer has an id only on firmware that keeps
    reserved layers in place (studio); elsewhere it has none, and the ids after it move up."""
    dts = dts or device_tree(keymap_path)
    nodes = None
    if dts is not None:
        try:
            nodes = [n for p in dts.get_compatible_nodes("zmk,keymap") for n in p.children]
        except Exception:
            nodes = None
    if not nodes:
        return [{"name": n, "drawn": True} for n in layer_names(keymap_path)]
    keeps = studio(root, keymap_path)
    out = []
    for node in nodes:
        name = node.get_string("label|display-name") or node.name.removeprefix("layer_").removesuffix("_layer")
        reserved = node.get_string("status") == "reserved"
        if reserved and not keeps:
            continue
        out.append({"name": name, "drawn": not reserved})
    if log and any(not l["drawn"] for l in out[:-1]) and any(l["drawn"] for l in out):
        log("a reserved layer sits between live ones; its id is kept because the firmware keeps it in place "
            "(ZMK Studio, or CONFIG_ZMK_KEYMAP_LAYER_REORDERING)")
    return out


def unique_names(names):
    """Every name once: a second NUMBERS becomes "NUMBERS 2". A drawing keys its layers by name, and
    keymap-drawer's parser would otherwise fold two layers of one name into one."""
    seen, out = {}, []
    for n in names:
        seen[n] = seen.get(n, 0) + 1
        out.append(n if seen[n] == 1 else f"{n} {seen[n]}")
    return out


def keymap_parse_cmd(keymap_path, drawer_config=None, names=None):
    """`keymap parse` as keymap-drawer's CLI wants it: `-c` is its global flag, before the
    subcommand; after `parse` it would be --columns."""
    exe = shutil.which("keymap", path=os.path.dirname(sys.executable)) or shutil.which("keymap")
    if not exe:
        raise SyncError("keymap-drawer is needed to read the keymap (make venv, or pip install keymap-drawer)")
    cmd = [exe] + (["-c", drawer_config] if drawer_config else []) + ["parse", "-z", keymap_path]
    if names:
        cmd += ["-l"] + list(names)
    return cmd


def parse_keymap(keymap_path, drawer_config=None, names=None):
    """keymap-drawer's own ZMK parser, as a keymap-drawer YAML: every layer's bindings as legends,
    and the combos with the layers they really fire on."""
    out = run(keymap_parse_cmd(keymap_path, drawer_config, names)).stdout
    try:
        import yaml
    except ImportError:
        raise SyncError("PyYAML is needed (make venv)")
    return yaml.safe_load(out) or {}


# ---------- the physical layout, for a drawing made from the keymap ----------

LAYOUT_NODE_RE = re.compile(r'compatible\s*=\s*"zmk,physical-layout"')
CHOSEN_LAYOUT_RE = re.compile(r"zmk,physical-layout\s*=\s*&([A-Za-z_][A-Za-z0-9_]*)")
SHARED_RE = re.compile(r"#include\s*<layouts/([^>]+?)\.dtsi>")


def boards_files(root, keymap_path):
    """The keyboard's own devicetree files: beside its .keymap, and in any directory named after
    it (the exact name before a longer one), never inside a submodule of ZMK or its modules."""
    board = os.path.basename(keymap_path)[: -len(".keymap")].lower()
    exact, near, beside = [], [], []
    here = os.path.dirname(os.path.abspath(keymap_path))
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in NOT_THE_BOARDS]
        name = os.path.basename(base).lower()
        into = exact if name == board else near if board in name else beside if os.path.abspath(base) == here else None
        if into is None:
            continue
        into += [os.path.join(base, f) for f in sorted(files) if f.endswith((".dtsi", ".overlay", ".keymap"))]
    return exact or near or [], beside


def dts_layouts(path):
    """{layout name: key count} of the zmk,physical-layout nodes in one devicetree file, read by
    keymap-drawer, or {} when it cannot read them."""
    try:
        from keymap_drawer.config import ParseConfig
        from keymap_drawer.physical_layout import _parse_dts_layout
        from pathlib import Path
        qmk = _parse_dts_layout(Path(path), ParseConfig())
        return {name: len(keys) for name, keys in qmk.layouts.items()}
    except Exception:
        return {}


def layout_for_keymap(cfg, config_path, root, keymap_path, parsed):
    """The keymap-drawer layout spec a drawing made from the ZMK keymap is drawn on: the config's
    `layout:`; else a zmk,physical-layout node in the keyboard's own files -- the one the keymap or
    the board chooses, else the one with as many keys as the keymap has bindings; else ZMK's shared
    layout the board includes; else keymap-drawer's guess from the keyboard's name."""
    if cfg.get("layout"):
        spec = dict(cfg["layout"])
        base = os.path.dirname(os.path.abspath(config_path))
        for key in ("qmk_info_json", "dts_layout"):
            if isinstance(spec.get(key), str):
                spec[key] = keymap_mod.expand(spec[key], base)
        return spec
    guess = parsed.get("layout") or {}
    bindings = len(next(iter((parsed.get("layers") or {}).values()), []))
    own, beside = boards_files(root, keymap_path)
    files = own + beside
    candidates = []            # (file, layout name, key count)
    chosen = guess.get("layout_name")
    for path in files:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        if LAYOUT_NODE_RE.search(text):
            candidates += [(path, name, n) for name, n in dts_layouts(path).items()]
        if chosen is None and (m := CHOSEN_LAYOUT_RE.search(text)):
            chosen = m.group(1)
    if candidates:
        pick = [c for c in candidates if chosen and c[1] == chosen] or [c for c in candidates if c[2] == bindings]
        if len(pick) == 1 or (pick and len({c[:2] for c in pick}) == 1):
            path, name, _ = pick[0]
            return {"dts_layout": path, "layout_name": name}
        raise SyncError("the keyboard's files hold several physical layouts ("
                        + ", ".join(f"{c[1]} ({c[2]} keys, {os.path.relpath(c[0], root)})" for c in candidates)
                        + "); say which with `layout:` in the config, e.g. "
                        "{dts_layout: path/to/it.dtsi, layout_name: NAME}")
    for path in files:
        with open(path, encoding="utf-8", errors="replace") as f:
            if m := SHARED_RE.search(f.read()):
                return {"zmk_shared_layout": m.group(1)}
    if guess.get("zmk_keyboard"):
        return {k: v for k, v in guess.items() if k in ("zmk_keyboard", "layout_name")}
    raise SyncError("no physical layout found for the keyboard; say how its keys sit with `layout:` in the "
                    "config, a keymap-drawer layout such as {zmk_keyboard: corne} or "
                    "{cols_thumbs_notation: 33333+3 3+33333}")


# ---------- the drawing ----------

def drawing_from_yaml(cfg, config_path, previous, fetch, log):
    """The drawing of the keymap-drawer YAML the config names, with its drawer config."""
    base = os.path.dirname(os.path.abspath(config_path))
    try:
        doc = keymap_mod.load_yaml(keymap_mod.expand(cfg["keymap"], base))
        drawer_cfg = keymap_mod.load_yaml(keymap_mod.expand(cfg["drawer_config"], base)) if cfg.get("drawer_config") else None
    except (OSError, keymap_mod.KeymapError) as e:
        raise SyncError(f"could not read the keymap-drawer file the config names: {e}")
    try:
        return keymap_mod.draw(doc, drawer_cfg, stagger=cfg.get("stagger", True) is not False,
                               fetch=fetch, previous=previous, log=log)
    except keymap_mod.KeymapError as e:
        raise SyncError(str(e))


def drawing_from_keymap(cfg, config_path, root, keymap_path, zlayers, previous, fetch, log):
    """The drawing of the ZMK keymap itself: keymap-drawer's parse of it, every layer under a name
    of its own, on the physical layout layout_for_keymap finds."""
    base = os.path.dirname(os.path.abspath(config_path))
    drawer_config = keymap_mod.expand(cfg["drawer_config"], base) if cfg.get("drawer_config") else None
    names = unique_names([l["name"] for l in zlayers if l["drawn"]])
    parsed = parse_keymap(keymap_path, drawer_config, names)
    try:
        drawer_cfg = keymap_mod.load_yaml(drawer_config) if drawer_config else None
        doc = {"layout": layout_for_keymap(cfg, config_path, root, keymap_path, parsed),
               "layers": parsed.get("layers") or {}, "combos": parsed.get("combos") or []}
        return keymap_mod.draw(doc, drawer_cfg, stagger=cfg.get("stagger", True) is not False,
                               fetch=fetch, previous=previous, log=log), names
    except (OSError, keymap_mod.KeymapError) as e:
        raise SyncError(str(e))


# ---------- the mapping ----------

def normalise(name):
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def draft_layers(names, drawn, previous):
    """ZMK layer id -> {name, drawer, label}, carrying over what the config already said.

    Which drawing a layer belongs to is a naming choice — your names, not the keymap's — so it is
    carried over wherever the config has already made it, matched by name where that is
    unambiguous, and otherwise left undecided rather than guessed at.
    """
    by_norm = {normalise(d): d for d in drawn}
    out, undecided = {}, []
    for i, name in enumerate(names):
        keep = (previous or {}).get(str(i)) or {}
        drawer = keep.get("drawer") if "drawer" in keep else by_norm.get(normalise(name))
        out[str(i)] = {"name": name, "drawer": drawer, "label": keep.get("label") or name.title()}
        if drawer is None and "drawer" not in keep:
            undecided.append((i, name, None))
    return out, undecided


def combo_coverage(parsed, layers, positions, drawn, drawn_combos):
    """Each drawn combo's key positions and the drawn layers it really fires on.

    A combo's layer list comes back as display names, and two ZMK layers may share one; both are
    resolved to the drawing they belong to, which is the only thing the HUD draws anyway.

    Only combos the drawer file actually draws are worth saying anything about — one the HUD has no
    legend for can never show a pill. And where the drawer draws several combos on one set of keys
    it has already said which layer each belongs to, key by key, which is more than can be
    recovered from the firmware: those are left alone.
    """
    drawer_of_name = {}
    for entry in layers.values():
        if entry.get("drawer"):
            drawer_of_name.setdefault(entry["name"], set()).add(entry["drawer"])
    idx_of_pos = {int(p): i for p, i in (positions or {}).items()}
    once = {k for k, n in drawn_combos.items() if n == 1}
    out = []
    for combo in parsed.get("combos") or []:
        pos = combo.get("p") or combo.get("key_positions") or []
        if not pos or not all(p in idx_of_pos for p in pos):
            continue                       # a combo on keys this board does not draw
        keys = tuple(sorted(idx_of_pos[p] for p in pos))
        if keys not in once:
            continue
        names = combo.get("l") or combo.get("layers") or []
        on = sorted({d for n in names for d in drawer_of_name.get(n, ())}, key=drawn.index)
        if not on:
            continue
        out.append({"positions": list(keys), "layers": on,
                    "binding": combo.get("k") if isinstance(combo.get("k"), str) else None})
    return out


def drawn(drawing, cfg):
    """The drawn layer names, ZMK position -> drawn key index, and how many combos each set of keys
    draws, from the drawing import has just made. Which keys are drawn is the drawing's business;
    import only needs to speak its language."""
    counts = {}
    for combo in drawing["combos"]:
        key = tuple(sorted(combo["positions"]))
        counts[key] = counts.get(key, 0) + 1
    positions = cfg.get("positions")
    if positions:
        return drawing["layer_order"], {str(int(p)): i for i, p in enumerate(positions)}, counts
    return drawing["layer_order"], {str(i): i for i in range(len(drawing["layout"]["keys"]))}, counts


# ---------- writing ----------

def render(data, undecided):
    """The derived file. Written by hand rather than yaml.dump so it can carry its own comments —
    it is meant to be read, and the lines import had to guess at have to stand out."""
    L = ["# Written by `zmk-layer-hud import`. Do not edit: `zmk-layer-hud sync` rewrites it.",
         "#",
         "# What the keyboard's own ZMK keymap says, which a drawing does not carry: the id of every",
         "# layer, and the layers each combo really fires on. This copy is for reading; the HUD takes",
         "# the same from the definitions beside it. Anything you set in config.yaml wins over both,",
         "# so corrections belong there and survive a sync.",
         f"#   source:   {data['source']}",
         f"#   keyboard: {data['keyboard']}",
         ""]
    if undecided:
        L += [f"# {len(undecided)} layer(s) below have no drawing: set `drawer:` for them in config.yaml",
              "# under `layers:`, or leave them null if nothing draws them.", ""]
    if data.get("combo_term_ms"):
        L += ["# The keyboard's own combo timeout: presses within it are one chord, and the HUD has",
              "# to group them the same way or it draws chords that were never struck.",
              f"combo_term_ms: {data['combo_term_ms']}", ""]
    if data.get("combo_idle_ms"):
        L += ["# How long the keyboard must be idle before a combo (ZMK require-prior-idle-ms): a",
              "# chord struck sooner after another key is typed as its keys, and drawn so.",
              f"combo_idle_ms: {data['combo_idle_ms']}", ""]
    L.append("layers:")
    for lid, entry in sorted(data["layers"].items(), key=lambda kv: int(kv[0])):
        drawer = entry["drawer"]
        mark = "" if drawer else "    # not drawn — set one in config.yaml if it should be"
        L.append(f"  {lid}: {{ name: {yq(entry['name'])}, drawer: {yq(drawer) if drawer else 'null'}, "
                 f"label: {yq(entry['label'])} }}{mark}")
    L += ["", "combos:"]
    for c in data["combos"]:
        note = f"    # {c['binding']}" if c.get("binding") else ""
        L.append(f"  - positions: [{', '.join(str(p) for p in c['positions'])}]{note}")
        L.append(f"    layers: [{', '.join(c['layers'])}]")
    return "\n".join(L) + "\n"


def yq(s):
    """Quote a scalar only when YAML would otherwise read it as something else."""
    s = str(s)
    return s if re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9 _.-]*", s) and s.lower() not in (
        "yes", "no", "on", "off", "true", "false", "null", "~") else json.dumps(s, ensure_ascii=False)


def delta(before, after):
    """What changed, as lines a human can read. Empty when nothing did."""
    out = []
    was, now = (before or {}).get("combo_term_ms"), after.get("combo_term_ms")
    if before is not None and was != now:
        out.append(f"  ~ combo term: {was} ms became {now} ms")
    was, now = (before or {}).get("combo_idle_ms"), after.get("combo_idle_ms")
    if before is not None and was != now:
        out.append(f"  ~ idle before a combo: {was or 0} ms became {now or 0} ms")
    ol, nl = (before or {}).get("layers", {}), after["layers"]
    for lid in sorted(set(ol) | set(nl), key=int):
        a, b = ol.get(lid), nl.get(lid)
        if a == b:
            continue
        if not a:
            out.append(f"  + layer {lid} {b['name']!r}" + (f" -> {b['drawer']}" if b["drawer"] else " (not drawn)"))
        elif not b:
            out.append(f"  - layer {lid} {a['name']!r} is gone")
        else:
            was = f"{a['name']!r}" + (f" -> {a['drawer']}" if a.get("drawer") else "")
            now = f"{b['name']!r}" + (f" -> {b['drawer']}" if b.get("drawer") else "")
            out.append(f"  ~ layer {lid}: {was} became {now}")
    oc = {tuple(c["positions"]): c for c in (before or {}).get("combos", [])}
    nc = {tuple(c["positions"]): c for c in after["combos"]}
    for pos in sorted(set(oc) | set(nc)):
        a, b = oc.get(pos), nc.get(pos)
        if a and b and a["layers"] == b["layers"]:
            continue
        keys = "[" + ", ".join(str(p) for p in pos) + "]"
        if not a:
            out.append(f"  + combo {keys} on {len(b['layers'])} layers")
        elif not b:
            out.append(f"  - combo {keys} is gone")
        else:
            gained = [l for l in b["layers"] if l not in a["layers"]]
            lost = [l for l in a["layers"] if l not in b["layers"]]
            bits = (f"+{', '.join(gained)}" if gained else "") + ("  " if gained and lost else "") + \
                   (f"-{', '.join(lost)}" if lost else "")
            out.append(f"  ~ combo {keys}: {bits}")
    return out


def drawing_delta(before, after):
    """What changed in the drawing, one line for each kind of thing. Empty when nothing did."""
    if before is None:
        return []
    out = []
    ok, nk = len(before["layout"]["keys"]), len(after["layout"]["keys"])
    if ok != nk:
        out.append(f"  ~ keys: {ok} became {nk}")
    elif before["layout"] != after["layout"]:
        out.append("  ~ keys: moved")
    gone = [l for l in before["layer_order"] if l not in after["layer_order"]]
    new = [l for l in after["layer_order"] if l not in before["layer_order"]]
    if new:
        out.append(f"  + drawn layers {', '.join(new)}")
    if gone:
        out.append(f"  - drawn layers {', '.join(gone)}")
    redrawn = [l for l in after["layer_order"] if l in before["layers"] and before["layers"][l] != after["layers"][l]]
    if redrawn:
        out.append(f"  ~ legends on {', '.join(redrawn)}")
    if before["combos"] != after["combos"]:
        out.append(f"  ~ drawn combos: {len(before['combos'])} became {len(after['combos'])}")
    if set(before["glyphs"]) != set(after["glyphs"]):
        out.append(f"  ~ glyphs: {len(before['glyphs'])} became {len(after['glyphs'])}")
    return out


def read_imported(path):
    if not os.path.isfile(path):
        return None
    import yaml
    with open(path, encoding="utf-8") as f:
        got = yaml.safe_load(f) or {}
    return {"layers": {str(k): v for k, v in (got.get("layers") or {}).items()},
            "combo_term_ms": got.get("combo_term_ms"),
            "combo_idle_ms": got.get("combo_idle_ms"),
            "combos": got.get("combos") or []}


def read_previous(path):
    """The definitions written before, or None: missing, unreadable, or of another version, which
    this write replaces."""
    try:
        return keymap_mod.read_definitions(path) if os.path.isfile(path) else None
    except (OSError, keymap_mod.KeymapError):
        return None


def write_if_changed(path, text):
    """`text` into `path`, unless it holds exactly that already: every reload clears the keys the
    page holds down, so an unchanged sync must change nothing. Written to the file a symlink points
    at (a config kept in a repo, `zmk-layer-hud config link`), and in one step, by replacing it, so
    no reader ever sees half a file. Returns whether anything was written."""
    target = os.path.realpath(path)
    try:
        with open(target, encoding="utf-8") as f:
            if f.read() == text:
                return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(target), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target), prefix=f".{os.path.basename(target)}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


def new_config(config_path):
    """A config to start from, where there is none: config/example.yaml, as `setup` copies it."""
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    shutil.copyfile(os.path.join(ROOT, "config", "example.yaml"), config_path)


# ---------- the commands ----------

def do_import(source, keyboard, config_path, quiet=False, fetch=True):
    """Write the definitions (and, from a repo, the readable record) for one config."""
    config_path = os.path.abspath(os.path.expanduser(config_path))
    if not os.path.exists(config_path):
        new_config(config_path)
        say(quiet, f"no config yet; wrote one to start from: {config_path}")
    cfg = keymap_mod.load_yaml(config_path)
    if not isinstance(cfg, dict):
        raise SyncError(f"{config_path}: expected settings, one per line (`name: value`)")
    log = (lambda msg: say(quiet, msg))
    defs_path, rec_path = definitions_path(config_path), imported_path(config_path)
    previous = read_previous(defs_path)
    before_rec = read_imported(rec_path)
    sources, zmk, undecided, rec = {}, None, [], None

    if source:
        root, kind = resolve_source(source)
        keymap_path = find_keymap(root, keyboard)
        board = os.path.basename(keymap_path)[: -len(".keymap")]
        say(quiet, f"reading {os.path.relpath(keymap_path, root)}")
        sources.update(repo=source if kind == "url" else os.path.abspath(os.path.expanduser(source)), keyboard=board)
        dts = device_tree(keymap_path)
        zlayers = layer_list(keymap_path, root, dts, log)
    elif not cfg.get("keymap"):
        raise SyncError("import needs a ZMK repo -- a GitHub URL, or a path to a working copy -- or `keymap:` in "
                        f"{config_path} naming your keymap-drawer YAML")

    if cfg.get("keymap"):
        made = drawing_from_yaml(cfg, config_path, previous, fetch, log)
        sources["drawing"] = str(cfg["keymap"])
        short = os.path.basename(keymap_mod.expand(cfg["keymap"], os.path.dirname(config_path)))
    else:
        made, drawn_names = drawing_from_keymap(cfg, config_path, root, keymap_path, zlayers, previous, fetch, log)
        sources["drawing"] = "the ZMK keymap"
        short = board
        keys = len(made["drawing"]["layout"]["keys"])
        for key in ("positions", "fingers"):
            given = cfg.get(key)
            count = len(given.split()) if isinstance(given, str) else len(given) if isinstance(given, list) else None
            if count is not None and count != keys:
                raise SyncError(f"{config_path}: `{key}:` gives {count} keys, and the keymap draws {keys}; it was "
                                "written for another drawing, so drop it or write it for this one")

    if source:
        names = [l["name"] for l in zlayers]
        layers_drawn, positions, drawn_combos = drawn(made["drawing"], cfg)
        if cfg.get("keymap"):
            previous_map = dict((before_rec or {}).get("layers")
                                or ((previous or {}).get("zmk") or {}).get("layers") or {})
            # Whatever config.yaml already says outranks anything derived: that mapping is the one
            # part of this a human decided. It is written in ZMK layer order, so a layer's id is its
            # position.
            for i, entry in enumerate(((cfg.get("layers") or {}).get("map") or {}).values()):
                if isinstance(entry, dict) and "drawer" in entry:
                    previous_map.setdefault(str(i), {}).update({"drawer": entry["drawer"], "label": entry.get("label")})
            layers, undecided = draft_layers(names, layers_drawn, previous_map)
            parsed = parse_keymap(keymap_path)
            coverage = combo_coverage(parsed, layers, positions, layers_drawn, drawn_combos)
        else:
            # Drawn from the keymap itself: every live layer is drawn under its own name, in order,
            # and its combos already carry the layers they fire on.
            it = iter(drawn_names)
            layers = {str(i): {"name": l["name"], "drawer": next(it) if l["drawn"] else None,
                               "label": l["name"].title()} for i, l in enumerate(zlayers)}
            coverage = []
        zmk = {"layers": layers, "combo_term_ms": combo_term(keymap_path),
               "combo_idle_ms": combo_idle(keymap_path, dts), "combos": coverage}
        rec = dict(zmk, source=sources["repo"], keyboard=board)
        zmk = {k: v for k, v in zmk.items() if v is not None}
    else:
        # A drawing alone: what an earlier repo import said of the keymap still holds, and where it
        # came from is kept, so a later sync reads that repo again.
        zmk = (previous or {}).get("zmk") or (before_rec and {k: v for k, v in before_rec.items() if v})
        repo, kb = recorded(config_path)
        sources.update({k: v for k, v in (("repo", repo), ("keyboard", kb)) if v})

    definitions = {"version": keymap_mod.DEFINITIONS_VERSION, "sources": sources, "source": short}
    if zmk:
        definitions["zmk"] = zmk
    definitions.update(made)

    # Built before anything is written: definitions that would not load are not written, so a sync
    # can never leave the HUD without a keymap it had.
    try:
        keymap_mod.build_message(keymap_mod.merge_imported(dict(cfg), definitions.get("zmk")),
                                 definitions["drawing"], source=short)
    except keymap_mod.KeymapError as e:
        raise SyncError(f"the definitions would not load, so nothing was written: {e}")
    wrote = write_if_changed(defs_path, json.dumps(definitions, ensure_ascii=False, indent=1) + "\n")
    if rec is not None:
        wrote = write_if_changed(rec_path, render(rec, undecided)) or wrote

    d = made["drawing"]
    say(quiet, f"{len(d['layout']['keys'])} keys, {len(d['layer_order'])} drawn layers, {len(d['combos'])} combos, "
               f"{len(d['glyphs'])} glyphs -> {defs_path}")
    if d["missing_glyphs"]:
        say(quiet, f"{len(d['missing_glyphs'])} glyph(s) could not be had, and show as text: "
                   + ", ".join(d["missing_glyphs"][:6]) + (" …" if len(d["missing_glyphs"]) > 6 else ""))
    if rec is not None:
        say(quiet, f"{len(rec['layers'])} ZMK layers, {len(rec['combos'])} combo coverages -> {rec_path}")
    if undecided:
        say(quiet, f"{len(undecided)} layer(s) have no drawing yet: "
                   + ", ".join(f"{i} {n!r}" for i, n, _ in undecided[:6])
                   + (" …" if len(undecided) > 6 else ""))
    lines = (delta(before_rec, rec) if rec is not None else []) + drawing_delta((previous or {}).get("drawing"), d)
    if previous is None and before_rec is None:
        say(quiet, "first import; nothing to compare against")
    elif previous is None:
        say(quiet, "definitions written: from now on the HUD reads its keymap from them, and nothing else")
    elif not wrote:
        say(quiet, "no change")
    elif lines:
        say(quiet, f"{len(lines)} change(s):")
        for line in lines[:40]:
            say(quiet, line)
        if len(lines) > 40:
            say(quiet, f"  … and {len(lines) - 40} more")
    else:
        say(quiet, "written again: a glyph or a detail of the drawing changed")
    return 0


def recorded(config_path):
    """(repo, keyboard) a sync reads again: what the definitions recorded, or, from before there
    were definitions, the imported file's header."""
    previous = read_previous(definitions_path(config_path))
    if previous is not None:
        s = previous.get("sources") or {}
        return s.get("repo"), s.get("keyboard")
    rec = imported_path(config_path)
    if os.path.isfile(rec):
        with open(rec, encoding="utf-8") as f:
            head = f.read()
        source = re.search(r"^#\s+source:\s+(.+)$", head, re.M)
        keyboard = re.search(r"^#\s+keyboard:\s+(.+)$", head, re.M)
        return (source.group(1).strip() if source else None), (keyboard.group(1).strip() if keyboard else None)
    return None, None


def say(quiet, msg):
    if not quiet:
        print(msg, file=sys.stderr)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, argv = argv[0], argv[1:]
    opts, rest = {}, []
    while argv:
        a = argv.pop(0)
        if a in ("--keyboard", "--config"):
            opts[a.lstrip("-")] = argv.pop(0) if argv else ""
        elif a in ("--quiet", "--no-fetch"):
            opts[a.lstrip("-")] = True
        else:
            rest.append(a)
    fetch = not opts.get("no-fetch")
    try:
        if cmd == "import":
            # The config given, or the one the HUD would read -- created there when there is none,
            # never swapped for another that happens to exist.
            config_path = opts.get("config") or os.environ.get("ZMKHUD_CONFIG") or keymap_mod.DEFAULT_CONFIG
            return do_import(rest[0] if rest else None, opts.get("keyboard"), config_path, opts.get("quiet", False), fetch)
        if cmd == "sync":
            config_path = opts.get("config") or keymap_mod.find_config()
            repo, keyboard = recorded(config_path)
            if repo is None and not keymap_mod.load_yaml(config_path).get("keymap"):
                raise SyncError("nothing to sync: import a ZMK repo (`zmk-layer-hud import <repo>`), "
                                "or name your keymap-drawer YAML as `keymap:` in the config")
            return do_import(repo, keyboard, config_path, opts.get("quiet", False), fetch)
        raise SyncError(f"unknown command {cmd!r}; try `import` or `sync`")
    except (SyncError, keymap_mod.KeymapError, OSError) as e:
        print(f"zmk-layer-hud {cmd}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
