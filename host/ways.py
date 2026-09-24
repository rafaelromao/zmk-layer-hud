"""Every way the keyboard can type each legend, and what the HUD must show for each of them.

The board sweep (hud/tests/cases.js) drives the page with positions and the strip test drives it
with characters, each on its own, and both take the page's own idea of the keymap for granted. A
letter typed by a combo got past both: with positions it drew its pill, the strip showed it, and
the moment the board had to work from the character instead -- positions not fresh, a keyboard
without the module -- every base-layer letter combo lit nothing at all. Nothing that tests one
channel at a time with the renderer's own rules can see that.

So the cases here are built from the keyboard's side. A way to type a legend is the live layer set
the keyboard reports, the keys struck, and the reports it sends; hud/tests/words_test.js sends each
one down every channel it can arrive by and checks the board and the strip against what was struck.
The rules are ZMK's, stated here rather than borrowed from hud.js -- an expectation that calls the
code under test agrees with it however wrong it is:

  - A combo fires only on the highest active layer (ZMK's combo.c matches against
    zmk_keymap_highest_layer_active() alone), so a chord on a layer that declares no combo there
    is two keystrokes, never the base layer's combo.
  - A key falls through transparent bindings to the next active layer.
  - The report for a hold-tap's tap -- a home-row mod, a combo bound to one -- is sent when the key
    comes up, not when it goes down. Every way is sent both ways round: the page cannot know which
    kind of binding it was, and it must be right either way.

`--audit` checks the drawing against the keyboard's own keymap: a drawer layer stands for every ZMK
layer drawn as it, so where two of them do different things on the same keys, the HUD can be right
for only one of them. Those are data, not the page, and are reported rather than tested.

  python3 host/ways.py --cases KEYMAP.json     one JSON case per line, for hud/tests/words_test.js
  python3 host/ways.py --audit [--config CFG] [--source REPO]
                                               the drawing against the keyboard's keymap (the
                                               source `import` recorded, or a working copy)
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uskeys  # noqa: E402

# The kit's recording pace (showcase/rehearse.py: 70 wpm), well outside the 30 ms combo term, so a
# word is keystrokes and not accidental chords.
KEY_GAP_MS = 170


# ---------- the keymap message ----------

class Keymap:
    def __init__(self, data):
        self.data = data
        self.base = data.get("base") or data["layer_order"][0]
        self.ids_of = {}
        for zid, z in (data.get("zmk_layers") or {}).items():
            if z and z.get("drawer"):
                self.ids_of.setdefault(z["drawer"], []).append(int(zid))
        for ids in self.ids_of.values():
            ids.sort()
        self.pos_of = {int(idx): int(pos) for pos, idx in (data.get("positions") or {}).items()}
        self.sticky = set((data.get("extras") or {}).get("sticky") or [])

    def zmk(self, idx):
        """Drawer key -> the ZMK position the firmware reports for it."""
        return self.pos_of.get(idx, idx)

    def stack(self, layer):
        """Top first, the way the keyboard resolves a key with `layer` up."""
        return [layer] if layer == self.base else [layer, self.base]

    def binding(self, idx, stack):
        for name in stack:
            keys = self.data["layers"].get(name) or []
            if idx < len(keys) and keys[idx].get("type") != "trans":
                return name, keys[idx]
        return None, None

    def typing(self, layer):
        """A layer made for typing text: the base, and the sticky alpha layers the config names."""
        return layer == self.base or layer in self.sticky

    def combos_on(self, layer):
        """The combos that fire with `layer` on top -- that layer's, and no other's."""
        return [c for c in self.data["combos"] if layer in c["layers"]]

    def activators(self, layer):
        """The keys that bring `layer` up (host/keymap.py activators(): {layer, idx, kind})."""
        return sorted({a["idx"] for a in self.data.get("activators") or [] if a.get("layer") == layer})


def typed(key, typing_layer):
    """The reports that type this key's legend, or None when it is not something a keyboard types.

    A glyph is an icon, not text: its `tap` only carries the glyph's name for when the SVG is
    missing. A multi-character legend is a macro typing its characters back to back (qu, ão, ões),
    but only as a key on a layer made for typing -- anywhere else, a combo included, the same shape
    is a label ("vim", "cancel", "numbers", "meh ␣") naming what a key does, and nothing of it
    reaches the host as text. Callers pass typing_layer=False for combos.
    """
    legend = key.get("tap") or ""
    if not legend or "<" in legend:
        return None
    if uskeys.keystrokes_for(legend) is not None:
        return uskeys.events_for(legend)
    if key.get("glyph"):
        return None  # the glyph's name standing in for its SVG, not something typed
    if uskeys.NAME_FOR_LEGEND.get(legend) or not typing_layer or not legend.isalpha():
        return None
    events = []
    for ch in legend:
        one = uskeys.events_for(ch) if uskeys.keystrokes_for(ch) is not None else None
        if one is None:
            return None
        events += one
    return events


def accepts(legend):
    name = uskeys.NAME_FOR_LEGEND.get(legend)
    return sorted({l for l, n in uskeys.NAME_FOR_LEGEND.items() if name and n == name} | {legend})


def ident(legend):
    """What a legend types, whatever it is spelled: ⇥ and ↹ are both Tab."""
    return uskeys.NAME_FOR_LEGEND.get(legend) or legend


def adaptive(key):
    """An adaptive key drawn "h|v": the first legend is what it types, the rest what it types in
    some context the drawing does not say (the keyboards repo's magic key is v after a vowel)."""
    parts = [p.strip() for p in (key.get("tap") or "").split("|")]
    if len(parts) < 2 or not all(parts):
        return None
    return {**key, "tap": parts[0]}, parts[1:]


# ---------- the ways ----------

def ways(km):
    """Every (layer, keys) that types a legend on this keymap, with the live ids that put it there.

    A layer is reached the way the keyboard reports it: its lowest ZMK id is what `layers` says
    while it is up, whether it was held, tapped as a one-shot or toggled. The base is reached with
    nothing up; on a Mac the base still has a layer above it (the MACOS one, drawn as nothing), and
    that is a way of its own, since the page has to see through it.
    """
    out = []
    live_states = [(km.base, [], "base")]
    for zid, z in sorted((km.data.get("zmk_layers") or {}).items(), key=lambda kv: int(kv[0])):
        if int(zid) and z and not z.get("drawer"):
            live_states.append((km.base, [int(zid)], f"base under {z.get('name') or zid}"))
            break  # one undrawn layer above the base is enough to prove the page skips it
    for layer in km.data["layer_order"]:
        if layer != km.base and layer in km.ids_of:
            live_states.append((layer, [km.ids_of[layer][0]], layer))

    for layer, live, label in live_states:
        stack = km.stack(layer)
        for idx in range(len(km.data["layers"][layer])):
            on, key = km.binding(idx, stack)
            if key is None:
                continue
            split = adaptive(key)
            if split:
                key, sometimes = split
                for other in sometimes:
                    # Right in its context, which a report does not carry: an answer the page may
                    # give for this legend, never one it must.
                    if typed({"tap": other}, km.typing(on)):
                        out.append({"legend": other, "layer": on, "live": live, "state": label,
                                    "keys": [idx], "combo": False, "conditional": True})
            events = typed(key, km.typing(on))
            if events:
                out.append({"legend": key["tap"], "layer": on, "live": live, "state": label,
                            "keys": [idx], "combo": False, "events": events})
        for combo in km.combos_on(layer):
            events = typed(combo["key"], False)
            if events:
                out.append({"legend": combo["key"]["tap"], "layer": layer, "live": live, "state": label,
                            "keys": sorted(combo["positions"]), "combo": True, "events": events,
                            "pill": pill_of(combo)})
    out += shifted_by_hand(km)
    return out


def shifted_by_hand(km):
    """A capital typed with a Shift held: a base-layer home-row ⇧, then the letter's own keys. The
    layer stays the base -- Shift is a modifier, not a layer -- so the board shows the letter's keys
    and the held Shift, and the strip shows the capital."""
    shifts = [i for i, k in enumerate(km.data["layers"][km.base]) if k.get("hold") == "⇧"]
    out = []
    if not shifts:
        return out
    stack = km.stack(km.base)
    letters = []
    for idx in range(len(km.data["layers"][km.base])):
        _, key = km.binding(idx, stack)
        split = key and adaptive(key)
        if split:
            key = split[0]
        if key and len(key.get("tap") or "") == 1 and key["tap"].isalpha() and key["tap"].islower():
            letters.append((key["tap"], [idx], False))
    for combo in km.combos_on(km.base):
        tap = combo["key"].get("tap") or ""
        if len(tap) == 1 and tap.isalpha() and tap.islower():
            letters.append((tap, sorted(combo["positions"]), combo))
    for tap, keys, combo in letters:
        shift = next((s for s in shifts if s not in keys), None)
        events = typed({"tap": tap.upper()}, True)
        if shift is None or not events:
            continue
        way = {"legend": tap.upper(), "layer": km.base, "live": [], "state": "base, ⇧ held",
               "keys": keys, "combo": bool(combo), "events": events, "held": [shift]}
        if combo:
            # The chord is still the base layer's combo -- Shift changes what it types, not which
            # combo it is -- so the pill is that combo's own.
            way["pill"] = pill_of(combo)
        out.append(way)
    return out


def pill_of(combo):
    return {"tap": combo["key"].get("tap") or "", "glyph": combo["key"].get("glyph")}


def cases(km):
    """One case per way, carrying what the page needs to replay it and what it must then show."""
    all_ways = ways(km)
    by_legend = {}
    for w in all_ways:
        by_legend.setdefault(ident(w["legend"]), []).append(w)
    out = []
    for w in all_ways:
        if w.get("conditional"):
            continue
        same = by_legend[ident(w["legend"])]
        out.append({
            "legend": w["legend"], "accepts": accepts(w["legend"]), "state": w["state"],
            "layer": w["layer"], "live": w["live"], "combo": w["combo"],
            "keys": w["keys"], "zmk": [km.zmk(i) for i in w["keys"]],
            "held": w.get("held", []), "held_zmk": [km.zmk(i) for i in w.get("held", [])],
            "events": w["events"], "pill": w.get("pill"),
            # From the character alone the page cannot tell ways apart that share a live stack --
            # any of them is a right answer there, and only there.
            "alternatives": sorted({tuple(o["keys"]) for o in same
                                    if o["live"] == w["live"] and o.get("held") == w.get("held")}),
            "anywhere": sorted({tuple(o["keys"]) for o in same}),
            # With nothing up but the base: where typing sent in with combos lands first.
            "on_base": sorted({tuple(o["keys"]) for o in same if o["live"] == [] and not o.get("held")}),
            # The pill each chord among them draws: another spelling of the same key (a Tab
            # combo drawn as a glyph, for a ↹ on the nav layer) is still that combo's own.
            "chord_pills": {",".join(map(str, o["keys"])): o["pill"] for o in same if o.get("pill")},
        })
    return out


# ---------- the words ----------

WORDS = [
    # Every base-layer letter combo, in words: k x q w z y v j.
    "zebra", "quick", "jukebox", "wax", "yak", "vex", "jazz", "kiwi",
    # Accents and ç, typed through Alpha 2 and the Ç extension.
    "ação", "você", "já", "é", "órgão", "pão", "lições", "café",
    # Capitals.
    "Zulu", "Quebec", "Xray",
]


def words(km, cases_):
    """Each word typed one legend at a time, every character by every way it has, the others by
    their first: so every way is exercised between neighbours, which is where a combo term, a
    sequence window or a one-shot layer left over from the previous key would show."""
    first = {}
    for c in cases_:
        first.setdefault(c["legend"], c)
    by_legend = {}
    for c in cases_:
        by_legend.setdefault(c["legend"], []).append(c)
    out = []
    for word in WORDS:
        spelled = spell(word, first)
        if spelled is None:
            continue
        variants = [spelled]
        for i, part in enumerate(spelled):
            for alt in by_legend[part["legend"]]:
                if alt is not part:
                    variants.append(spelled[:i] + [alt] + spelled[i + 1:])
        for v in variants:
            out.append({"word": word, "gap_ms": KEY_GAP_MS, "steps": v})
    return out


def spell(word, first):
    """The word as legends this keymap has a way to type, longest first (ão before ã)."""
    out, i = [], 0
    legends = sorted(first, key=len, reverse=True)
    while i < len(word):
        hit = next((l for l in legends if word.startswith(l, i)), None)
        if hit is None:
            return None
        out.append(first[hit])
        i += len(hit)
    return out


# ---------- the audit ----------

def audit(km, parsed):
    """Where one drawer layer stands for ZMK layers that do different things on the same keys.

    `parsed` is keymap-drawer's parse of the keyboard's own keymap (what `import` reads). Layer
    names there are display names and two ZMK layers can share one, so a name stands for every id
    that carries it.
    """
    zl = km.data.get("zmk_layers") or {}
    ids_named = {}
    for zid, z in zl.items():
        ids_named.setdefault(z.get("name"), []).append(int(zid))
    drawer_of = {int(zid): (z.get("drawer") or km.base) for zid, z in zl.items()}
    name_of = {int(zid): z.get("name") for zid, z in zl.items()}
    idx_of = {int(p): i for p, i in (km.data.get("positions") or {}).items()}

    # chord (drawer keys) -> {zmk id: binding}
    fires = {}
    for combo in parsed.get("combos") or []:
        pos = combo.get("p") or []
        if not pos or not all(p in idx_of for p in pos):
            continue
        chord = tuple(sorted(idx_of[p] for p in pos))
        for name in combo.get("l") or []:
            for zid in ids_named.get(name, []):
                fires.setdefault(chord, {})[zid] = combo.get("k")

    findings = []
    for chord, by_id in sorted(fires.items()):
        groups = {}
        for zid in zl:
            zid = int(zid)
            groups.setdefault(drawer_of[zid], {})[zid] = by_id.get(zid)
        for drawer, members in groups.items():
            bindings = {json.dumps(b, sort_keys=True, ensure_ascii=False) for b in members.values()}
            if len(bindings) < 2:
                continue
            fired = {zid: b for zid, b in members.items() if b is not None}
            if not fired:
                continue
            shown = [c["key"].get("tap") for c in km.data["combos"]
                     if drawer in c["layers"] and tuple(sorted(c["positions"])) == chord]
            findings.append({
                "chord": list(chord), "zmk": [km.zmk(i) for i in chord], "drawer": drawer,
                "shown": shown[0] if shown else None,
                "fires": {name_of[z]: members[z] for z in sorted(members)},
            })
    return findings


def report(path, findings):
    """The findings for reading: one line per chord, the ZMK layers grouped by what they fire."""
    def said(b):
        if not b:
            return "nothing"
        if isinstance(b, dict):
            return b.get("t") or json.dumps(b, ensure_ascii=False)
        return str(b)
    lines = [f"{path}", ""]
    if not findings:
        lines.append("every drawing agrees with every ZMK layer drawn as it")
        return "\n".join(lines)
    lines.append(f"{len(findings)} chords where one drawing stands for ZMK layers that fire different things.")
    lines.append("The HUD draws each chord once per drawing, so it can be right for only one group:")
    lines.append("")
    by_drawer = {}
    for f in findings:
        by_drawer.setdefault(f["drawer"], []).append(f)
    for drawer, fs in by_drawer.items():
        lines.append(f"  drawn as {drawer}:")
        for f in fs:
            groups = {}
            for layer, b in f["fires"].items():
                groups.setdefault(said(b), []).append(layer)
            what = "  |  ".join(f"{', '.join(ls)}: {b}" for b, ls in groups.items())
            shown = f" (drawn {f['shown']!r})" if f["shown"] else ""
            lines.append(f"    keys {f['chord']}{shown}  {what}")
    return "\n".join(lines)


def parsed_keymap(config=None, source=None):
    """keymap-drawer's parse of the keyboard's keymap: from `source` (a URL or a working copy,
    as `import` takes), else from the source `import` recorded. A working copy is read where it
    is, so it says what you have not pushed yet."""
    import re
    import keymap as keymap_mod
    import sync
    config_path = config or keymap_mod.find_config()
    imported = sync.imported_path(config_path)
    head = ""
    if os.path.isfile(imported):
        with open(imported, encoding="utf-8") as f:
            head = f.read()
    recorded = re.search(r"^#\s+source:\s+(.+)$", head, re.M)
    keyboard = re.search(r"^#\s+keyboard:\s+(.+)$", head, re.M)
    spec = source or (recorded.group(1).strip() if recorded else None)
    if not spec:
        raise SystemExit(f"ways --audit: {imported} records no source; pass --source, or run `zmk-layer-hud import`")
    try:
        root, _ = sync.resolve_source(os.path.expanduser(spec))
        path = sync.find_keymap(root, keyboard.group(1).strip() if keyboard else None)
        return path, sync.parse_keymap(path)
    except sync.SyncError as e:
        raise SystemExit(f"ways --audit: {e}")


def main(argv):
    if "--cases" in argv:
        with open(argv[argv.index("--cases") + 1], encoding="utf-8") as f:
            km = Keymap(json.load(f))
        cs = cases(km)
        for c in cs:
            print(json.dumps({"kind": "case", **c}, ensure_ascii=False))
        for w in words(km, cs):
            print(json.dumps({"kind": "word", **w}, ensure_ascii=False))
        return 0
    if "--audit" in argv:
        import keymap as keymap_mod
        config = argv[argv.index("--config") + 1] if "--config" in argv else None
        repo = argv[argv.index("--source") + 1] if "--source" in argv else None
        source = keymap_mod.KeymapSource(config)
        source.fetch_glyphs = False
        message = source.load()
        path, parsed = parsed_keymap(source.config_path, repo)
        findings = audit(Keymap(message), parsed)
        if "--json" in argv:
            print(json.dumps({"keymap": path, "findings": findings}, ensure_ascii=False, indent=1))
        else:
            print(report(path, findings))
        return 1 if findings else 0
    sys.stderr.write(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
