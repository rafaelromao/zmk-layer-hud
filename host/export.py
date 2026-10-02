"""zmk-layer-hud session export: a session's heatmap, drawn by keymap-drawer as an SVG.

The drawing is keymap-drawer's own of the configured keymap -- its legends, glyphs and combos, and
the drawer config's styles -- with each key given a class for its step of heat (hs1 to hs6), and
the steps coloured by a stylesheet keymap-drawer puts after its own (svg_extra_style), in the
HUD's light keys or its dark ones (`dark`). The steps are the page's (hud/hud.js heatLevels,
hud/hud.css):

  session   how often each key was pressed on each layer, ln(1+count)/ln(1+most) of the layer's
            own keys: a transparent key draws another layer's legend, and is left out.
  physical  every layer's presses of a key added up, which is where the fingers went; drawn on the
            base layer alone, whose legends are the ones printed on the keys.
  speed     how long each of a layer's own keys takes after the key before it, from the keys timed
            at least SPEED_MIN times; the slowest are the hottest.

`levels` needs only the session and the HUD's keymap message; `svg` needs keymap-drawer (the venv).
"""

import html
import math
from itertools import chain

LEVELS = 6
SPEED_MIN = 3
MODES = ("session", "physical", "speed")
# ColorBrewer YlOrRd's light half, as hud/hud.css draws .hs1 to .hs6: the dark legends read on all.
RAMP = ("#ffffcc", "#ffeda0", "#fed976", "#feb24c", "#fd8d3c", "#fc4e2a")
LEGEND = "#24292e"
# The dark keys (hud/hud.css body.dark), the way LayoutMaster draws its board: dark keys, and the
# steps one indigo over them, light blue cold to dark blue hot -- Tailwind's indigo 200 to 600, then 800 --
# with dark legends on the colder three and light ones on the hotter.
DARK_BOARD = {"background": "#1a1b26", "key": "#24283b", "stroke": "#3b4261", "text": "#e6e8ef",
              "small": "#a9b1d6", "trans": "#545c7e", "combo": "#2f334d", "held": "#5a3b46",
              "dendron": "#565f89"}
DARK_RAMP = ("#c7d2fe", "#a5b4fc", "#818cf8", "#6366f1", "#4f46e5", "#3730a3")
DARK_LEGEND = "#d5d8ff"   # a heated key's hold and shifted legends, on the hotter steps
DARK_COLD = 3             # the steps up to this one are light, and their legends dark
DARK_COLD_LEGEND = "#1a1b26"
DARK_COLD_SMALL = "#2e3240"   # ...and the hold and shifted ones on them


def _log_steps(counts):
    most = max(counts.values(), default=0)
    return {idx: max(1, math.ceil(math.log1p(n) / math.log1p(most) * LEVELS)) for idx, n in counts.items() if n}


def levels(session, message, mode="session"):
    """{drawer layer: {drawer key index: step 1..LEVELS}}, for the keys that have one."""
    if mode not in MODES:
        raise ValueError(f"{mode!r} is not a heatmap to export: {', '.join(MODES)}")
    positions = message.get("positions") or {}
    n = len(message["layout"]["keys"])
    idx_of = (lambda pos: positions.get(str(pos))) if positions else (lambda pos: int(pos) if int(pos) < n else None)
    layers = message["layers"]
    own = lambda layer, idx: idx is not None and idx < len(layers[layer]) and layers[layer][idx].get("type") != "trans"  # noqa: E731
    out = {}
    if mode == "physical":
        total = {}
        for counts in session.get("presses", {}).values():
            for pos, c in counts.items():
                idx = idx_of(pos)
                if idx is not None:
                    total[idx] = total.get(idx, 0) + c
        steps = _log_steps(total)
        return {message["base"]: steps} if steps else {}
    for layer in layers:
        if mode == "session":
            counts = {}
            for pos, c in (session.get("presses", {}).get(layer) or {}).items():
                idx = idx_of(pos)
                if own(layer, idx):
                    counts[idx] = c
            steps = _log_steps(counts)
        else:
            timed, ms = session.get("timed", {}).get(layer) or {}, session.get("ms", {}).get(layer) or {}
            means = {}
            for pos, t in timed.items():
                idx = idx_of(pos)
                if t >= SPEED_MIN and own(layer, idx):
                    means[idx] = ms.get(pos, 0) / t
            lo, hi = min(means.values(), default=0), max(means.values(), default=0)
            steps = {idx: 1 + round((m - lo) / (hi - lo) * (LEVELS - 1)) if hi > lo else 1 for idx, m in means.items()}
        if steps:
            out[layer] = steps
    return out


