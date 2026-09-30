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
    zmk-layer-hud import --pristine                      # from scratch: forget what earlier imports left
    zmk-layer-hud import --pristine --keep-custom        # ...but keep what only they had, and redo the rest
    zmk-layer-hud import --pristine --keep-custom=2      # ...just the second thing it lists
    zmk-layer-hud sync                                   # read the recorded sources again
    zmk-layer-hud sync --watch                           # and again each time one is edited, till Ctrl-C

This is the only part of zmk-layer-hud that reads the keymap-drawer file, the drawer config, the ZMK
keymap or the network; the HUD itself reads its config and these definitions. The config stays
yours: anything set there wins over what was imported, and a sync never touches it. What a repo's
keymap says is also written to be read, in `config.imported.yaml`. When the drawing is a
keymap-drawer file, the one thing import cannot know is which drawn layer shows which ZMK layer --
your names, not the keymap's -- so it drafts that mapping there and marks the lines it had to leave
undecided; correct those once in the config.

`sync --watch` stays, and syncs again whenever a file it reads where it is changes: the config, the
keymap-drawer YAML and the drawer config, and in a working copy the keymap and everything it
includes. A running HUD reloads the definitions, so it redraws as you edit. A repo imported by URL
is fetched, not watched: `sync` after a push reads it again.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

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

def resolve_source(spec, fresh=False):
    """A working copy to read. A path is used where it is; a URL is cloned into the cache and
    fetched on every later sync, so a sync sees what was pushed. fresh: clone it again."""
    local = os.path.expanduser(spec)
    if os.path.isdir(local):
        return os.path.abspath(local), "path"
    if "://" in spec or spec.startswith("git@"):
        url = spec
    elif re.fullmatch(r"[\w-]+(\.[\w-]+)+(/[\w.-]+){2,}/?", spec):
        url = f"https://{spec}"         # github.com/owner/repo, the way people paste it
    else:
        raise SyncError(f"{spec} is neither a directory nor a repository (github.com/owner/repo, or a URL)")
    if not shutil.which("git"):
        raise SyncError("git is needed to read a repository by URL; pass a path to a working copy instead")
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", url.rstrip("/").removesuffix(".git").split("/")[-1])
    dest = os.path.join(CACHE, name)
    os.makedirs(CACHE, exist_ok=True)
    if fresh and os.path.isdir(dest):
        shutil.rmtree(dest)             # ours: the cache holds nothing but these clones
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
    except Exception as e:   # the YAML parser's own errors too: a file saved half-edited
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

def config_labels(cfg):
    """[(position, key, label)] of the layers.map entries that give a layer a label of their own. The
    map is written in ZMK layer order, so an entry's position is its layer's id."""
    out = []
    for i, (key, entry) in enumerate(((cfg.get("layers") or {}).get("map") or {}).items()):
        if isinstance(entry, dict) and entry.get("label"):
            out.append((i, str(key), str(entry["label"])))
    return out


LABEL_ITEM = re.compile(r"""(,\s*)?\blabel:\s*("[^"]*"|'[^']*'|[^,}]*?)\s*(?=,|\})(\s*,)?""")


def strip_config_labels(config_path):
    """Take every `label:` out of config.yaml's layers.map, and nothing else: the drawings, classes
    and comments stay, line for line. A copy goes to config.yaml.bak-<time> first. -> (how many
    were taken out, [lines left for a human: a label this cannot take out safely])."""
    import datetime
    path = os.path.realpath(config_path)             # a linked config is edited where it lives
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    out, taken, left, where, map_indent = [], 0, [], None, None
    for n, line in enumerate(lines, 1):
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if where is None and re.match(r"^layers:\s*(#.*)?$", line):
            where = "layers"
        elif where == "layers" and re.match(r"^\s+map:\s*(#.*)?$", line):
            where, map_indent = "map", indent
        elif where in ("layers", "map") and stripped and not stripped.startswith("#") and indent == 0:
            where = None                                   # the next top-level setting
        elif where == "map" and stripped and not stripped.startswith("#") and indent <= map_indent:
            where = "layers"
        if where == "map" and indent > map_indent and "label:" in line.split("#")[0]:
            body, hash_, comment = line.partition("#") if not re.search(r"""["'][^"']*#""", line) else (line, "", "")
            if re.match(r"^\s+label:", body):
                taken += 1
                continue                                   # a block mapping's own `label:` line
            if "{" in body and "}" in body:
                new = LABEL_ITEM.sub(lambda m: "," if m.group(1) and m.group(3) else "", body, count=1)
                new = re.sub(r"\{\s*,\s*", "{ ", re.sub(r",?\s*\}(?=[^}]*$)", " }", new))
                taken += 1
                gap = body[len(body.rstrip()):] or " "      # the comment keeps its column
                out.append(new.rstrip() + ((gap + hash_ + comment) if hash_ else ""))
                continue
            left.append(f"{config_path}:{n}: {stripped}")
        out.append(line)
    if taken:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(path, f"{config_path}.bak-{stamp}")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(out))
    return taken, left


