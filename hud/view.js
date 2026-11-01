/* zmk-layer-hud — which layer each key's legend comes from, and where a reported position is on the
 * board, without a DOM. hud.js draws the page with these answers, and keystrokes.js reads the
 * keyboard's messages with the same ones.
 *
 * Everything here is a function of the keymap message (host/keymap.py) and of what the caller holds:
 * nothing is kept, no timer, no DOM.
 */
(function (root) {
  "use strict";

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

  // The drawer key at a reported ZMK position. A position the keymap does not place is no key (-1):
  // lighting the key that happens to share the number is worse than lighting none, and the feed
  // already says so. A message with no map at all (a hand-written demo) still means position = index.
  function idxAt(data, pos) {
    const map = (data && data.positions) || {};
    if (map[String(pos)] !== undefined) return map[String(pos)];
    return Object.keys(map).length ? -1 : Number(pos);
  }

  // Each drawer key's ZMK position, which is what a session is kept by: the message's map inverted
  // (a message with none is a keymap in the firmware's own order, where they are the same).
  function posOf(data) {
    const map = data.positions || {};
    const out = [];
    for (const [pos, idx] of Object.entries(map)) out[idx] = Number(pos);
    if (!Object.keys(map).length) data.layout.keys.forEach((k, idx) => { out[idx] = idx; });
    return out;
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

  const ZmkView = { zl, drawnStack, idxAt, posOf, resolve };
  if (typeof module === "object" && module.exports) module.exports = ZmkView;
  else root.ZmkView = ZmkView;
})(typeof globalThis !== "undefined" ? globalThis : this);
