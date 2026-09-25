"""zmk-layer-hud import / sync — take what the HUD needs out of a ZMK repo, once.

The HUD needs three things the keymap-drawer file does not carry: which ZMK layer id each drawn
layer shows, which key position each drawn key sits at, and which layers each combo really fires
on. Those live in the keyboard's own ZMK keymap, and until now they were written out by hand.

`import` reads them from the repo and writes them into the config, so from then on the HUD reads
its own config and nothing else — no keymap, no repo, no network. `sync` does it again and says
what changed.

    zmk-layer-hud import github.com/you/zmk-config       # or a path to a working copy
    zmk-layer-hud import ~/zmk-config --keyboard corne   # which keyboard, when it holds several
    zmk-layer-hud sync                                   # re-read the recorded source

What is derived goes in its own file, named after the config (`config.yaml` -> `config.imported.yaml`),
so the config stays yours: anything you set there wins, and a sync never touches it. The one thing
import cannot know is which drawn layer shows which ZMK layer — your names, not the keymap's — so
it drafts that mapping and marks the lines it had to leave undecided. Correct those once in the
config; sync will not overwrite them.
"""

import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import keymap as keymap_mod  # noqa: E402

# Where a repo given by URL is kept between syncs. $ZMKHUD_CACHE moves it.
CACHE = os.path.expanduser(os.environ.get("ZMKHUD_CACHE") or "~/.cache/zmk-layer-hud/repos")
# Where what was imported is written: keymap.py decides, so the two cannot disagree.
imported_path = keymap_mod.imported_path


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
    """The display name of every layer, in the keymap's own node order — which is the ZMK layer id.

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


def parse_keymap(keymap_path):
    """keymap-drawer's own ZMK parser: combos with the layers they really fire on."""
    exe = shutil.which("keymap", path=os.path.dirname(sys.executable)) or shutil.which("keymap")
    if not exe:
        raise SyncError("keymap-drawer is needed to read the keymap (make venv, or pip install keymap-drawer)")
    out = run([exe, "parse", "-z", keymap_path]).stdout
    try:
        import yaml
    except ImportError:
        raise SyncError("PyYAML is needed (make venv)")
    return yaml.safe_load(out)


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


# ---------- writing ----------