def carry(short, title, without, token):
    """One thing --pristine would lose (do_import): what it is, briefly for the question and in full
    for the list, what the HUD does without it, and what do_import keeps it by."""
    return {"short": short, "title": title, "without": without, "token": token}


def decide(carried, keep, ask, quiet, since):
    """Which of carried --pristine keeps (do_import): a set of indexes into it. keep: True, all;
    False, none; a set of numbers as listed (from 1), those; None, ask of each in turn, or refuse
    with the list when there is no one to ask. since: the earlier import they came from."""
    lines = [f"--pristine starts over. These came from {since}, and starting over would lose them:", ""]
    for n, c in enumerate(carried, 1):
        lines += [f"  {n}. {c['title']}", f"     Without it: {c['without']}"]
    if keep is None:
        if ask is None:
            raise SyncError("\n".join(lines + ["", "Nothing was written. Say which to keep: --keep-custom (all of them), "
                                                "--keep-custom=1,3 (just those), or --drop-custom (none)."]))
        print("\n".join(lines + [""]), file=sys.stderr)
        kept = {i for i, c in enumerate(carried) if ask(f"Keep {i + 1}, {c['short']}? [Y/n] ")}
    elif keep is True or keep is False:
        kept = set(range(len(carried))) if keep else set()
    else:
        wrong = sorted(n for n in keep if not 1 <= n <= len(carried))
        if wrong:
            raise SyncError("\n".join(lines + ["", f"Nothing was written: --keep-custom names {', '.join(map(str, wrong))}, "
                                                f"and the list goes up to {len(carried)}."]))
        kept = {n - 1 for n in keep}
    said = lambda idx: ", ".join(f"{i + 1} ({carried[i]['short']})" for i in sorted(idx))
    dropped = set(range(len(carried))) - kept
    say(quiet, "--pristine: " + "; ".join(x for x in (kept and f"kept {said(kept)}", dropped and f"dropped {said(dropped)}") if x))
    return kept


def drop_config_labels(config_path, labelled, quiet):
    """--pristine's config-labels item, dropped: config.yaml loses its `label:`s. -> the config as
    it now reads."""
    taken, left = strip_config_labels(config_path)
    say(quiet, f"--pristine: took {taken} label(s) out of {config_path} (a copy is beside it, .bak-<time>)")
    if left:
        say(quiet, "these could not be taken out safely; change them by hand:\n" + "\n".join("  " + l for l in left))
    cfg = keymap_mod.load_yaml(config_path)
    if not isinstance(cfg, dict):
        raise SyncError(f"{config_path}: taking the labels out left something that does not read; "
                        "the copy beside it is what it was")
    return cfg


def describe_layer(entry):
    drawn = f'drawn as "{entry["drawer"]}"' if entry.get("drawer") else "no drawing of its own"
    return f'shown as "{entry.get("label")}", {drawn}'


