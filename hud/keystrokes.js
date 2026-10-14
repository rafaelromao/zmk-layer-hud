/* zmk-layer-hud — the keystrokes: what the keyboard's messages say was struck, without a DOM.
 *
 * The feed's messages go in, one at a time, each with the time it arrived; what they mean comes
 * out. A keystroke is one strike the board draws and counts once (GLOSSARY.md): a key, a combo, a
 * tapped hold-tap. Which layers are up, which keys and combos were struck, what a report alone
 * typed and where -- the reading of the messages lives here, and hud.js paints what it says.
 *
 *   const ks = ZmkKeystrokes.create();
 *   ks.read(message, now)    a feed message: keymap, layers (ids; null: no longer reported), press
 *                            and release (pos, and `sent` for a press sent in), key (a modifier
 *                            change, flagsChanged), touch (the touch keyboard's layer ids; null: off).
 *                            Other kinds are ignored.
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
      posAt: 0,                 // when the last position went down
      lastPress: null,          // the last press, for a keystroke the feed cannot name
      comboEntry: null,         // the ledger's entry for the combo whose pill is up
      down: new Map(),          // key idx -> the ledger entry of its press, while it is down
      posOf: [],                // drawer idx -> ZMK position
      mods: {},                 // modifier flag -> true while held
      modSource: {},            // modifier flag -> the key idx that turned it on, null: none known (positions)
    };
    let said = [];
    // What hud.js still draws itself, in order, until the board says it: a key down, the keys
    // drawn again, a new chord group, a combo, a key up.
    let paint = [];

    const T = name => (s.data && s.data.hud && s.data.hud[name] != null) ? s.data.hud[name] : DEFAULTS[name];
    const base = () => (s.data ? s.data.base : null);
    const zl = id => View.zl(s.data, id);
    const comboTerm = () => (s.data.combo_term || 50) + T("combo_slack_ms");
    const activatorsOf = layer => s.data.activators.filter(a => a.layer === layer).map(a => a.idx);
    const resolve = (idx, layers) => View.resolve(s.data, idx, layers);
    // Modifier flags -> the glyph a hold legend uses for them.
    const MOD_GLYPH = { shift: "⇧", ctrl: "⌃", alt: "⌥", cmd: "⌘" };
    // A key that carries a modifier: a home-row mod (hold legend) or the modifier key itself (tap).
    const carries = (key, glyph) => !!key && ((key.hold && key.hold.includes(glyph)) || key.tap === glyph);
    // The drawer key's ZMK position, which is what a session is kept by; null for a key the keymap
    // places nowhere the firmware could report.
    const zmkPos = idx => (s.posOf[idx] !== undefined ? s.posOf[idx] : null);
    const comboKey = idxs => idxs.map(zmkPos).sort((a, b) => a - b).join(",");
    // A keystroke drawn, to be counted once nothing can take it back (hud.js counts it).
    const ledgerAdd = entry => { s.ledger.push(entry); return entry; };

    // The drawer key at a reported ZMK position. A position the keymap does not place lights
    // nothing: lighting the key that happens to share the number is worse than lighting none, and
    // the feed already says so. A message with no map at all (a hand-written demo) still means
    // position = index.
    function idxAt(pos) {
      const map = (s.data && s.data.positions) || {};
      if (map[String(pos)] !== undefined) return map[String(pos)];
      return Object.keys(map).length ? -1 : Number(pos);
    }
    const onBoard = idx => idx >= 0 && idx < s.data.layout.keys.length;

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

    // ---------- the positions ----------

    // Firmware `positions;`: the physical key at ZMK position `pos` went down. The one exact source
    // for what to light, whatever the key produced (chords, combos, macros, modifiers, layer keys).
    // Two or more positions within a combo term that form a combo draw its pill. `sent`: a press
    // sent in, lit the same and never counted into a session.
    function press(pos, sent, now) {
      const idx = idxAt(pos);
      // A key going down is a keystroke of its own: a layer change held back for the previous
      // one's flash is the keyboard's state now, and this key resolves on it.
      flushLayers(now);
      // The keyboard's own combo term (config combo_term_ms) plus slack for the reports' travel.
      const term = comboTerm();
      // A guess still waiting to be counted is not: with positions coming, this keystroke counts by
      // its position, and so does the one the guess was for.
      for (const e of s.ledger) if (e.kind === "guess") e.cancelled = true;
      s.posAt = now; s.lastKeyAt = now;
      if (!onBoard(idx)) return;
      s.held.add(idx);
      const entry = { kind: "press", idx, pos: Number(pos), t: now, due: now + term, stack: stack(), eligible: !sent };
      s.lastPress = entry;              // for a keystroke the feed cannot name (stripEvent)
      // The layer set and the key that brought it up are two reports, in no promised order. When
      // the key comes second, setLayers had nothing to attribute the layer to: if the drawer says
      // this key reaches a live layer and nothing is recorded as holding it, this is what did --
      // if the layer appeared at the same moment. One already up (a one-shot tapped earlier) came
      // with an earlier keystroke, and this press belongs to whatever chord it arrives in: with
      // Alpha 2 waiting, the 0 chord that uses Alpha 2's own key drew nothing.
      let redraw = false;
      if (s.live) {
        for (const name of liveStack()) {
          if (name === base() || s.activatorOf[name] != null) continue;
          if (now - (s.drawnSince[name] || 0) > term) continue;
          if (activatorsOf(name).includes(idx)) {
            s.activatorOf[name] = idx;
            entry.activator = true;
            redraw = true;                // marked as what brought it up, its alternates no longer
            // It went down on the layers below the one it brought up, and counts there.
            entry.stack = entry.stack.filter(l => l !== name);
          }
        }
      }
      // Whether it is a keystroke of its own is for its release to say, if it can be held (strokes).
      const r = resolve(idx, entry.stack);
      entry.holder = !!(r && r.key.hold) || (s.data.activators || []).some(a => a.idx === idx);
      for (const d of s.down.values()) d.overlapped = true;      // down under another key: maybe held (strokeKind)
      s.down.set(idx, entry);
      ledgerAdd(entry);
      paint.push({ op: "down", idx, pos: Number(pos), t: now });
      // Older presses stay in the list for the activator lookup (setLayers); the combo group is
      // the trailing run of presses that started within the term of this one.
      const keepMs = Math.max(T("activator_ms"), s.data.combo_idle || 0);   // the idle rule below looks back this far
      while (s.recentPos.length && now - s.recentPos[0].t > keepMs) s.recentPos.shift();
      s.recentPos.push({ idx, t: now, entry });
      // A modifier reported before the key that turned it on: this is that key, if it carries it.
      for (const f of Object.keys(s.modSource)) {
        if (s.modSource[f] === null && r && carries(r.key, MOD_GLYPH[f])) { s.modSource[f] = idx; redraw = true; }
      }
      if (redraw) paint.push({ op: "keys" });
      // The keyboard has already decided. A key still down that is what brought one of the live
      // layers up was treated by ZMK as a layer hold, not as part of a chord — had it been half
      // of a combo, the combo would have fired and the layer would not have changed. So the group
      // stops there, however recently it was pressed. Without this the thumb joins the burst and
      // the chord after it is either missed or drawn as whichever larger combo happens to contain
      // that thumb: holding Alpha 2 and chording backspace drew Tab ([4,5] became [4,5,22]).
      // …but only a key pressed before this burst. ZMK releases a combo's captured positions
      // together, so they arrive in the same report and share a timestamp; a key that a combo
      // both holds a layer with and uses as one of its own (a sticky layer's own key) is still
      // part of the chord it arrived with.
      const holdsALayer = i => s.held.has(i) && Object.values(s.activatorOf).includes(i);
      // ...and a key already let go ends it too. ZMK cancels a combo whose key comes up before the
      // combo is complete and types the keys one by one, which is what a fast roll across a combo's
      // keys is: the first key is up before the next goes down. Drawn by timing alone, every such
      // roll was a pill, and a combo in the counts.
      const recentPos = s.recentPos;
      let start = recentPos.length - 1;
      while (start > 0) {
        const prev = recentPos[start - 1];
        if (now - prev.t > term || !s.held.has(prev.idx) || (prev.t < now && holdsALayer(prev.idx))) break;
        start--;
      }
      if (start === recentPos.length - 1) { s.comboEntry = null; paint.push({ op: "group" }); } // a new group begins
      // Only presses within the keymap's combo term form a combo: ZMK's combo module releases the
      // captured positions together when a combo completes, so they arrive within the term. A key
      // pressed later while a layer is held is that layer's key, never a combo with the holder.
      const pressedSet = recentPos.slice(start).map(p => p.idx);
      // A keymap that asks for idle before a combo (ZMK require-prior-idle-ms, data.combo_idle)
      // gets none from a chord that starts sooner than that after another key was struck: ZMK
      // types it as its keys. ZMK times it from the last key that typed something, so a key that
      // brought a layer in, and one held down as a modifier for the chord, do not count.
      const MODS = Object.values(MOD_GLYPH);
      const struck = p => {
        if (Object.values(s.activatorOf).includes(p.idx)) return false;
        if (!s.held.has(p.idx)) return true;
        const b = resolve(p.idx, stack());
        return !(b && ((b.key.hold && MODS.some(g => b.key.hold.includes(g))) || MODS.includes(b.key.tap)));
      };
      const prior = recentPos.slice(0, start).reverse().find(struck);
      const tooSoon = s.data.combo_idle > 0 && prior && recentPos[start].t - prior.t < s.data.combo_idle;
      if (pressedSet.length > 1 && !tooSoon) {
        // ZMK matches a combo against the highest active layer alone, as the layers stood when the
        // chord's first key went down (combo.c): with a layer up that declares no combo on these
        // keys they are typed one by one, whatever the layers below it declare. The first key's
        // stack leaves out a layer that key itself brought up, which came after it.
        const samePositions = c => c.positions.length === pressedSet.length && c.positions.every(p => pressedSet.includes(p));
        const comboLayer = recentPos[start].entry.stack[0];
        const combo = s.data.combos.find(c => c.layers.includes(comboLayer) && samePositions(c)) || null;
        if (combo) {
          // A third key within the term makes a bigger combo: take the smaller one's pill down,
          // and its count with it -- one chord, one combo.
          if (s.comboEntry) s.comboEntry.cancelled = true;
          s.comboEntry = ledgerAdd({ kind: "combo", layer: comboLayer, key: comboKey(combo.positions), t: now,
                                     due: now + term, eligible: !sent });
          for (const p of recentPos.slice(start)) if (p.entry) p.entry.chord = true;   // one keystroke (strokes)
          entry.combo = combo.key;      // what this keystroke is, for the strip (stripEvent)
          paint.push({ op: "combo", positions: combo.positions, key: combo.key });
        }
      }
    }

    // The key at ZMK position `pos` went up: a tap or a hold, now that it is up; and any key still
    // down that went down before it had a key go down and up inside it (strokes).
    function release(pos, now) {
      const idx = idxAt(pos);
      if (!onBoard(idx)) return;
      s.held.delete(idx);
      for (const name of Object.keys(s.activatorOf)) if (s.activatorOf[name] === idx) s.activatorOf[name] = null;
      const press = s.down.get(idx);
      if (press) {
        s.down.delete(idx);
        press.upAt = now;
        for (const d of s.down.values()) if (d.t < press.t) d.nested = true;
      }
      paint.push({ op: "up", idx, t: now, stroke: !!press });
    }

    /* Which key turned a modifier on, when positions say which keys went down: one still held that
     * carries it (a home-row mod, a sticky shift still down), else the latest such key pressed within
     * activator_ms (a sticky shift tapped and let go). null: none known yet -- the position may still
     * be on its way, and press takes it then. */
    function modSourceOf(glyph, now) {
      const layers = stack();
      const carrying = p => { const b = resolve(p.idx, layers); return b && carries(b.key, glyph); };
      const latest = [...s.recentPos].reverse();
      const src = latest.find(p => s.held.has(p.idx) && carrying(p))
        || latest.find(p => now - p.t < T("activator_ms") && carrying(p));
      return src ? src.idx : null;
    }

    function setMods(flags, now) {
      const positions = s.posAt && now - s.posAt < T("positions_fresh_ms");
      for (const f of Object.keys(MOD_GLYPH)) {
        if (!flags[f]) delete s.modSource[f];
        else if (!s.mods[f] || s.modSource[f] === undefined) {
          if (positions) s.modSource[f] = modSourceOf(MOD_GLYPH[f], now);
          else delete s.modSource[f];
        }
      }
      s.mods = flags;
      paint.push({ op: "keys" });
    }

    // The keymap message. Re-sent by the host when a sync rewrites it: the live layer set is kept,
    // what was read on the keymap going away is not.
    function load(data) {
      if (!data.layout || !data.layers) return;
      s.data = data;
      s.momentary = []; s.oneShot = null;
      s.activatorOf = {}; s.drawnSince = {}; s.held.clear();
      s.modSource = {}; s.lastPress = null; s.comboEntry = null;
      // Keystrokes on the keymap going away are not followed by the next one's keys.
      s.down.clear();
      s.baseLayers = [data.base];
      // A session is kept by ZMK position: each drawer key's, inverted from the message's map (a
      // message with none is a keymap in the firmware's own order, where they are the same).
      const map = data.positions || {};
      s.posOf = [];
      for (const [pos, idx] of Object.entries(map)) s.posOf[idx] = Number(pos);
      if (!Object.keys(map).length) data.layout.keys.forEach((k, idx) => { s.posOf[idx] = idx; });
    }

    // ---------- the answer ----------

    function board() {
      if (!s.data) return { ready: false, layers: [], live: s.live, oneShot: null, momentary: [] };
      return { ready: true, layers: stack(), live: s.live, oneShot: s.oneShot, momentary: s.momentary.map(m => m.layer) };
    }
    function answer() {
      const out = { board: board(), said, label: null, next: nextAt(), _paint: paint };
      said = []; paint = [];
      return out;
    }

    function read(m, now) {
      if (typeof now !== "number" || !isFinite(now)) throw new TypeError("keystrokes: read needs the time it is");
      runDue(now);
      if (m && typeof m === "object") {
        if (m.kind === "keymap") load(m);
        else if (m.kind === "touch") s.touch = Array.isArray(m.ids) ? m.ids.slice() : null;
        else if (m.kind === "press" && s.data) press(m.pos, m.sent === true, now);
        else if (m.kind === "release" && s.data) release(m.pos, now);
        else if (m.kind === "key" && s.data) {
          if (m.type === "flagsChanged" && m.flags && typeof m.flags === "object" && !Array.isArray(m.flags)) setMods(m.flags, now);
        }
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
