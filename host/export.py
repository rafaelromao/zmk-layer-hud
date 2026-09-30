"""zmk-layer-hud session export: a session's heatmap, drawn by keymap-drawer as an SVG.

The drawing is keymap-drawer's own of the configured keymap -- its legends, glyphs and combos, and
the drawer config's styles -- with each key given a class for its step of heat (hs1 to hs6), and
the steps coloured by a stylesheet keymap-drawer puts after its own (svg_extra_style). The steps
are the page's (hud/hud.js heatLevels, hud/hud.css):

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


def stylesheet():
    """The steps' colours, after keymap-drawer's own: the key's fill, and dark legends on it whatever
    the drawer config's dark mode made of them."""
    rules = [f"rect.key.hs{i + 1} {{ fill: {c}; }}" for i, c in enumerate(RAMP)]
    rules.append(", ".join(f"g.hs{i + 1} text" for i in range(LEVELS)) + f" {{ fill: {LEGEND}; }}")
    return "/* zmk-layer-hud: the session's heatmap */\n" + "\n".join(rules)


def svg(session, message, doc, drawer_cfg=None, mode="session", layers=None, footer=""):
    """The SVG text: `doc` is the keymap-drawer YAML the config names, `drawer_cfg` its drawer config.
    `layers` picks which to draw (default: those with any heat, in the keymap's order)."""
    from io import StringIO

    from keymap_drawer.config import Config, DrawConfig
    from keymap_drawer.draw import KeymapDrawer
    from keymap_drawer.keymap import LayoutKey

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
    extra = "\n".join(s for s in (config.draw_config.svg_extra_style, stylesheet()) if s)
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