def stylesheet(dark=False):
    """The steps' colours, after keymap-drawer's own styles and whatever the drawer config's dark
    mode made of them. On light keys: the key's fill, and dark legends on it. `dark`: the HUD's dark
    keys, the steps' fills over them, and the legends that read on each: dark on the colder steps
    (up to DARK_COLD), light on the hotter."""
    if not dark:
        rules = [f"rect.key.hs{i + 1} {{ fill: {c}; }}" for i, c in enumerate(RAMP)]
        rules.append(", ".join(f"g.hs{i + 1} text" for i in range(LEVELS)) + f" {{ fill: {LEGEND}; }}")
    else:
        b = DARK_BOARD
        rules = [f"svg.keymap {{ fill: {b['text']}; background-color: {b['background']}; }}",
                 f"rect.key {{ fill: {b['key']}; }}",
                 f"rect.key, rect.combo {{ stroke: {b['stroke']}; }}",
                 f"rect.combo, rect.combo-separate {{ fill: {b['combo']}; }}",
                 f"rect.held, rect.combo.held {{ fill: {b['held']}; }}",
                 f"text.label, text.footer {{ stroke: {b['background']}; }}",
                 f"text.hold, text.shifted {{ fill: {b['small']}; }}",
                 f"text.trans {{ fill: {b['trans']}; }}",
                 f"path.combo {{ stroke: {b['dendron']}; }}"]
        rules += [f"rect.key.hs{i + 1} {{ fill: {c}; }}" for i, c in enumerate(DARK_RAMP)]
        rules.append(", ".join(f"g.hs{i} text.{t}" for i in range(DARK_COLD + 1, LEVELS + 1) for t in ("hold", "shifted"))
                     + f" {{ fill: {DARK_LEGEND}; }}")
        rules.append(", ".join(f"g.hs{i} text" for i in range(1, DARK_COLD + 1)) + f" {{ fill: {DARK_COLD_LEGEND}; }}")
        rules.append(", ".join(f"g.hs{i} text.{t}" for i in range(1, DARK_COLD + 1) for t in ("hold", "shifted"))
                     + f" {{ fill: {DARK_COLD_SMALL}; }}")
    return "/* zmk-layer-hud: the session's heatmap */\n" + "\n".join(rules)


def svg(session, message, definitions, mode="session", layers=None, footer="", dark=False):
    """The SVG text, drawn from the HUD's definitions alone (`zmk-layer-hud import` wrote them):
    keymap-drawer's own form of the drawing -- its layers and combos, the drawer config -- on the
    drawn keys, handed to keymap-drawer as a QMK layout, and with the glyphs the definitions carry,
    so nothing is fetched and no file of the user's is read. `layers` picks which to draw (default:
    those with any heat, in the keymap's order); `dark`, the HUD's dark keys over its light ones."""
    import json
    from io import BytesIO, StringIO

    from keymap_drawer.config import Config, DrawConfig
    from keymap_drawer.draw import KeymapDrawer
    from keymap_drawer.keymap import LayoutKey

    form = definitions.get("keymap_drawer") or {}
    doc = {"layout": {"qmk_info_json": BytesIO(json.dumps(form.get("layout") or []).encode())},
           "layers": form.get("layers") or {}, "combos": form.get("combos") or [],
           "draw_config": form.get("draw_config") or {}}
    drawer_cfg = dict(form.get("config") or {})
    glyphs = (definitions.get("drawing") or {}).get("glyphs") or {}
    if glyphs:
        dc = dict(drawer_cfg.get("draw_config") or {})
        dc["glyphs"] = {**glyphs, **(dc.get("glyphs") or {})}
        drawer_cfg["draw_config"] = dc
    steps = levels(session, message, mode)
    names = list(layers) if layers else [name for name in doc.get("layers") or {} if name in steps]
    unknown = [name for name in names if name not in (doc.get("layers") or {})]
    if unknown:
        raise ValueError(f"no layer {', '.join(unknown)} in the keymap; it has {', '.join(doc.get('layers') or {})}")
    if not names:
        raise ValueError("nothing is counted in this session to draw")
    config = Config.model_validate(drawer_cfg) if drawer_cfg else Config()
    # The drawer YAML's own draw_config, over the drawer config's, as `keymap draw` does.
    if doc.get("draw_config"):
        config.draw_config = DrawConfig.model_validate(config.draw_config.model_dump() | doc["draw_config"])
    extra = "\n".join(s for s in (config.draw_config.svg_extra_style, stylesheet(dark)) if s)
    config.draw_config = config.draw_config.model_copy(update={"svg_extra_style": extra,
                                                               "footer_text": html.escape(footer)})
    # Every layer goes in, the drawn ones with their heat: a combo names layers that must exist.
    drawn = {}
    for name, rows in doc["layers"].items():
        # Flattened as keymap-drawer flattens a layer's rows, so an index is a keypos.
        keys = [LayoutKey.from_key_spec(k) for k in chain.from_iterable(v if isinstance(v, list) else [v] for v in rows)]
        for idx, step in (steps.get(name) or {}).items() if name in names else ():
            if idx < len(keys):
                keys[idx] = keys[idx].model_copy(update={"type": f"{keys[idx].type} hs{step}".strip()})
        drawn[name] = keys
    out = StringIO()
    drawer = KeymapDrawer(config=config, out=out, layers=drawn, layout=doc["layout"], combos=doc.get("combos") or [])
    drawer.print_board(draw_layers=names)
    return out.getvalue()
