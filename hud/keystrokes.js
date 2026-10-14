/* zmk-layer-hud — the keystrokes: what the keyboard's messages say was struck, without a DOM.
 *
 * The feed's messages go in, one at a time, each with the time it arrived; what they mean comes
 * out. A keystroke is one strike the board draws and counts once (GLOSSARY.md): a key, a combo, a
 * tapped hold-tap. Which layers are up, which keys and combos were struck, what a report alone
 * typed and where -- the reading of the messages lives here, and hud.js paints what it says.
 *
 *   const ks = ZmkKeystrokes.create();
 *   ks.read(message, now)    a feed message: keymap, layers (ids; null: no longer reported), touch
 *                            (the touch keyboard's layer ids; null: off). Other kinds are ignored.
 *   ks.tick(now)             nothing new: only what has come due by now
 *       -> { board, said, label, next }
 *          board  what the board shows as read: { ready, layers (top first, the base last), live
 *                 ({ids, at} or null), oneShot, momentary (inferred layers) }
 *          said   what happened since the last call, in order, never taken back:
 *                 { say: "layers", ids }  the keyboard's layers applied (late, if held back)
 *          next   when to call tick again, or null
 *   ZmkKeystrokes.play(feed, until)   [[t, message], ...] read on a clock of its own, ticked up to
 *                                     `until`: { board, said } (tests, replays)
 *
 * Nothing here keeps time: `now` is an argument, whatever is due at or before it runs first, and
 * `next` says when something will be. No DOM, no Date, no timers, no console, as in engine.js: the
 * same file runs in the page and under node's tests. Which layer a key resolves on comes from
 * view.js. Time is taken as given, never clamped: a GIF still dates a held key back (hud.js
 * demoFrame), and it is read as of then.
 */