def do_import(source, keyboard, config_path, quiet=False, fetch=True, pristine=False, keep=None, ask=None):
    """Write the definitions (and, from a repo, the readable record) for one config.

    pristine: from what is given now and nothing else -- not the definitions or the record an
    earlier import left, not a cached clone. What those carried that the sources given now would
    not make again (carried(), below) is said first; keep=True keeps just that and makes the rest
    anew, keep=False drops it, and keep=None asks `ask` (a question -> yes/no), or refuses with the
    list when there is no one to ask. config.yaml is never written either way."""
    config_path = os.path.abspath(os.path.expanduser(config_path))
    if not os.path.exists(config_path):
        new_config(config_path)
        say(quiet, f"no config yet; wrote one to start from: {config_path}")
    cfg = keymap_mod.load_yaml(config_path)
    if not isinstance(cfg, dict):
        raise SyncError(f"{config_path}: expected settings, one per line (`name: value`)")
    log = (lambda msg: say(quiet, msg))
    defs_path, rec_path = definitions_path(config_path), imported_path(config_path)
    old_defs, old_rec = read_previous(defs_path), read_imported(rec_path)
    previous = None if pristine else old_defs
    before_rec = None if pristine else old_rec
    sources, zmk, undecided, rec = {}, None, [], None
    carried = []                          # what --pristine would lose (carry()), in the order it asks
    labelled = config_labels(cfg) if pristine else []
    if labelled:
        had = ((old_defs or {}).get("zmk") or {}).get("layers") or (old_rec or {}).get("layers") or {}
        eg = [(i, key, label) for i, key, label in labelled if i > 0][:2] or labelled[:1]
        named = lambda i: (had.get(str(i)) or {}).get("name")
        eg_new = next((named(i).title() for i, _, _ in eg if named(i)), None)
        carried.append(carry("the layer labels in config.yaml",
                             f"Layer labels your config.yaml sets ({len(labelled)} layers, e.g. "
                             + ", ".join(f'{key} = "{label}"' for _, key, label in eg) + ")",
                             "each layer is named as the keymap names it" + (f' (e.g. "{eg_new}")' if eg_new else "")
                             + ". config.yaml is edited: only the `label:` parts go, after a backup.",
                             "config_labels"))

    if source and os.path.isfile(os.path.expanduser(source)):
        # A keymap-drawer YAML, not a repo. The config is what says where the drawing lives -- sync
        # reads it again from there -- so the file is taken only when that is what it already names.
        given = os.path.realpath(os.path.expanduser(source))
        named = cfg.get("keymap") and keymap_mod.expand(cfg["keymap"], os.path.dirname(config_path))
        if not named or os.path.realpath(named) != given:
            raise SyncError(f"to draw from {source}, name it as `keymap: {source}` in {config_path} "
                            "and run `zmk-layer-hud import`")
        source = None

    if source:
        root, kind = resolve_source(source, fresh=pristine)
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
            if pristine:
                # Mappings an earlier import settled for layers the config does not map: a fresh
                # draft guesses them from names, or leaves them undecided.
                old_map = (old_rec or {}).get("layers") or ((old_defs or {}).get("zmk") or {}).get("layers") or {}
                for i, entry in sorted(layers.items(), key=lambda kv: int(kv[0])):
                    was = old_map.get(i) or {}
                    if i in previous_map or "drawer" not in was or was.get("name", entry["name"]) != entry["name"]:
                        continue
                    if (was.get("drawer"), was.get("label")) != (entry["drawer"], entry["label"]):
                        carried.append(carry(f"layer {i} ({entry['name']})",
                                             f"Layer {i} ({entry['name']}): {describe_layer(was)}",
                                             f"starting over would guess: {describe_layer(entry)}.",
                                             ("layer", i, was)))
            parsed = parse_keymap(keymap_path)
            coverage = combo_coverage(parsed, layers, positions, layers_drawn, drawn_combos)
        else:
            # Drawn from the keymap itself: every live layer is drawn under its own name, in order,
            # and its combos already carry the layers they fire on.
            it = iter(drawn_names)
            layers = {str(i): {"name": l["name"], "drawer": next(it) if l["drawn"] else None,
                               "label": l["name"].title()} for i, l in enumerate(zlayers)}
            coverage = []
        since = f"your last import of {old_rec.get('source')}" if (old_rec or {}).get("source") else "your last import"
        kept = [carried[n]["token"] for n in sorted(decide(carried, keep, ask, quiet, since))] if carried else []
        if labelled and "config_labels" not in kept:
            cfg = drop_config_labels(config_path, labelled, quiet)
            for i, _, _ in labelled:
                if str(i) in layers:
                    layers[str(i)]["label"] = layers[str(i)]["name"].title()
        mappings = [t for t in kept if isinstance(t, tuple)]
        if mappings:
            for _kind, i, was in mappings:
                layers[i].update(drawer=was.get("drawer"), label=was.get("label") or layers[i]["label"])
            undecided = [u for u in undecided if str(u[0]) not in {c[1] for c in mappings}]
            if cfg.get("keymap"):
                coverage = combo_coverage(parsed, layers, positions, layers_drawn, drawn_combos)
        zmk = {"layers": layers, "combo_term_ms": combo_term(keymap_path),
               "combo_idle_ms": combo_idle(keymap_path, dts), "combos": coverage}
        rec = dict(zmk, source=sources["repo"], keyboard=board)
        zmk = {k: v for k, v in zmk.items() if v is not None}
    else:
        # A drawing alone: what an earlier repo import said of the keymap still holds, and where it
        # came from is kept, so a later sync reads that repo again.
        zmk = (previous or {}).get("zmk") or (before_rec and {k: v for k, v in before_rec.items() if v})
        repo, kb = (None, None) if pristine else recorded(config_path)
        if pristine:
            old_zmk = (old_defs or {}).get("zmk") or (old_rec and {k: v for k, v in old_rec.items() if v})
            old_repo, old_kb = recorded(config_path)
            # One item each, and only what would change something: a value the config sets itself
            # wins over the import anyway, and one equal to the default is the default.
            old_zmk = old_zmk or {}
            layers_had = old_zmk.get("layers") or {}
            if layers_had:
                eg = ", ".join(f"{i} = {layers_had[i].get('name')}" for i in sorted(layers_had, key=int)[1:3])
                carried.append(carry("layer numbers and names",
                                     f"Layer numbers and names ({len(layers_had)} layers" + (f", e.g. {eg})" if eg else ")"),
                                     "the layers are numbered in drawing order, so a layer the keyboard reports "
                                     "may show as the wrong one.", "layers"))
            term = old_zmk.get("combo_term_ms")
            if term and "combo_term_ms" not in cfg and int(term) != 50:
                carried.append(carry(f"combo timeout ({term} ms)", f"Combo timeout: {term} ms",
                                     "the default, 50 ms.", "combo_term_ms"))
            idle = old_zmk.get("combo_idle_ms")
            if idle and "combo_idle_ms" not in cfg:
                carried.append(carry(f"combo idle time ({idle} ms)",
                                     f"Combo idle time: {idle} ms (a combo fires only this long after the key before it)",
                                     "none: a combo fires straight after any key.", "combo_idle_ms"))
            combos_had = old_zmk.get("combos") or []
            if combos_had:
                carried.append(carry("which layers each combo works on",
                                     f"Which layers each combo works on ({len(combos_had)} combos)",
                                     "each combo shows on the layers your keymap-drawer file draws it on.", "combos"))
            if old_repo:
                where = old_repo + (f" ({old_kb})" if old_kb else "")
                carried.append(carry("the repo sync reads", f"The repo `zmk-layer-hud sync` reads again: {where}",
                                     "`sync` redraws from your keymap-drawer file only.", "repo"))
            since = f"your last import of {old_repo}" if old_repo else "your last import"
            kept = {carried[n]["token"] for n in decide(carried, keep, ask, quiet, since)} if carried else set()
            zmk = {k: v for k, v in old_zmk.items() if k in kept} or None
            if labelled and "config_labels" not in kept:
                cfg = drop_config_labels(config_path, labelled, quiet)
                if zmk and zmk.get("layers"):
                    zmk["layers"] = {i: dict(e, label=e["name"].title()) if int(i) in {p for p, _, _ in labelled} else e
                                     for i, e in zmk["layers"].items()}
            if "repo" in kept:
                repo, kb = old_repo, old_kb
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
    lines = (delta(old_rec, rec) if rec is not None else []) + drawing_delta((old_defs or {}).get("drawing"), d)
    if old_defs is None and old_rec is None:
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


