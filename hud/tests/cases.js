/* zmk-layer-hud — the sweep: every key on every layer, every combo on every layer it is declared
 * on, and every combination that must NOT draw a combo.
 *
 * Nothing here is hand-written per keymap. The cases are derived from the keymap message itself,
 * so pointing the runner at a different board sweeps that board instead. The expectations come
 * from the message too — what the drawer file says a key or combo is — never from the renderer,
 * which is the thing under test.
 *
 * The same sweep runs under node (hud/tests/dom.js, virtual time) and in the real page
 * (hud/tests/browser.js, real time). `driver` is the only difference between them.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.hudCases = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // ---------- reading the keymap message ----------

  /* ZMK layer ids that draw each drawer layer, and the drawer layers no id reaches. A layer with
   * no id can never appear in liveStack(), so the page cannot be asked to show it: those are
   * reported, not swept. */
  function layerIndex(data) {
    const idsOf = new Map(), unreachable = [];
    for (const [id, z] of Object.entries(data.zmk_layers || {})) {
      if (!z || !z.drawer) continue;
      if (!idsOf.has(z.drawer)) idsOf.set(z.drawer, []);
      idsOf.get(z.drawer).push(Number(id));
    }
    for (const name of data.layer_order) if (!idsOf.has(name)) unreachable.push(name);
    for (const list of idsOf.values()) list.sort((a, b) => a - b);
    return { idsOf, unreachable, live: data.layer_order.filter(n => idsOf.has(n)) };
  }

  // Drawer key index -> ZMK position: the inverse of the message's own map.
  function positionOf(data) {
    const inv = new Map();
    for (const [pos, idx] of Object.entries(data.positions || {})) inv.set(Number(idx), Number(pos));
    return idx => (inv.has(idx) ? inv.get(idx) : idx);
  }

  // The ZMK positions the firmware can report that this keymap does not place: pressing one must
  // light nothing at all (never an arbitrary key that happens to share the number).
  function unmappedPositions(data) {
    const mapped = new Set(Object.keys(data.positions || {}).map(Number));
    const top = Math.max(...mapped, data.layout.keys.length - 1);
    const out = [];
    for (let p = 0; p <= top; p++) if (!mapped.has(p)) out.push(p);
    return out;
  }

  // The stack the page will have with exactly this drawer layer live, top first (hud.js:119).
  const stackFor = (data, layer) => (layer === data.base ? [data.base] : [layer, data.base]);

  // What the drawer file says wins at idx on that stack — the spec resolveBinding is measured
  // against (hud.js:148): the first layer with a binding that is not transparent.
  function expectedBinding(data, idx, stack) {
    for (const name of stack) {
      const k = data.layers[name] && data.layers[name][idx];
      if (!k || k.type === "trans") continue;
      return { key: k, layer: name };
    }
    return null;
  }

  // What the drawer file says these keys do on that stack (hud.js:677): the topmost layer that
  // declares a combo on exactly this position set. No combo declared there means no pill.
  function expectedCombo(data, positions, stack) {
    const want = new Set(positions);
    const same = c => c.positions.length === want.size && c.positions.every(p => want.has(p));
    for (const name of stack) {
      const hit = data.combos.find(c => c.layers.includes(name) && same(c));
      if (hit) return hit;
    }
    return null;
  }

  // Every distinct position set any combo uses.
  function positionSets(data) {
    const seen = new Map();
    for (const c of data.combos) {
      const key = [...c.positions].sort((a, b) => a - b).join(",");
      if (!seen.has(key)) seen.set(key, [...c.positions].sort((a, b) => a - b));
    }
    return [...seen.values()];
  }

  // ---------- expectations, as the page would render them ----------

  /* A legend is the glyph markup when the message carries one for it, else the plain text
   * (hud.js:159). Comparing the markup rather than a parsed id keeps this exact for a real MDI
   * SVG and for the placeholder the fixture uses. */
  const escapeHTML = s => s.replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  function legendText(data, text, glyph) {
    const svg = glyph && data.glyphs && data.glyphs[glyph];
    if (!svg) return text || "";
    return '<span class="glyph">' + svg + "</span>" + (text ? escapeHTML(text) : "");
  }
  const expectedLegends = (data, key) => ({
    tap: legendText(data, key.tap, key.glyph),
    hold: legendText(data, key.hold, key.glyph_hold),
    shifted: legendText(data, key.shifted, key.glyph_shifted),
  });

  // ---------- the sweep ----------

  const T = (data, name) => (data.hud && data.hud[name] != null ? data.hud[name] : 400);
  const list = set => [...set].sort((a, b) => a - b).join(",");

  async function sweep(driver, data, opts) {
    opts = opts || {};
    const fail = [];
    const notes = [];
    let checked = 0;

    const { idsOf, unreachable, live } = layerIndex(data);
    const zmkPos = positionOf(data);
    const nKeys = data.layout.keys.length;
    const settle = Math.max(T(data, "combo_pill_ms"), T(data, "activator_ms"), T(data, "press_ms")) + 400;

    const add = (check, where, expected, actual, extra) => {
      fail.push(Object.assign({ check, where, expected, actual }, extra || {}));
    };

    await driver.load(data);

    // 0. Legend hygiene, straight from the message: a legend that is partly a $$glyph$$ marker is
    //    not a glyph and not text — it reaches the key as literal characters.
    for (const name of data.layer_order) {
      data.layers[name].forEach((k, idx) => {
        for (const f of ["tap", "hold", "shifted"]) {
          checked++;
          if (k[f] && k[f].includes("$$")) add("legend-marker", `${name}[${idx}].${f}`, "no $$ marker", k[f]);
        }
      });
    }
    data.combos.forEach((c, i) => {
      for (const f of ["tap", "hold", "shifted"]) {
        checked++;
        if (c.key[f] && c.key[f].includes("$$")) add("legend-marker", `combo#${i} ${c.positions}.${f}`, "no $$ marker", c.key[f]);
      }
    });
    for (const name of unreachable) {
      notes.push(`drawer layer "${name}" has no ZMK layer id: the page can never show it`);
    }
    for (const [i, c] of data.combos.entries()) {
      if (!c.layers.some(l => idsOf.has(l))) {
        notes.push(`combo #${i} ${JSON.stringify(c.key.tap)} on [${c.layers}] can never be reached: no ZMK id draws those layers`);
      }
    }

    // 1. Legends: with one layer live, every key shows what the drawer file says wins there.
    for (const layer of live) {
      await driver.reset(idsOf.get(layer)[0], layer === data.base);
      const stack = stackFor(data, layer);
      const drawn = await driver.legends();
      for (let idx = 0; idx < nKeys; idx++) {
        checked++;
        const r = expectedBinding(data, idx, stack);
        const want = r ? expectedLegends(data, r.key) : { tap: "", hold: "", shifted: "" };
        const got = drawn[idx];
        for (const f of ["tap", "hold", "shifted"]) {
          if (want[f] !== got[f]) add("legend", `${layer}[${idx}].${f}`, want[f], got[f]);
        }
      }
    }

    // 2. Highlight: a press lights exactly its own key, a release lets it go.
    for (const layer of live) {
      for (let idx = 0; idx < nKeys; idx++) {
        checked++;
        await driver.reset(idsOf.get(layer)[0], layer === data.base);
        const pos = zmkPos(idx);
        await driver.press(pos);
        const lit = await driver.lit();
        if (list(lit) !== String(idx)) add("press", `${layer} pos ${pos} (key ${idx})`, String(idx), list(lit));
        await driver.release(pos);
        await driver.advance(T(data, "release_ms") + 50);
        const after = await driver.lit();
        if (after.length) add("release", `${layer} pos ${pos} (key ${idx})`, "", list(after));
      }
    }

    // 3. A position the keymap does not place must light nothing — not the key that happens to
    //    share its number.
    for (const pos of unmappedPositions(data)) {
      checked++;
      await driver.reset(idsOf.get(data.base) ? idsOf.get(data.base)[0] : 0, true);
      await driver.press(pos);
      const lit = await driver.lit();
      if (lit.length) add("unmapped-position", `unmapped ZMK position ${pos}`, "", list(lit));
      await driver.release(pos);
      await driver.advance(settle);
    }

    // 4. Combos, in the order a keyboard actually reports them. Every position set is tried on
    //    every live layer, so the same loop asserts "draws the right combo" and "draws none when
    //    none is declared".
    //
    //    Two orderings, because a real chord is not the idealised one. `clean` is the demo's
    //    order: the layer is already up, then the keys. `held` is what a thumb does — press the
    //    layer key, the layer arrives, and the chord follows a few ms later, while the thumb is
    //    still down. A combo that only works when nothing preceded it is still broken.
    const activatorOf = layer => {
      const a = (data.activators || []).filter(x => x.layer === layer);
      return a.length ? a[0].idx : null;
    };
    const term = (data.combo_term || 50) + T(data, "combo_slack_ms");
    const orderings = layer => {
      const act = layer === data.base ? null : activatorOf(layer);
      const list = [{ name: "clean", act: null, gap: 0 }];
      if (act !== null) {
        list.push({ name: "held+" + (term + 10) + "ms", act, gap: term + 10 });
        list.push({ name: "held+20ms", act, gap: 20 });
      }
      return list;
    };

    for (const layer of live) {
      const stack = stackFor(data, layer);
      const ids = idsOf.get(layer)[0];
      for (const positions of positionSets(data)) {
        const want = expectedCombo(data, positions, stack);
        // The held orderings only say something about a combo that is declared here; a phantom
        // is a phantom whatever preceded it, and `clean` already covers that.
        for (const order of want ? orderings(layer) : [{ name: "clean", act: null, gap: 0 }]) {
          if (order.act !== null && positions.includes(order.act)) continue;  // the thumb is in the combo
          checked++;
          await driver.reset(null, false);
          const held = [];
          // The thumb's position and the layer it turned on are two separate HID reports.
          if (order.act !== null) { await driver.press(zmkPos(order.act)); held.push(order.act); await driver.advance(2); }
          await driver.setLayers(layer === data.base ? [] : [ids]);
          if (order.gap) await driver.advance(order.gap);
          for (const idx of positions) await driver.press(zmkPos(idx));

          const pills = await driver.pills();
          const lit = await driver.lit();
          const where = `${layer} [${positions}] ${order.name}`;
          if (!want) {
            if (pills.length) add("combo-phantom", where, "no pill", JSON.stringify(pills.map(p => p.tap)));
          } else if (pills.length !== 1) {
            add("combo-pill", where, "1 pill " + JSON.stringify(want.key.tap), pills.length + " pills");
          } else {
            const e = expectedLegends(data, want.key);
            const sub = e.hold || e.shifted;
            if (pills[0].tap !== e.tap) add("combo-label", where, e.tap, pills[0].tap);
            if (pills[0].sub !== sub) add("combo-sub", where, sub, pills[0].sub);
            // The activator stays lit while it is held: that is the key the user is pressing.
            const expectLit = list([...want.positions, ...held]);
            if (list(lit) !== expectLit) add("combo-keys", where, expectLit, list(lit));
          }
          for (const idx of positions) await driver.release(zmkPos(idx));
          for (const idx of held) await driver.release(zmkPos(idx));
          await driver.advance(settle);
        }
      }
    }

    // 5. Two layers live at once. Which combo wins is liveStack()'s call (the highest ZMK id), and
    //    whether that is the right call is a keymap question, so these are reported, never failed.
    const contested = [];
    for (const positions of positionSets(data)) {
      const want = new Set(positions);
      const on = data.combos.filter(c => c.positions.length === want.size && c.positions.every(p => want.has(p)));
      const layers = [...new Set(on.flatMap(c => c.layers).filter(l => idsOf.has(l) && l !== data.base))];
      if (layers.length < 2) continue;
      const pair = layers.slice(-2);
      await driver.reset(null, false);
      await driver.setLayers(pair.map(l => idsOf.get(l)[0]));
      for (const idx of positions) await driver.press(zmkPos(idx));
      const pills = await driver.pills();
      contested.push({
        positions, holding: pair.map(l => `${l}#${idsOf.get(l)[0]}`),
        shows: pills.length ? pills[0].tap : null,
        candidates: on.filter(c => c.layers.some(l => pair.includes(l))).map(c => c.key.tap),
      });
      for (const idx of positions) await driver.release(zmkPos(idx));
      await driver.advance(settle);
    }

    return { fail, notes, contested, checked, live, unreachable };
  }

  return { sweep, layerIndex, positionOf, positionSets, expectedBinding, expectedCombo,
           expectedLegends, legendText, stackFor, unmappedPositions };
});
