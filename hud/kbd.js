/* zmk-layer-hud — the touch keyboard for a native shell: the virtual firmware (engine.js), what its
 * reports type (hidtext.js) and what the board looks like (view.js), with no DOM and no timer. The
 * iPad keyboard runs it in JavaScriptCore and draws the board itself (apps/ios).
 *
 *   const kbd = ZmkKbd.create(keymapMessage, {shortcuts: false})
 *   kbd.down(idx, t) / kbd.up(idx, t)    a finger on / off the drawer key idx, at t ms
 *   kbd.tick(t)                          when the last answer's `next` comes round
 *   kbd.releaseAll(t)                    the keyboard went away: everything up
 *   -> {ops, refused, changed, next}
 *
 * `ops` are text ops (hidtext.textEvents: {kind:"text"|"key"|"chord", ...}) for the field, `refused`
 * the keys that cannot type here (to flash), `changed` whether kbd.board() would now say something
 * else, and `next` the time to call tick() at, or null. kbd.board() is the banner and every key's
 * face (view.js board), kbd.geometry(width, maxHeight, split) where the keys go, and
 * kbd.chips(width, maxHeight, split) the combos to draw as chips there, which kbd.chord(i, down, t)
 * presses.
 *
 * A finger is a key index the shell hit-tested; a key the firmware reports no position for is
 * drawn and never typed. The same rules as the page's touch.js, which drives the DOM board.
 */
(function (root) {
  "use strict";
  const node = typeof module === "object" && module.exports;
  const Engine = node ? require("./engine.js") : root.ZmkEngine;
  const HidText = node ? require("./hidtext.js") : root.ZmkHidText;
  const View = node ? require("./view.js") : root.ZmkView;
  const Tables = node ? require("./hidtables.js") : root.ZmkHidTables;

  // The gap between keys a native board starts from before docking scales it (hud.js GAP).
  const GAP = 6;

  function create(message, options) {
    const data = typeof message === "string" ? JSON.parse(message) : message;
    const opts = Object.assign({ shortcuts: false, repeat: "synthetic" }, options || {});
    if (!data || data.kind !== "keymap") throw new Error("not a keymap message");
    if (!data.actions || data.actions.error) throw new Error((data.actions && data.actions.error) || "this keymap says nothing of what its keys do");
    const touch = Object.assign({}, data.touch || {});
    const engine = Engine.create(data.actions, { touch, sink: { repeat: opts.repeat, pages: [7], mouse: "none" } });
    const decoder = HidText.decoder({ altgr: !!data.altgr });
    const deadMs = (Tables ? Tables.DEAD_KEY_MS : 60) + 5;

    // Drawer index <-> ZMK position (data.positions: position -> index). A message with no map at
    // all (a hand-written one) means position = index.
    const positions = data.positions || {};
    const mapped = Object.keys(positions).length > 0;
    const posOfIdx = new Map();
    for (const [pos, idx] of Object.entries(positions)) posOfIdx.set(idx, Number(pos));
    const posOf = (idx) => (mapped ? (posOfIdx.has(idx) ? posOfIdx.get(idx) : null) : idx);
    const idxOf = (pos) => (mapped ? (positions[String(pos)] !== undefined ? positions[String(pos)] : null) : pos);

    const view = { ids: engine.state().layers, down: [], latched: {}, mods: 0 };
    const fingers = new Map();       // idx -> how many fingers are on it
    const chipsDown = new Map();     // combo -> the drawer keys it presses, while its chip is held
    let deadAt = null;

    function note() {
      const states = Object.values(view.latched);
      if (states.includes("locked")) return "touch · locked";
      if (states.includes("oneshot")) return "touch · one shot";
      if (fingers.size && view.ids && view.ids[0] !== (data.actions.default || 0)) return "touch · held";
      return "";
    }

    function textOf(keyEvents) {
      return HidText.textEvents(keyEvents, { shortcuts: opts.shortcuts });
    }

    function answer(events, t, changed) {
      const out = { ops: [], refused: [], changed: !!changed, next: null };
      for (const e of events) {
        if (e.kind === "report") {
          view.mods = e.mods;
          out.ops.push(...textOf(decoder.feed(e, e.t)));
          deadAt = decoder.pending() !== null ? e.t + deadMs : null;
          out.changed = true;
        } else if (e.kind === "layers") {
          view.ids = e.ids;
          out.changed = true;
        } else if (e.kind === "latches") {
          const latched = {};
          for (const l of e.latches) { const i = idxOf(l.pos); if (i != null) latched[i] = l.state; }
          view.latched = latched;
          out.changed = true;
        } else if (e.kind === "unsupported" && e.input != null) {
          const i = idxOf(e.input);
          if (i != null) out.refused.push(i);
        }
      }
      const due = [engine.nextDeadline(), deadAt].filter((x) => x !== null);
      out.next = due.length ? Math.min(...due) : null;
      return out;
    }

    // The methods below use no `this`: a shell may call them detached (JSKbd applies them to null).
    function geometry(width, maxHeight, split) {
      return View.geometry(data.layout, { width, gap: GAP, chrome: 0, dock: { maxHeight, split: split !== false } });
    }

    function setDown() {
      view.down = [...new Set([...fingers.keys()].concat(...chipsDown.values()))];
      view.chips = [...chipsDown.keys()];
    }

    function comboKeys(i) {
      const c = (data.actions.combos || [])[i];
      return c ? c.positions.map(idxOf).filter((x) => x != null) : [];
    }

    return {
      down(idx, t) {
        const pos = posOf(idx);
        if (pos === null || pos === undefined) return answer([], t, false);
        fingers.set(idx, (fingers.get(idx) || 0) + 1);
        setDown();
        return answer(engine.down({ pos }, t), t, true);
      },
      up(idx, t) {
        const pos = posOf(idx);
        if (pos === null || pos === undefined || !fingers.has(idx)) return answer([], t, false);
        const n = fingers.get(idx) - 1;
        if (n > 0) { fingers.set(idx, n); return answer([], t, false); }
        fingers.delete(idx);
        setDown();
        return answer(engine.up({ pos }, t), t, true);
      },
      // A combo chip (P10's chips): its index in data.actions.combos.
      chord(i, isDown, t) {
        if (isDown) chipsDown.set(i, comboKeys(i));
        else if (!chipsDown.delete(i)) return answer([], t, false);
        setDown();
        return answer(isDown ? engine.down({ combo: i }, t) : engine.up({ combo: i }, t), t, true);
      },
      tick(t) {
        const out = answer(engine.tick(t), t, false);
        if (deadAt !== null && t >= deadAt) {
          deadAt = null;
          out.ops.push(...textOf(decoder.flush(t)));
          const due = [engine.nextDeadline()].filter((x) => x !== null);
          out.next = due.length ? due[0] : null;
        }
        return out;
      },
      releaseAll(t) {
        fingers.clear();
        chipsDown.clear();
        setDown();
        const out = answer(engine.releaseAll(t), t, true);
        out.ops.push(...textOf(decoder.flush(null)));
        deadAt = null;
        return out;
      },
      board() {
        return View.board(data, Object.assign({}, view, { note: note() }));
      },
      geometry,
      chips(width, maxHeight, split) {
        if ((data.touch || {}).combos !== "chips") return [];
        return View.chips(data, geometry(width, maxHeight, split), view);
      },
      title() { return data.title || ""; },
    };
  }

  const ZmkKbd = { create };
  if (node) module.exports = ZmkKbd;
  else root.ZmkKbd = ZmkKbd;
})(typeof globalThis !== "undefined" ? globalThis : this);