# ---------- watching ----------

def is_url(repo):
    return "://" in repo or repo.startswith("git@")


def watched(config_path, repo=None, keyboard=None):
    """The files a sync of this config reads where they are: the config, the keymap-drawer YAML,
    the drawer config and the layout files it names, and in a working copy, the keyboard's keymap,
    everything it includes, and the files its physical layout can be in."""
    config_path = os.path.abspath(config_path)
    base = os.path.dirname(config_path)
    files = [config_path]
    try:
        cfg = keymap_mod.load_yaml(config_path)
    except Exception:
        cfg = None                        # saved half-edited: the config alone, until it reads again
    if isinstance(cfg, dict):
        named = [cfg.get("keymap"), cfg.get("drawer_config")]
        if isinstance(cfg.get("layout"), dict):
            named += [cfg["layout"].get(k) for k in ("qmk_info_json", "dts_layout")]
        files += [keymap_mod.expand(p, base) for p in named if isinstance(p, str) and "://" not in p]
    if repo and os.path.isdir(repo):
        try:
            keymap_path = find_keymap(repo, keyboard)
        except SyncError:
            keymap_path = None
        if keymap_path:
            own, beside = boards_files(repo, keymap_path)
            files += [path for path, _ in reachable(keymap_path)] + own + beside
    return sorted({os.path.abspath(f) for f in files})