def render(data, undecided):
    """The derived file. Written by hand rather than yaml.dump so it can carry its own comments —
    it is meant to be read, and the lines import had to guess at have to stand out."""
    L = ["# Written by `zmk-layer-hud import`. Do not edit: `zmk-layer-hud sync` rewrites it.",
         "#",
         "# What the keyboard's own ZMK keymap says, which the keymap-drawer file does not carry:",
         "# the id of every layer, and the layers each combo really fires on. Anything you set in",
         "# config.yaml wins over what is here, so corrections belong there and survive a sync.",
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


def read_imported(path):
    if not os.path.isfile(path):
        return None
    import yaml
    with open(path, encoding="utf-8") as f:
        got = yaml.safe_load(f) or {}
    return {"layers": {str(k): v for k, v in (got.get("layers") or {}).items()},
            "combo_term_ms": got.get("combo_term_ms"),
            "combos": got.get("combos") or []}


# ---------- the commands ----------

def do_import(source, keyboard, config_path, quiet=False):
    cfg_dir = os.path.dirname(os.path.abspath(config_path))
    out_path = imported_path(config_path)
    cfg = keymap_mod.load_yaml(config_path)
    root, kind = resolve_source(source)
    keymap_path = find_keymap(root, keyboard)
    board = os.path.basename(keymap_path)[: -len(".keymap")]
    say(quiet, f"reading {os.path.relpath(keymap_path, root)}")

    names = layer_names(keymap_path)
    parsed = parse_keymap(keymap_path)
    drawn, positions, drawn_combos = drawing(cfg, config_path)

    previous = dict((read_imported(out_path) or {}).get("layers") or {})
    # Whatever config.yaml already says outranks anything derived: that mapping is the one part of
    # this a human decided. It is written in ZMK layer order, so a layer's id is its position.
    for i, entry in enumerate(((cfg.get("layers") or {}).get("map") or {}).values()):
        if isinstance(entry, dict) and "drawer" in entry:
            previous.setdefault(str(i), {}).update({"drawer": entry["drawer"], "label": entry.get("label")})
    layers, undecided = draft_layers(names, drawn, previous)
    data = {"source": source if kind == "url" else os.path.abspath(os.path.expanduser(source)),
            "keyboard": board, "layers": layers,
            "combo_term_ms": combo_term(keymap_path),
            "combos": combo_coverage(parsed, layers, positions, drawn, drawn_combos)}

    before = read_imported(out_path)
    os.makedirs(cfg_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(render(data, undecided))
    lines = delta(before, data)
    say(quiet, f"{len(names)} layers, {len(data['combos'])} combos -> {out_path}")
    if undecided:
        say(quiet, f"{len(undecided)} layer(s) have no drawing yet: "
                   + ", ".join(f"{i} {n!r}" for i, n, _ in undecided[:6])
                   + (" …" if len(undecided) > 6 else ""))
    if before is None:
        say(quiet, "first import; nothing to compare against")
    elif not lines:
        say(quiet, "no change")
    else:
        say(quiet, f"{len(lines)} change(s):")
        for line in lines[:40]:
            say(quiet, line)
        if len(lines) > 40:
            say(quiet, f"  … and {len(lines) - 40} more")
    return 0


def drawing(cfg, config_path):
    """The drawn layer names and the drawn key position map, from the configured keymap-drawer
    file. Which keys are drawn is the drawer file's business; import only needs to speak its
    language."""
    if not cfg.get("keymap"):
        raise SyncError(f"{config_path}: `keymap:` (the path to your keymap-drawer YAML) is required")
    base = os.path.dirname(os.path.abspath(config_path))
    try:
        doc = keymap_mod.load_yaml(keymap_mod.expand(cfg["keymap"], base))
    except Exception as e:
        raise SyncError(f"could not read the keymap-drawer file named by the config: {e}")
    drawn = list(doc.get("layers") or {})
    if not drawn:
        raise SyncError("the keymap-drawer file has no layers")
    counts = {}
    for combo in doc.get("combos") or []:
        pos = combo.get("p") or combo.get("key_positions") or []
        if pos:
            key = tuple(sorted(pos))
            counts[key] = counts.get(key, 0) + 1
    positions = cfg.get("positions")
    if positions:
        return drawn, {str(int(p)): i for i, p in enumerate(positions)}, counts
    n = len(next(iter(doc["layers"].values())))
    return drawn, {str(i): i for i in range(n)}, counts


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
        elif a == "--quiet":
            opts["quiet"] = True
        else:
            rest.append(a)
    config_path = opts.get("config") or keymap_mod.find_config()
    try:
        if cmd == "import":
            if not rest:
                raise SyncError("import needs a repository: a GitHub URL, or a path to a working copy")
            return do_import(rest[0], opts.get("keyboard"), config_path, opts.get("quiet", False))
        if cmd == "sync":
            out_path = imported_path(config_path)
            if not os.path.isfile(out_path):
                raise SyncError(f"nothing imported yet ({out_path} does not exist); run `zmk-layer-hud import` first")
            with open(out_path, encoding="utf-8") as f:
                head = f.read()
            source = re.search(r"^#\s+source:\s+(.+)$", head, re.M)
            keyboard = re.search(r"^#\s+keyboard:\s+(.+)$", head, re.M)
            if not source:
                raise SyncError(f"{out_path} does not record where it came from; run `zmk-layer-hud import` again")
            return do_import(source.group(1).strip(), keyboard.group(1).strip() if keyboard else None,
                             config_path, opts.get("quiet", False))
        raise SyncError(f"unknown command {cmd!r}; try `import` or `sync`")
    except SyncError as e:
        print(f"zmk-layer-hud {cmd}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