(function (root) {
  "use strict";
  const View = (typeof module === "object" && module.exports) ? require("./view.js") : root.ZmkView;

  // The timings the reading uses, from the config's `hud:` section (host/keymap.py fills the
  // defaults in); these are only the fallbacks for a keymap message without it. hud.js takes them
  // into its own.
  const DEFAULTS = { press_ms: 320, release_ms: 60, held_timeout_ms: 5000, momentary_ms: 700, combo_slack_ms: 20,
    activator_ms: 400, positions_fresh_ms: 3000, combo_pill_ms: 1000, sequence_ms: 200, sequence_max: 6,
    one_shot_ms: 450 };

  function create() {
    // What has been read. hud.js reaches into it (_s) until the page is only its caller.
    const s = {
      data: null,
      live: null,               // {ids, at} once the keyboard has reported its layers
      baseLayers: [],           // emulated: just the base layer
      momentary: [],            // [{layer, until}]  (inference only)
      oneShot: null,            // layer name        (inference only)
      touch: null,              // the touch keyboard's layer ids, top first, while it is up
      activatorOf: {},          // drawn layer -> the key idx that brought it in (positions)
      drawnSince: {},           // drawn layer -> when it appeared (applyLayers)
      pendingLayers: null,      // a layer change held back for a flash (setLayers)
      pendingDue: null,         // ...and when it applies, fixed at the first drop
      held: new Set(),          // key idx currently down, by reported position
      recentPos: [],            // the presses of the last activator_ms or so: {idx, t, entry}
      ledger: [],               // keystrokes drawn and not counted yet
      lastKeyAt: 0,             // when the last key went down, by position or report
    };
    let said = [];

    const T = name => (s.data && s.data.hud && s.data.hud[name] != null) ? s.data.hud[name] : DEFAULTS[name];
    const base = () => (s.data ? s.data.base : null);
    const zl = id => View.zl(s.data, id);
    const comboTerm = () => (s.data.combo_term || 50) + T("combo_slack_ms");
    const activatorsOf = layer => s.data.activators.filter(a => a.layer === layer).map(a => a.idx);

    // ---------- what is due ----------

    // Things that happen later, each at its time: the deadline name -> {at, seq, fn}. A name is
    // armed again by replacing it; `once` gives a name of its own to each.
    const due = new Map();
    let seq = 0;
    function after(name, at, fn) { due.set(name, { at, seq: ++seq, fn }); }
    function once(at, fn) { after("once:" + (seq + 1), at, fn); }
    function cancel(name) { due.delete(name); }
    function nextAt() {
      let n = null;
      for (const d of due.values()) if (n === null || d.at < n) n = d.at;
      return n;
    }
    function runDue(now) {
      for (;;) {
        let first = null, name = null;
        for (const [k, d] of due) {
          if (d.at <= now && (!first || d.at < first.at || (d.at === first.at && d.seq < first.seq))) { first = d; name = k; }
        }
        if (!first) return;
        due.delete(name);
        first.fn(first.at);
      }
    }

    // ---------- the layers ----------

    // The live stack as drawer layer names, top first: higher ZMK ids win, undrawn layers are
    // skipped, duplicates (two ids drawn by one layer) collapse; the base layer is always last.
    function liveStack() { return View.drawnStack(s.data, s.live.ids.slice().sort((a, b) => b - a)); }

    function baseStack() {
      // top first
      if (s.live) return liveStack();
      const out = [];
      for (let i = s.baseLayers.length - 1; i >= 0; i--) out.push(s.baseLayers[i]);
      if (!out.includes(base())) out.push(base());
      return out;
    }

    // What a key resolves on: the touch keyboard's layers while it is up (touch.js: the engine's,
    // which takes the keyboard's own while nothing on glass holds one), else an inferred one-shot
    // or held layer over the keyboard's own.
    function stack() {
      if (s.touch) return View.drawnStack(s.data, s.touch);
      const out = [];
      if (s.oneShot) out.push(s.oneShot);
      for (let i = s.momentary.length - 1; i >= 0; i--) out.push(s.momentary[i].layer);
      for (const name of baseStack()) if (!out.includes(name)) out.push(name);
      return out;
    }

    /* ZMK sends a layer's frame after the key that decided its hold: a hold-tap turns into a hold
     * when the next key goes down, and that key's position is sent first. Counted on the stack it
     * went down on, the first key typed on every held layer would count on the one below. So when a
     * layer comes up, a press still waiting that followed the key now holding it is moved onto the
     * new stack. A layer going away moves nothing: the key before it was typed with it up. */
    function recredit(gained, now) {
      const term = comboTerm(), layers = stack();
      for (const e of s.ledger) {
        if (e.kind !== "press" || e.cancelled || now - e.t > term) continue;
        if (gained.some(p => e.t > p.t && e.idx !== p.idx)) e.stack = layers;
      }
    }

    function applyLayers(ids, now) {
      // Which key brought each new drawn layer in: the position pressed just before (its own
      // activator among the candidates), so an alternate activator elsewhere stays dark.
      const wasDrawn = s.live ? new Set(s.live.ids.map(id => (zl(id) || {}).drawer).filter(Boolean)) : new Set();
      for (const id of ids) {
        const name = (zl(id) || {}).drawer;
        if (!name || wasDrawn.has(name)) continue;
        s.drawnSince[name] = now;
        if (s.activatorOf[name] != null) continue;
        // Prefer a key the drawer marks as reaching this layer; else the key pressed right
        // before the layer appeared is the one holding it (a thumb whose legend says otherwise).
        const candidates = activatorsOf(name);
        // Still down: a key that was tapped and let go is not what is holding this layer, even
        // if it was the last thing pressed before the layer arrived.
        const fresh = [...s.recentPos].reverse().filter(p => now - p.t < T("activator_ms") && s.held.has(p.idx));
        const press = fresh.find(p => candidates.includes(p.idx)) || fresh[0];
        if (press) {
          s.activatorOf[name] = press.idx;
          if (press.entry) press.entry.activator = true;     // brought a layer up: held, if another key went down (strokes)
        }
      }
      const drawnNow = new Set(ids.map(id => (zl(id) || {}).drawer).filter(Boolean));
      // The presses of the keys now holding a layer that just came up (recredit).
      const gained = [];
      for (const name of drawnNow) {
        if (wasDrawn.has(name) || s.activatorOf[name] == null) continue;
        const p = [...s.recentPos].reverse().find(q => q.idx === s.activatorOf[name]);
        if (p) gained.push(p);
      }
      for (const name of Object.keys(s.activatorOf)) if (!drawnNow.has(name)) delete s.activatorOf[name];
      for (const name of Object.keys(s.drawnSince)) if (!drawnNow.has(name)) delete s.drawnSince[name];
      s.live = { ids, at: now };
      s.momentary = []; s.oneShot = null;
      if (gained.length) recredit(gained, now);
      said.push({ say: "layers", ids });
    }

    /* A layer change held back for a flash is the keyboard's word all the same, and the moment
     * another key goes down it applies, so that key resolves on the layers really up. Held back
     * any longer, a one-shot layer outlived its key for as long as typing went on: every keystroke
     * within press_ms of the last re-armed the delay, and at 170 ms a key the chord after an Alpha 2
     * letter drew Alpha 2's combo -- "wax" drew its x as "-", "quick" its k as a dead quote. */
    function flushLayers(now) {
      if (!s.pendingLayers) return;
      const ids = s.pendingLayers;
      s.pendingLayers = null; s.pendingDue = null;
      cancel("layers");
      applyLayers(ids, now);
    }

    // The keyboard's active ZMK layer ids (layer 0 omitted). Clears every inference: from now on
    // the stack is what the keyboard says.
    function setLayers(ids, now) {
      ids = ids.map(Number).filter(n => Number.isInteger(n) && n > 0);
      // The change already held back, said again (a heartbeat, a re-assertion): it is on its way
      // and keeps its deadline. Starting the wait over each time kept a dropped layer up for as
      // long as the repeats kept coming.
      const sameIds = (a, b) => a.length === b.length && a.every(x => b.includes(x));
      if (s.pendingLayers && sameIds(s.pendingLayers, ids)) return;
      cancel("layers");
      // A one-shot layer leaves right after the key it served (and may enter another, undrawn
      // one). Keep the board on the drawn layers while the key's flash is visible, otherwise the
      // flash appears under the wrong legends. Layers that only appear apply at once.
      const since = now - (s.lastKeyAt || 0);
      // A drop is due press_ms after the key it followed, and stays due then: a change arriving
      // while it waits replaces what will apply, never when. Measured afresh each time, a run of
      // changes a keystroke apart kept the first layer up for as long as the run lasted.
      const at = s.pendingLayers ? s.pendingDue : now + T("press_ms") - since;
      s.pendingLayers = null; s.pendingDue = null;
      if (s.live && at > now) {
        const drawn = set => new Set(set.map(id => (zl(id) || {}).drawer).filter(Boolean));
        const before = drawn(s.live.ids), after_ = drawn(ids);
        const losesDrawn = [...before].some(name => !after_.has(name));
        // Only a layer falling away is worth holding back. One that brings another drawn layer
        // up means the next key is already on it -- ç on Alpha 2, then ão on the Ç extension --
        // and holding the whole change back left the macro looking on Alpha 2, where it is not.
        const gainsDrawn = [...after_].some(name => !before.has(name));
        if (losesDrawn && !gainsDrawn) {
          // Held back for the flash only: when it is due it applies outright rather than asking
          // again, and the next key to go down applies it first (flushLayers).
          s.pendingLayers = ids; s.pendingDue = at;
          after("layers", at, when => { s.pendingLayers = null; s.pendingDue = null; applyLayers(ids, when); });
          return;
        }
      }
      applyLayers(ids, now);
    }

    // The keymap message. Re-sent by the host when a sync rewrites it: the live layer set is kept,
    // what was read on the keymap going away is not.
    function load(data) {
      if (!data.layout || !data.layers) return;
      s.data = data;
      s.momentary = []; s.oneShot = null;
      s.activatorOf = {}; s.drawnSince = {}; s.held.clear();
      s.baseLayers = [data.base];
    }

    // ---------- the answer ----------

    function board() {
      if (!s.data) return { ready: false, layers: [], live: s.live, oneShot: null, momentary: [] };
      return { ready: true, layers: stack(), live: s.live, oneShot: s.oneShot, momentary: s.momentary.map(m => m.layer) };
    }
    function answer() {
      const out = { board: board(), said, label: null, next: nextAt() };
      said = [];
      return out;
    }

    function read(m, now) {
      if (typeof now !== "number" || !isFinite(now)) throw new TypeError("keystrokes: read needs the time it is");
      runDue(now);
      if (m && typeof m === "object") {
        if (m.kind === "keymap") load(m);
        else if (m.kind === "touch") s.touch = Array.isArray(m.ids) ? m.ids.slice() : null;
        else if (m.kind === "layers" && s.data) {
          if (m.ids === null) s.live = null;          // the keyboard's layers are no longer reported
          else if (Array.isArray(m.ids)) setLayers(m.ids, now);
        }
      }
      return answer();
    }
    function tick(now) {
      if (typeof now !== "number" || !isFinite(now)) throw new TypeError("keystrokes: tick needs the time it is");
      runDue(now);
      return answer();
    }

    return {
      read, tick,
      // hud.js, until it is only the caller: the reading itself, and the steps it still takes.
      _s: s, _stack: stack, _liveStack: liveStack, _baseStack: baseStack,
      _flush(now) { runDue(now); flushLayers(now); return answer(); },
      _once: once,
    };
  }

  // A feed played on a clock of its own: each message read at its time, everything due in between
  // ticked at its own, and the clock stopped at `until`.
  function play(feed, until) {
    const ks = create();
    const said = [];
    let out = null;
    const catchUp = t => {
      for (;;) {
        const n = out && out.next;
        if (n === null || n === undefined || n > t) return;
        out = ks.tick(n); said.push(...out.said);
      }
    };
    for (const [t, msg] of feed) {
      if (t > until) break;
      catchUp(t);
      out = ks.read(msg, t); said.push(...out.said);
    }
    catchUp(until);
    out = ks.tick(until); said.push(...out.said);
    return { board: out.board, said };
  }

  const ZmkKeystrokes = { create, play, DEFAULTS };
  if (typeof module === "object" && module.exports) module.exports = ZmkKeystrokes;
  else root.ZmkKeystrokes = ZmkKeystrokes;
})(typeof globalThis !== "undefined" ? globalThis : this);