def stat_of(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size, st.st_ino


def watch(config_path, quiet=False, fetch=True, interval=1.0, sleep=time.sleep, rounds=None):
    """`sync --watch`: sync, then sync again each time a file watched() lists changes, until
    interrupted. What a sync is about to read is noted before it runs, so a file saved again while
    it runs is synced once more. A sync that fails says why and the watch goes on: a keymap saved
    half-edited is taken once it is whole. Each sync reads the glyphs afresh, so one that could not
    be had is tried again. `rounds` bounds the loop, for the tests."""
    repo, keyboard = recorded(config_path)
    if repo and is_url(repo):
        raise SyncError(f"the repo was imported by URL ({repo}), which cannot be watched: import a working copy "
                        "(`zmk-layer-hud import path/to/zmk-config`) to watch it, or `sync` after each push")

    def snapshot():
        return {path: stat_of(path) for path in watched(config_path, repo, keyboard)}

    def once():
        keymap_mod._glyph_memo.clear()
        try:
            do_import(repo, keyboard, config_path, quiet, fetch)
        except (SyncError, keymap_mod.KeymapError, OSError) as e:
            print(f"zmk-layer-hud sync: {e}", file=sys.stderr)
        except Exception as e:            # the config saved half-edited, say: the next save is taken
            print(f"zmk-layer-hud sync: {type(e).__name__}: {e}", file=sys.stderr)

    seen = snapshot()
    once()
    say(quiet, f"watching {len(seen)} file(s) for changes; Ctrl-C stops")
    while rounds is None or rounds > 0:
        if rounds is not None:
            rounds -= 1
        sleep(interval)
        now = snapshot()
        if now == seen:
            continue
        changed = [p for p in sorted(set(now) | set(seen)) if now.get(p) != seen.get(p)]
        say(quiet, "changed: " + ", ".join(os.path.basename(p) for p in changed[:5]) + (" …" if len(changed) > 5 else ""))
        seen = now
        once()
    return 0


def say(quiet, msg):
    if not quiet:
        print(msg, file=sys.stderr)


def ask_tty(question):
    try:
        return input(question).strip().lower() in ("", "y", "yes")
    except EOFError:
        return False


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
        elif a in ("--quiet", "--no-fetch", "--pristine", "--keep-custom", "--drop-custom", "--watch"):
            opts[a.lstrip("-")] = True
        elif a.startswith("--keep-custom="):
            try:
                opts["keep-custom"] = {int(n) for n in a.split("=", 1)[1].split(",") if n.strip()}
            except ValueError:
                print(f"zmk-layer-hud {cmd}: --keep-custom= takes the numbers of the items to keep, like 1,3",
                      file=sys.stderr)
                return 2
        else:
            rest.append(a)
    fetch = not opts.get("no-fetch")
    try:
        if cmd == "import":
            # The config given, or the one the HUD would read -- created there when there is none,
            # never swapped for another that happens to exist.
            config_path = opts.get("config") or os.environ.get("ZMKHUD_CONFIG") or keymap_mod.DEFAULT_CONFIG
            keep = opts.get("keep-custom") or (False if opts.get("drop-custom") else None)
            return do_import(rest[0] if rest else None, opts.get("keyboard"), config_path, opts.get("quiet", False), fetch,
                             pristine=opts.get("pristine", False) or keep is not None, keep=keep,
                             ask=ask_tty if sys.stdin.isatty() else None)
        if cmd == "sync":
            config_path = opts.get("config") or keymap_mod.find_config()
            repo, keyboard = recorded(config_path)
            if repo is None and not keymap_mod.load_yaml(config_path).get("keymap"):
                raise SyncError("nothing to sync: import a ZMK repo (`zmk-layer-hud import <repo>`), "
                                "or name your keymap-drawer YAML as `keymap:` in the config")
            if opts.get("watch"):
                try:
                    return watch(config_path, opts.get("quiet", False), fetch)
                except KeyboardInterrupt:
                    return 0
            return do_import(repo, keyboard, config_path, opts.get("quiet", False), fetch)
        raise SyncError(f"unknown command {cmd!r}; try `import` or `sync`")
    except (SyncError, keymap_mod.KeymapError, OSError) as e:
        print(f"zmk-layer-hud {cmd}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
