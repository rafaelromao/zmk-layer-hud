/* zmk-layer-hud — what the board looks like, without a DOM: where the keys go, which layer each
 * key's legend comes from, and how the touch keyboard marks them. hud.js draws the page with it,
 * and a native shell draws its own board from the same answers (hud/kbd.js, the iPad keyboard).
 *
 * Everything here is a function of the keymap message (host/keymap.py) and of what the caller holds:
 * nothing is kept, no timer, no DOM.
 */
(function (root) {
  "use strict";

  // ---------- where the keys go ----------

  // The two halves of a split board as its layout draws them: the keys left of the widest gap
  // between columns, and the rest. null for a board in one piece: no gap of a third of a key or
  // more in the middle half of the board. A third, because a split board's thumb clusters can come
  // close (the Diamond's are 0.43 of a key apart); the middle, because a unibody board's nav
  // cluster sits apart near one end.
  function halves(lay) {
    const spans = lay.keys.map(k => ({ l: k.x - k.w / 2, r: k.x + k.w / 2 })).sort((a, b) => a.l - b.l);
    let reach = -Infinity, best = null;
    for (const s of spans) {
      if (reach > -Infinity && s.l - reach > (best ? best.gap : 0)) best = { gap: s.l - reach, at: s.l };
      reach = Math.max(reach, s.r);
    }
    if (!best || best.gap < Math.min(...lay.keys.map(k => k.w)) / 3) return null;
    const mid = best.at - best.gap / 2;
    if (mid < lay.width / 4 || mid > lay.width * 3 / 4) return null;
    const left = new Set();
    lay.keys.forEach((k, idx) => { if (k.x < best.at) left.add(idx); });
    return { left, gap: best.gap };
  }

  const gapFor = (lay, scale) => Math.max(2, Math.min(8, Math.round(Math.min(...lay.keys.map(k => k.h)) * scale * 0.1)));

  /* The keys placed: keymap-drawer's physical layout (centre x/y, width, height, rotation in the
   * drawer's units) scaled to `width`. Floating, the board spans the width with `gap` between keys.
   * Docked ({maxHeight, split}), it is as wide as the width allows and no taller than maxHeight less
   * `chrome` (what is not the board); a split board's halves go to the sides, where the thumbs are
   * on a tablet held in two hands, never closer than the layout draws them, and a board in one
   * piece is centred. The gap grows with the keys, so a board scaled up for glass is not all key.
   *
   * Returns {scale, gap, offsetX, centers: [{x, y}], rects: [{x, y, w, h, r}], height, kh, middle}: each
   * key's centre (what a touch between keys is measured from), its box before rotation, the board's
   * height, the typical key's height (what legends scale with), and for a split board docked the
   * room between its halves, {x, w} (null otherwise). */
  function geometry(lay, opts) {
    const width = opts.width;
    let scale = width / lay.width, gap = opts.gap, offsetX = 0, middle = null;
    let offsetOf = () => offsetX;
    if (opts.dock) {
      const room = Math.max(40, opts.dock.maxHeight - (opts.chrome || 0));
      const split = opts.dock.split !== false ? halves(lay) : null;
      if (split) {
        const ext = (keys, side) => keys.map(k => k.x + side * k.w / 2);
        const left = lay.keys.filter((k, i) => split.left.has(i)), right = lay.keys.filter((k, i) => !split.left.has(i));
        const lMin = Math.min(...ext(left, -1)), lMax = Math.max(...ext(left, 1));
        const rMin = Math.min(...ext(right, -1)), rMax = Math.max(...ext(right, 1));
        // The halves never come closer than the layout draws them, a gap's margin at either side.
        const span = (lMax - lMin) + (rMax - rMin) + split.gap;
        scale = Math.min(width / span, room / lay.height);
        gap = gapFor(lay, scale);
        scale = Math.min((width - 2 * gap) / span, room / lay.height);
        const toLeft = gap - lMin * scale, toRight = width - gap - rMax * scale;
        offsetOf = idx => (split.left.has(idx) ? toLeft : toRight);
        middle = { x: toLeft + lMax * scale, w: (toRight + rMin * scale) - (toLeft + lMax * scale) };
      } else {
        scale = Math.min(scale, room / lay.height);
        offsetX = Math.max(0, (width - lay.width * scale) / 2);
        gap = gapFor(lay, scale);
      }
    }
    const centers = lay.keys.map((k, idx) => ({ x: offsetOf(idx) + k.x * scale, y: k.y * scale }));
    const rects = lay.keys.map((k, idx) => {
      const w = k.w * scale - gap, h = k.h * scale - gap;
      return { x: centers[idx].x - w / 2, y: k.y * scale - h / 2, w, h, r: k.r || 0 };
    });
    return { scale, gap, offsetX, centers, rects, height: Math.ceil(lay.height * scale),
             kh: Math.min(...lay.keys.map(k => k.h)) * scale - gap, middle };
  }

  // ---------- which legend ----------

  // ZMK layer id → keymap entry ({name, drawer, label, cls}); falls back to the YAML order.
  function zl(data, id) {
    const t = data.zmk_layers || {};
    if (t[String(id)]) return t[String(id)];
    const name = data.layer_order[id];
    return name ? { id, name, drawer: name, label: name, cls: id === 0 ? "off" : "momentary" } : null;
  }

  // Layer ids, top first, as the drawer layers that draw them: undrawn layers are skipped,
  // duplicates (two ids drawn by one layer) collapse, and the base layer is always last.
  function drawnStack(data, ids) {
    const names = [];
    for (const id of ids) {
      const z = zl(data, id);
      if (z && z.drawer && !names.includes(z.drawer)) names.push(z.drawer);
    }
    if (!names.includes(data.base)) names.push(data.base);
    return names;
  }

  // The binding that wins at idx, walking the stack through transparent keys.
  function resolve(data, idx, layersTopFirst) {
    for (const name of layersTopFirst) {
      const k = data.layers[name] && data.layers[name][idx];
      if (!k) continue;
      if (k.type === "trans") continue;
      return { key: k, layer: name };
    }
    return null;
  }

  // How the winning binding marks its key: see-through to a layer below (trans), nothing there
  // (blank), a key the layer leaves to another one (ghost), a layer's own key held (held).
  function bindingClasses(data, idx, layers, r) {
    if (!r) return ["blank"];
    const out = [];
    const top = layers[0];
    const topKey = data.layers[top] && data.layers[top][idx];
    if (r.layer !== top && topKey && topKey.type === "trans") out.push("trans");
    if (r.key.type === "blank") out.push("blank");
    if (r.key.type === "ghost") out.push("ghost");
    if (r.key.type.startsWith("held")) out.push("held");
    return out;
  }

  // The touch keyboard's marks: a finger on the key, and a hold let go and still on -- one-shot
  // (latched) or locked.
  function touchClasses(touch, idx) {
    const out = [];
    if (!touch) return out;
    if (touch.down && touch.down.includes(idx)) out.push("pressed");
    const latch = touch.latched && touch.latched[idx];
    if (latch) out.push(latch === "locked" ? "locked" : "latched");
    return out;
  }

  // Shift is a modifier, not a layer: a single lowercase letter is the whole of what it changes
  // (\p{Ll}, so á becomes Á), and a glyph, a word or a symbol is left alone.
  const shiftLegend = (t, shifting) => (shifting && typeof t === "string" && /^\p{Ll}$/u.test(t)) ? t.toUpperCase() : t;

  // Either Shift in a report's modifier byte.
  const SHIFT_BITS = 0x22;

  /* One key as a native board draws it, on the touch keyboard's layers: its legends (the tap one
   * shifted when Shift is held or latched), the glyph names for them (data.glyphs), and its marks. */
  function keyFace(data, idx, layers, touch) {
    const r = resolve(data, idx, layers);
    const cls = bindingClasses(data, idx, layers, r).concat(touchClasses(touch, idx));
    if (!r) return { tap: "", hold: "", shifted: "", cls };
    const shifting = !!(touch && touch.mods & SHIFT_BITS);
    return { tap: shiftLegend(r.key.tap, shifting) || "", hold: r.key.hold || "", shifted: r.key.shifted || "",
             glyph: r.key.glyph || null, glyph_hold: r.key.glyph_hold || null, glyph_shifted: r.key.glyph_shifted || null,
             cls };
  }

  // A drawer layer's banner label: the ZMK table's label when one id is drawn by it, else its name.
  function layerLabel(data, name) {
    for (const z of Object.values(data.zmk_layers || {})) if (z.drawer === name) return z.label;
    return name;
  }

  /* The banner on the touch keyboard's layers: the top layer drawn, its class, and on the sub line
   * what glass is holding (touch.note: touch · held, one shot, locked), else the stack below. */
  function touchSummary(data, touch) {
    const entries = touch.ids.map(id => zl(data, id)).filter(z => z && z.drawer);
    const top = entries[0];
    const sub = touch.note || (entries.length > 2 ? entries.slice(0, -1).map(z => z.name).join(" · ") : "");
    if (!top || top.drawer === data.base) return { name: layerLabel(data, data.base), cls: "off", sub };
    return { name: top.label, cls: top.cls || "momentary", sub };
  }

  // ---------- combo chips ----------

  // Whether a point is on a key, its rotation undone.
  function onKey(r, x, y) {
    const a = -(r.r || 0) * Math.PI / 180, dx = x - (r.x + r.w / 2), dy = y - (r.y + r.h / 2);
    return Math.abs(dx * Math.cos(a) - dy * Math.sin(a)) <= r.w / 2 && Math.abs(dx * Math.sin(a) + dy * Math.cos(a)) <= r.h / 2;
  }

  /* The touch keyboard's combos as chips to tap (touch.combos: "chips"): one for each combo that
   * fires on the top layer, whose keys are all drawn and that the drawing gives a legend. A chip sits
   * between its keys, at their centres' middle; one that would land on a key is moved off it, to
   * the nearest point between keys within half a key, and one with no room there beside the others
   * is not drawn.
   * Chips are as big as 0.38 of a key, between 18 and 34 px.
   *
   * `g` is geometry()'s answer (centers, rects, scale); `touch` the touch keyboard's view (ids, its
   * layers top first; chips, the combos held). Returns [{combo, keys, x, y, size, tap, glyph, cls}]:
   * the combo's index in actions.combos, the drawer keys it presses, the chip's centre and size. */
  function chips(data, g, touch) {
    const acts = (data.actions && data.actions.combos) || [];
    const drawn = data.combos || [];
    if (!touch || !touch.ids || !touch.ids.length || !acts.length) return [];
    const top = touch.ids[0];
    const positions = data.positions || {};
    const mapped = Object.keys(positions).length > 0;
    const idxOf = (pos) => (mapped ? positions[String(pos)] : pos);
    const unit = Math.min(...data.layout.keys.map(k => k.h)) * g.scale;
    const size = Math.max(18, Math.min(34, 0.38 * unit));
    const placed = [];
    const free = (x, y) => !g.rects.some(r => onKey(r, x, y)) && placed.every(p => Math.hypot(p.x - x, p.y - y) >= size);
    const out = [];
    acts.forEach((c, i) => {
      const legend = c.drawn != null && drawn[c.drawn] && drawn[c.drawn].key;
      if (!legend || (c.layers && !c.layers.includes(top))) return;
      const keys = c.positions.map(idxOf);
      if (keys.some(k => k == null || !g.centers[k])) return;
      let x = keys.reduce((s, k) => s + g.centers[k].x, 0) / keys.length;
      let y = keys.reduce((s, k) => s + g.centers[k].y, 0) / keys.length;
      if (!free(x, y)) {
        let found = null;
        // Never further than half a key: a chip any further away no longer reads as its keys'.
        for (let r = 2; r <= unit / 2 && !found; r += 2) {
          for (let n = 0; n < 16 && !found; n++) {
            const px = x + r * Math.cos(n * Math.PI / 8), py = y + r * Math.sin(n * Math.PI / 8);
            if (free(px, py)) found = { x: px, y: py };
          }
        }
        if (!found) return;
        x = found.x; y = found.y;
      }
      placed.push({ x, y });
      out.push({ combo: i, keys, x, y, size, tap: legend.tap || "", glyph: legend.glyph || null,
                 cls: touch.chips && touch.chips.includes(i) ? ["pressed"] : [] });
    });
    return out;
  }

  /* The whole board for a native shell: the banner, and every key's face, on the touch keyboard's
   * layers ({ids, down, latched, mods, note}). */
  function board(data, touch) {
    const layers = drawnStack(data, touch.ids || []);
    return { banner: touchSummary(data, touch), layers,
             keys: data.layout.keys.map((k, idx) => keyFace(data, idx, layers, touch)) };
  }

  const ZmkView = { halves, geometry, zl, drawnStack, resolve, bindingClasses, touchClasses, shiftLegend, SHIFT_BITS,
                    keyFace, layerLabel, touchSummary, board, chips };
  if (typeof module === "object" && module.exports) module.exports = ZmkView;
  else root.ZmkView = ZmkView;
})(typeof globalThis !== "undefined" ? globalThis : this);
