/* zmk-layer-hud — the keystrokes: what the keyboard's messages say was struck, without a DOM.
 *
 * The feed's messages go in, one at a time, each with the time it arrived; what they mean comes
 * out. A keystroke is one strike the board draws and counts once (GLOSSARY.md): a key, a combo, a
 * tapped hold-tap. Which layers are up, which keys and combos were struck, what a report alone
 * typed and where, how long each stays lit -- the reading of the messages lives here, and hud.js
 * draws and counts what it says.
 *
 *   const ks = ZmkKeystrokes.create();
 *   ks.read(message, now)    a feed message: keymap, layers (ids; null: no longer reported), press
 *                            and release (pos, and `sent` for a press sent in), key (a report:
 *                            keyDown, flagsChanged; `combos` for typing sent in), secure (on: a
 *                            secret is being typed, and nothing typed before it is kept), touch
 *                            (the touch keyboard's layer ids, top first; null: off). Other kinds
 *                            are ignored.
 *   ks.tick(now)             nothing new: only what has come due by now
 *       -> { board, said, label, next }
 *          board  the board as read:
 *                 { ready, secure, layers (top first, the base last), live ({ids, at} or null),
 *                   oneShot, momentary (inferred layers, the latest last), activators (keys marked
 *                   as what brought a layer up), mods (the modifier flags), modSource (flag -> the
 *                   key that turned it on, null: none known; a flag left out: no positions to say),
 *                   held (keys down), lit (keys lit), chord (the lit keys lit as a combo's),
 *                   pills ([{id, positions, key, until}]: combo pills up) }
 *          said   what happened since the last call, in order, never taken back:
 *                 { say: "layers", ids }  the keyboard's layers applied (late, if held back)
 *                 { say: "down", idx, pos, own, t }  a key went down by position: a position is
 *                                         never taken back, so it may glow at once
 *                 { say: "press", idx, pos, layer, own, t }   a key struck, settled: count it on
 *                                         `layer`, at ZMK position `pos`
 *                 { say: "combo", layer, key, own, t }        a combo struck, settled ("p,p": its
 *                                         ZMK positions)
 *                 { say: "guess", keys: [{idx, pos, layer}], layer, combo, own, t }  keys a report
 *                                         lit with no position to say so, settled (`combo`: the
 *                                         chord they make, or null)
 *                 { say: "keystroke", as: "tap", idx, pos, layer, own, t }  a key that typed, in
 *                                         the order keys went down; as: "chord" for a combo
 *                 { say: "hold", idx, pos, own, t }  a key that was held: no keystroke
 *                 `own`: the keyboard's own typing, not typing sent in
 *          label  for a key message: the board's name for a key the feed cannot name, or null
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
    // What has been read.
    const s = {
      data: null,
      live: null,               // {ids, at} once the keyboard has reported its layers
      baseLayers: [],           // emulated: just the base layer
      momentary: [],            // [{layer, until}]  (inference only)
      oneShot: null,            // layer name        (inference only)
      touch: null,              // the touch keyboard's layer ids, top first, while it is up
      touchFollows: false,      // ...and they are the keyboard's own: nothing on glass holds a layer
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
      recent: [],               // the last few tokens typed, with what they lit, for macros (remember)
      capitals: false,          // the board draws its letters as capitals (load)
      ledgerDue: 0,             // when what waits to be settled is next due (armLedger)
      strokes: [],              // settled keystrokes not yet known to be taps or holds (strokes)
      secure: false,            // a secret is being typed: nothing of it is read
      lights: new Map(),        // key idx -> {until: when it goes out (null: while it is down), combo}
      pills: new Map(),         // pill id -> {id, positions, key, until}
      comboPill: null,          // the pill of the chord being struck
    };
    let label = null;
    let pillSeq = 0;
    let said = [];

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
    const idxAt = pos => View.idxAt(s.data, pos);
    const comboKey = idxs => idxs.map(zmkPos).sort((a, b) => a - b).join(",");
    // A key counts where its binding came from on the layers that were up when it went down: a
    // transparent key on a held layer typed the layer underneath's legend, and counts there.
    const bindingLayer = (idx, layers) => { const r = View.resolve(s.data, idx, layers); return r ? r.layer : layers[0]; };

    // ---------- settling ----------

    /* What the board drew, said once. A keystroke goes into the ledger first and is settled only
     * when nothing can take it back any more: a press once no combo can claim it, a combo once no
     * longer one can supersede it, a key lit from a report alone once its position can no longer
     * arrive and no macro absorb it. Said any sooner, a report that beats its own position counts
     * its keystroke twice, a guess the position corrects counts the wrong key, and a macro's steps
     * count as the keys they spell. */
    function ledgerAdd(entry) {
      s.ledger.push(entry);
      armLedger();
      return entry;
    }

    function armLedger() {
      if (!s.ledger.length) return;
      const at = Math.min(...s.ledger.map(e => e.due));
      if (due.has("ledger") && s.ledgerDue <= at) return;
      s.ledgerDue = at;
      after("ledger", at, when => { settle(when, false); armLedger(); });
    }

    // Say what is due (everything, with `all`: a keymap about to be replaced).
    function settle(now, all) {
      const keep = [];
      for (const e of s.ledger) {
        if (!all && e.due > now) { keep.push(e); continue; }
        if (e.cancelled) continue;
        said.push(settled(e));
        // A keystroke of its own, for the time it took and whose finger struck it: a press that
        // was not one key of a chord, and a chord.
        if ((e.kind === "press" && !e.chord) || e.kind === "combo") s.strokes.push(e);
      }
      s.ledger = keep;
      if (!all) resolveStrokes(now);
    }

    function settled(e) {
      if (e.kind === "press") return { say: "press", idx: e.idx, pos: e.pos, layer: bindingLayer(e.idx, e.stack), own: e.eligible, t: e.t };
      if (e.kind === "combo") return { say: "combo", layer: e.layer, key: e.key, own: e.eligible, t: e.t };
      // a guess: the keys a report lit, with no position to say so
      const keys = e.idxs.map(idx => ({ idx, pos: zmkPos(idx), layer: bindingLayer(idx, e.stack) }));
      const combo = e.idxs.length > 1 && keys.every(k => k.pos !== null) ? comboKey(e.idxs) : null;
      return { say: "guess", keys, layer: e.layer, combo, own: e.eligible, t: e.t };
    }

    // ---------- strokes ----------

    /* Which keystrokes typed, in the order they went down, for the time each key takes and
     * same-finger bigrams (hud.js). A chord is one keystroke, struck by no one finger. A key that
     * can be held (a layer key, a home-row mod: anything with a hold) is a keystroke only when it
     * was tapped; held, it typed nothing, and the key struck under it follows the keystroke before
     * it. It was held when it stayed down past HOLD_MS, when another key went down and came up
     * again inside it (how ZMK's balanced hold-tap tells a hold), or when a layer came up with it
     * and another key went down on that layer. Only its release says which, so a keystroke waits
     * in `strokes` until the ones before it are known, and is said then. */
    const HOLD_MS = 400;

    function strokeKind(e, now) {        // "tap", "hold", "chord", or null while it cannot be told
      if (e.kind === "combo") return "chord";
      if (!e.holder) return "tap";
      if (e.activator && e.overlapped) return "hold";
      const up = e.upAt === undefined ? null : e.upAt;
      if ((up === null ? now : up) - e.t > HOLD_MS) return "hold";
      if (up === null) return null;
      return e.nested ? "hold" : "tap";
    }

    function resolveStrokes(now) {
      while (s.strokes.length) {
        const e = s.strokes[0], kind = strokeKind(e, now);
        if (kind === null) break;
        s.strokes.shift();
        if (kind === "hold") said.push({ say: "hold", idx: e.idx, pos: e.pos, own: e.eligible, t: e.t });
        else if (kind === "chord") said.push({ say: "keystroke", as: "chord", own: e.eligible, t: e.t });
        else said.push({ say: "keystroke", as: "tap", idx: e.idx, pos: e.pos, layer: bindingLayer(e.idx, e.stack), own: e.eligible, t: e.t });
      }
    }

    // A secret is being typed: nothing typed before it is kept either. A key that slipped in as the
    // secret began would say what it was: what waits to be settled, what waits to be known as a tap
    // or a hold, the keys down and the last few typed go.
    function secure(on) {
      s.secure = on;
      if (!on) return;
      s.held.clear(); s.recent.length = 0; s.recentPos.length = 0;
      s.ledger = []; s.ledgerDue = 0; cancel("ledger");
      s.comboEntry = null; s.down.clear(); s.strokes = []; s.lastPress = null;
      // What is on screen of it goes too: the keys lit, and every pill.
      s.lights.clear(); s.pills.clear(); s.comboPill = null;
      cancelAll("light:"); cancelAll("pill:"); cancelAll("held:");
    }

    const onBoard = idx => idx >= 0 && idx < s.data.layout.keys.length;
    const extras = () => (s.data && s.data.extras) || {};
    // The keyboard's own typing, as opposed to typing sent in (which says whether combos count) or
    // a rehearsal's (synthetic).
    const fromKeyboard = ev => ev.sent !== true && typeof ev.combos !== "boolean" && !ev.synthetic;
    const isLetter = t => /^[\p{L}]$/u.test(t);

    // Named keys -> the legend text keymap-drawer keymaps usually use for them.
    const NAMED = {
      space: "␣", return: "↵", escape: "⎋", delete: "⌫", forwarddelete: "⌦", tab: "⇥",
      left: "←", right: "→", up: "↑", down: "↓", home: "⇱", end: "⇲",
      pagedown: "⇟", pageup: "⇞", insert: "⎀", capslock: "⇪",
      // Keyboard Cancel (K_CANCEL), what a cancel combo sends: without positions this is how its
      // keys light and its pill shows.
      cancel: "cancel",
    };
    // The function keys the decoder names (f1..f24): without them a keymap's F5 resolves to no
    // token at all and the key never lights when the firmware reports no positions.
    for (let i = 1; i <= 24; i++) NAMED["f" + i] = "F" + i;
    // Alternative spellings a drawer file may use for the same named key.
    const ALIASES = { "␣": ["space", "spc", "SPACE"], "↵": ["⏎", "enter", "ret", "RET"], "⎋": ["esc", "ESC"],
      "⌫": ["bspc", "BSPC", "backspace"], "⌦": ["del", "DEL"], "⇥": ["tab", "TAB", "↹"] };

    // ---------- what is due ----------

    // Things that happen later, each at its time: the deadline name -> {at, seq, fn}. A name is
    // armed again by replacing it; `once` gives a name of its own to each.
    const due = new Map();
    let seq = 0;
    function after(name, at, fn) { due.set(name, { at, seq: ++seq, fn }); }
    function once(at, fn) { after("once:" + (seq + 1), at, fn); }
    function cancel(name) { due.delete(name); }
    function cancelAll(prefix) { for (const name of [...due.keys()]) if (name.startsWith(prefix)) due.delete(name); }
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

    // ---------- what is lit ----------

    // A key down is lit until it comes up, and release_ms after; a key a report lit, press_ms; a
    // combo's pill, combo_pill_ms. A key lit as a combo's stays so while it is lit.
    function lightUntil(idx, until) {
      const l = s.lights.get(idx) || { until: null, combo: false };
      l.until = until;
      s.lights.set(idx, l);
      if (until === null) cancel("light:" + idx);
      else after("light:" + idx, until, () => s.lights.delete(idx));
    }
    function unlightKey(idx) { s.lights.delete(idx); cancel("light:" + idx); }
    // A key held down stays lit until it comes up, whatever else lights it meanwhile, and its own
    // time is what catches a lost release: a flash and a guess taken back leave it alone.
    function flash(idxs, cls, now) {
      for (const idx of idxs) {
        if (!onBoard(idx)) continue;
        const l = s.lights.get(idx) || { until: null, combo: false };
        if (cls === "combo") l.combo = true;
        s.lights.set(idx, l);
        if (!s.held.has(idx)) lightUntil(idx, now + T("press_ms"));
      }
    }
    function showPill(positions, key, now) {
      const id = ++pillSeq, until = now + T("combo_pill_ms");
      s.pills.set(id, { id, positions: positions.slice(), key, until });
      after("pill:" + id, until, () => s.pills.delete(id));
      return id;
    }
    function takeDown(id) { s.pills.delete(id); cancel("pill:" + id); }

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
      // The touch keyboard showing the keyboard's own layers takes the new ones (touch.js adopts
      // them while nothing on glass holds one): a key going down now resolves on them.
      if (s.touch && s.touchFollows) s.touch = ids.slice().sort((x, y) => y - x);
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
      // A report can beat its own positions: they travel on separate channels. What the character
      // drew for this keystroke was a guess the keyboard is replacing now -- take it down, pill
      // and all, or the board shows the guess and the truth side by side (two pills for one
      // chord). A guess older than the combo term was an earlier keystroke's, and fades by itself.
      for (const r of s.recent) if (now - r.t <= term) unlight(r);
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
      if (s.live) {
        for (const name of liveStack()) {
          if (name === base() || s.activatorOf[name] != null) continue;
          if (now - (s.drawnSince[name] || 0) > term) continue;
          if (activatorsOf(name).includes(idx)) {
            s.activatorOf[name] = idx;
            entry.activator = true;
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
      resolveStrokes(now);    // a key waiting to be told a tap from a hold may have been down long enough
      // Lit until the release arrives, and let go if it never does.
      lightUntil(idx, null);
      said.push({ say: "down", idx, pos: Number(pos), own: !sent, t: now });
      after("held:" + idx, now + T("held_timeout_ms"), when => release(pos, when));
      // Older presses stay in the list for the activator lookup (setLayers); the combo group is
      // the trailing run of presses that started within the term of this one.
      const keepMs = Math.max(T("activator_ms"), s.data.combo_idle || 0);   // the idle rule below looks back this far
      while (s.recentPos.length && now - s.recentPos[0].t > keepMs) s.recentPos.shift();
      s.recentPos.push({ idx, t: now, entry });
      // A modifier reported before the key that turned it on: this is that key, if it carries it.
      for (const f of Object.keys(s.modSource)) {
        if (s.modSource[f] === null && r && carries(r.key, MOD_GLYPH[f])) s.modSource[f] = idx;
      }

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
      if (start === recentPos.length - 1) { s.comboEntry = null; s.comboPill = null; } // a new group begins
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
          // These keys are down, and it is their release that puts them out.
          for (const p of combo.positions) { const l = s.lights.get(p) || { until: null, combo: false }; l.combo = true; s.lights.set(p, l); }
          if (s.comboPill !== null) takeDown(s.comboPill);
          s.comboPill = showPill(combo.positions, combo.key, now);
        }
      }
    }

    // The key at ZMK position `pos` went up: a tap or a hold, now that it is up; and any key still
    // down that went down before it had a key go down and up inside it (strokes).
    function release(pos, now) {
      const idx = idxAt(pos);
      if (!onBoard(idx)) return;
      s.held.delete(idx);
      cancel("held:" + idx);
      // Out release_ms from now.
      if (s.lights.has(idx)) lightUntil(idx, now + T("release_ms"));
      for (const name of Object.keys(s.activatorOf)) if (s.activatorOf[name] === idx) s.activatorOf[name] = null;
      const press = s.down.get(idx);
      if (press) {
        s.down.delete(idx);
        press.upAt = now;
        for (const d of s.down.values()) if (d.t < press.t) d.nested = true;
        resolveStrokes(now);
      }
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
    }

    // ---------- the reports ----------


    // A legend matches a typed token exactly, through the alias table, or as one side of an
    // "a|b" legend (a key that produces either). A multi-key sequence (a macro) matches when its
    // printable core equals the legend's: motion keys the macro types (End, arrows) and the
    // glyphs the legend uses to hint at them (⇥ → ⇲ …) are ignored on both sides.
    const MOTION = /[⇥⇤→←↑↓⇲⇱⇢⇠⏎↵␣⌫⌦\s]/g;
    const core = t => t.replace(MOTION, "");
    // `exact`: the legend itself, not one alternative of an adaptive key's "a|b" (findOnLayer).
    function legendMatches(legend, token, exact) {
      if (!legend) return false;
      if (legend === token) return true;
      if (!exact && legend.includes("|") && legend.split("|").some(part => part.trim() === token)) return true;
      const alts = ALIASES[token];
      if (alts && alts.includes(legend)) return true;
      if (token.length > 1) { const c = core(token); return c.length > 1 && c === core(legend); }
      return false;
    }

    // Letter-producing combos on the base layer are commands (e.g. vim motions) rather than typing
    // when `extras.letter_combos_on` is set: outside those layers they are ignored, so a typed
    // letter missing from the base is attributed to the secondary alpha layer instead.
    // `noCombos`: typing whose sender says combos are not how it is typed (keyDown).
    // Returns {hit, combo}: the keys that type it, and the combo they are when they are one -- the
    // combo found here, so its pill names what is lit -- or null.
    function findOnLayer(layer, token, commandLayersActive, noCombos) {
      const keys = s.data.layers[layer];
      if (!keys) return null;
      const gate = extras().letter_combos_on;
      // A legend that says exactly this beats one that only sometimes does. "h|v" is an adaptive
      // key -- h, or v right after a vowel -- while a combo drawn "v" always types v, so from a v
      // alone the combo is the likelier and the adaptive key the fallback.
      for (const exact of [true, false]) {
        for (let i = 0; i < keys.length; i++) if (keys[i].type !== "trans" && legendMatches(keys[i].tap, token, exact)) return { hit: [i], combo: null };
        if (noCombos) continue;
        for (const c of s.data.combos) {
          if (!c.layers.includes(layer) || !legendMatches(c.key.tap, token, exact)) continue;
          if (gate && gate.length && isLetter(token) && layer === base() && !commandLayersActive) continue;
          return { hit: c.positions.slice(), combo: c };
        }
      }
      return null;
    }

    function tokenFor(ev) {
      if (ev.name && NAMED[ev.name]) return NAMED[ev.name];
      if (ev.chars && ev.chars.length === 1) {
        // On a board drawn in capitals, h is the H key (load).
        const upper = ev.chars.toUpperCase();
        return s.capitals && upper.length === 1 && upper !== ev.chars && isLetter(ev.chars) ? upper : ev.chars;
      }
      return null;
    }

    function armMomentary(layer, now) {
      const m = s.momentary.find(x => x.layer === layer);
      if (m) m.until = now + T("momentary_ms"); else s.momentary.push({ layer, until: now + T("momentary_ms") });
      once(now + T("momentary_ms") + 20, expire);
    }

    function expire(now) {
      s.momentary = s.momentary.filter(m => m.until > now);
    }

    // Resolve a typed token on the given stack (top first). Returns {hit, combo, layer, viaShift} or null.
    // An uppercase letter may live on a shifted layer the config names among the sticky ones.
    function resolveOnStack(token, layers, commandLayersActive, noCombos) {
      const shiftLayers = (extras().sticky || []).filter(l => /shift/i.test(l) && !layers.includes(l));
      // A capital the stack itself draws came from there -- Caps word up, K is its chord. Only a
      // capital the stack has no key for came from a Shift the layers do not show, and a shift
      // layer that is not up is where the drawer keeps it. Searched the other way round, a live
      // Caps word's K was drawn as Shift · Alpha 2's key and that layer's activator.
      const searchStack = /^\p{Lu}$/u.test(token) ? [...layers, ...shiftLayers] : layers;
      for (const layer of searchStack) {
        const found = findOnLayer(layer, token, commandLayersActive, noCombos);
        if (found) return { hit: found.hit, combo: found.combo, layer, viaShift: shiftLayers.includes(layer) };
      }
      return null;
    }

    // A key lit from a report alone, entered against the record remember() keeps for the macro and
    // press lookups: unlight() cancels the guess, and so does any position going down.
    function guess(rec, idxs, layer, ev, now) {
      if (!idxs.length) return;
      rec.guess = ledgerAdd({ kind: "guess", idxs: idxs.slice(), layer, t: now,
                              stack: [layer].concat(stack().filter(l => l !== layer)),
                              due: now + Math.max(comboTerm(), T("sequence_ms")), eligible: fromKeyboard(ev) });
    }

    // Recent keyDown tokens with the keys they lit, for multi-key legends ("->", "=>", "&&", "()").
    function remember(token, lit, pill, layer, now) {
      const recent = s.recent;
      while (recent.length && now - recent[0].t > T("sequence_ms")) recent.shift();
      const rec = { token, t: now, lit: lit || [], pill: pill || null, layer: layer || null, guess: null };
      recent.push(rec);
      if (recent.length > T("sequence_max")) recent.shift();
      return rec;
    }
    // `inferring`: placing a key the keyboard's own stack does not speak for (keyDown).
    function matchSequence(token, layers, cmdActive, noCombos, inferring, ev, now) {
      const fresh = s.recent.filter(r => now - r.t <= T("sequence_ms"));
      for (let n = Math.min(fresh.length, T("sequence_max") - 1); n >= 1; n--) {
        const parts = fresh.slice(fresh.length - n);
        const seq = parts.map(p => p.token).join("") + token;
        // A macro's first character can use up the one-shot layer the macro lives on (Qu on
        // Shift · Alpha 2, ão on the Ç extension), so by its last character that layer is off the
        // stack: look where its first characters were found, too. Without layers from the keyboard
        // that may never have been the macro's own -- Qu's Q reads as Alpha 2's q with Shift -- so
        // the sticky layers a macro can live on are looked at as well, and for typing sent in, which
        // never came from the keyboard's layers at all. Otherwise the keyboard reports a one-shot
        // layer for as long as it is up, and the live stack says all there is to say.
        const where = [...new Set(parts.map(p => p.layer).concat(inferring ? (extras().sticky || []) : [])
          .filter(l => l && !layers.includes(l)))];
        const r = resolveOnStack(seq, where.concat(layers), cmdActive, noCombos);
        if (!r) continue;
        // The keys lit so far were the macro's steps (or a shorter legend that matched first, like
        // "()" inside "();"): unlight them, pill included, and light the longer match.
        for (const p of parts) unlight(p);
        flash(r.hit, r.hit.length > 1 ? "combo" : null, now);
        let pill = null;
        if (r.combo) pill = showPill(r.hit, r.combo.key, now);
        // Keep the tokens: a still longer legend may follow (";" after "()", "⏎" after "do {").
        guess(remember(token, r.hit, pill, r.layer, now), r.hit, r.layer, ev, now);
        afterKey(now);
        return true;
      }
      return false;
    }
    function unlight(entry) {
      for (const idx of entry.lit) if (!s.held.has(idx)) unlightKey(idx);
      if (entry.pill !== null) takeDown(entry.pill);
      if (entry.guess) entry.guess.cancelled = true;   // taken back before it was counted
      entry.lit = []; entry.pill = null;
    }

    function commandLayersActive(layers) {
      const gate = extras().letter_combos_on;
      return !gate || !gate.length || gate.some(l => layers.includes(l));
    }

    function touchLayer(layer, now) {
      const m = s.momentary.find(x => x.layer === layer);
      if (m) { m.until = now + T("momentary_ms"); return; }
      // a key that resolved only in the base drops any inferred momentary layer
      if (baseStack().includes(layer) && s.momentary.length) s.momentary = [];
    }

    // A one-shot layer is consumed by the key, but stays on the banner long enough to be seen.
    function afterKey(now) {
      if (!s.oneShot) return;
      after("oneShot", now + T("one_shot_ms"), () => { s.oneShot = null; });
    }

    // A report: a key went down (keyDown), placed on the board when no position says where.
    function keyDown(ev, now) {
      s.lastKeyAt = now;
      // With key positions coming from the firmware, the board is lit from them; the character
      // only feeds the strip. Physical character reports are redundant while firmware position
      // reports are fresh. The rehearsal feed marks its uinput events synthetic: those have no
      // matching firmware positions, so they must still resolve and light their key after a
      // synthetic thumb flash.
      if (!ev.synthetic && s.posAt && now - s.posAt < T("positions_fresh_ms")) return;
      const token = tokenFor(ev);
      if (!token) return;

      const layers = stack();
      // Typing sent in -- `zmk-layer-hud poke`, a WebSocket client -- carries `combos`: whether
      // combos are part of how it is typed (the feed makes it false for a sender that does not
      // say). It did not come from the keyboard's own layers, so it is placed the way inference
      // places a key, live or not: on the stack, else on the layer that has it -- z as Alpha 2's key
      // rather than the r+a chord, unless the sender says combos. A character only a combo types is
      // still that combo: the sender said how it types, not that the keymap has another way.
      const sent = typeof ev.combos === "boolean";
      const noCombos = sent && !ev.combos;
      // `letter_combos_on` is an inference hint, for a key the keyboard's live stack cannot explain.
      // A live stack is the keyboard's own word on which combos can fire -- ZMK matches a combo
      // against the highest active layer, and the import records each combo's real coverage -- so
      // on it a letter combo is typing, whatever the hint says. Gating it there hid every
      // base-layer letter combo (k w v q x z j y) whenever positions were not fresh. Typing sent in
      // says for itself whether combos count.
      const cmdActive = s.live || sent ? true : commandLayersActive(layers);

      // Macros type several keys back to back (-> is "-" then ">"): when the last few tokens
      // together spell a legend on the live stack, that key or combo is what was pressed.
      if (matchSequence(token, layers, cmdActive, noCombos, !s.live || sent, ev, now)) return;

      // Live: the keyboard told us the stack; the key must be on it (combos included). A chord
      // (⌘c, ⌃⇧a) first tries the legend spelled with its modifier glyphs.
      if (s.live && !sent) {
        const f = ev.flags || {};
        const modsGlyph = ["cmd", "ctrl", "alt", "shift"].filter(m => f[m] && (m !== "shift" || f.cmd || f.ctrl || f.alt)).map(m => MOD_GLYPH[m]).join("");
        const r = (modsGlyph && resolveOnStack(modsGlyph + token, layers, cmdActive)) || resolveOnStack(token, layers, cmdActive);
        if (r) {
          const extra = r.viaShift ? activatorsOf(r.layer) : [];
          flash(r.hit.concat(extra), r.hit.length > 1 ? "combo" : null, now);
          // The pill is remembered with the keys, so whatever supersedes this guess -- a macro's
          // longer legend, or the positions arriving after it -- takes the pill down too.
          let pill = null;
          if (r.combo) pill = showPill(r.hit, r.combo.key, now);
          guess(remember(token, r.hit.concat(extra), pill, r.layer, now), r.hit, r.layer, ev, now);
          touchLayer(r.layer, now);
          afterKey(now);
          return;
        }
        // Not on the real stack: a chord whose legend is a label or an icon (shortcut layers), or
        // a legend the drawer spells differently. The keyboard is the only source, so guessing
        // another layer would be wrong: remember the token for a possible macro and light nothing.
        remember(token, [], null, null, now);
        return;
      }

      const ex = extras();
      const sticky = new Set(ex.sticky || []);
      // 0. Typing goes through two alpha layers when the config names a secondary one: a letter
      //    that is not a plain base-layer key comes from the sticky alpha2 layer (one shot).
      if (ex.alpha2 && isLetter(token) && !cmdActive && !s.oneShot) {
        const lower = token.toLowerCase();
        const onBase = s.data.layers[base()].some(k => k.type !== "trans" && legendMatches(k.tap, lower));
        const direct = s.data.layers[ex.alpha2].findIndex(k => k.type !== "trans" && legendMatches(k.tap, lower));
        if (!onBase && direct >= 0) {
          s.oneShot = ex.alpha2;
          const shiftLayer = (ex.sticky || []).find(l => /shift/i.test(l));
          const extra = activatorsOf(ex.alpha2).concat(/^\p{Lu}$/u.test(token) && shiftLayer ? activatorsOf(shiftLayer) : []);
          flash([direct].concat(extra), null, now);
          guess(remember(token, [direct].concat(extra), null, ex.alpha2, now), [direct], ex.alpha2, ev, now);
          afterKey(now);
          return;
        }
      }
      const search = ex.search || s.data.layer_order.filter(l => l !== base());
      const order = isLetter(token)
        ? search
        : search.filter(l => !sticky.has(l)).concat(search.filter(l => sticky.has(l)));
      const place = noCombos => {
        // 1. the active stack, top first
        const r = resolveOnStack(token, layers, cmdActive, noCombos);
        if (r) {
          const extra = r.viaShift ? activatorsOf(r.layer) : [];
          flash(r.hit.concat(extra), r.hit.length > 1 ? "combo" : null, now);
          let pill = null;
          if (r.combo) pill = showPill(r.hit, r.combo.key, now);
          guess(remember(token, r.hit.concat(extra), pill, r.layer, now), r.hit, r.layer, ev, now);
          touchLayer(r.layer, now);
          afterKey(now);
          return true;
        }
        // 2. any other layer → a momentary or one-shot activation the host could not see. Letters
        //    most likely came from a sticky layer; anything else from a held one.
        for (const layer of order) {
          if (layers.includes(layer)) continue;
          const found = findOnLayer(layer, token, cmdActive, noCombos);
          if (found) {
            const hit = found.hit;
            if (sticky.has(layer)) s.oneShot = layer; else armMomentary(layer, now);
            flash(hit.concat(activatorsOf(layer)), hit.length > 1 ? "combo" : null, now);
            let pill = null;
            if (found.combo) pill = showPill(hit, found.combo.key, now);
            guess(remember(token, hit.concat(activatorsOf(layer)), pill, layer, now), hit, layer, ev, now);
            afterKey(now);
            return true;
          }
        }
        return false;
      };
      // Without combos first; a combo only when nothing else on the keymap types this.
      if (place(noCombos) || (noCombos && place(false))) return;
      afterKey(now);
    }

    /* A usage the feed has no name for -- usage9c, a key bound to something past F24 -- is a name no
     * one reads. The strip says instead what the board says of the key that sent it: the last key
     * down, from the firmware's positions, while that press is this keystroke's. When that press
     * completed a combo, the keystroke is the combo's, and the strip says what its pill says, not
     * whichever of its keys went down last. (Cancel, the Diamond's l+o+u, used to be the case in
     * point; the feed names its key now, so it reads "cancel" with or without positions.) */
    const STRIP_PRESS_MS = 1000;
    function labelOf(ev, now) {
      if (ev.type !== "keyDown" || !/^usage[0-9a-f]+$/.test(ev.name || "")) return null;
      const p = s.lastPress;
      if (!p || now - p.t > STRIP_PRESS_MS) return null;
      const r = p.combo ? null : resolve(p.idx, p.stack);
      const key = p.combo || (r && r.key);
      return (key && (key.tap || key.glyph)) || null;
    }

    // The keymap message. Re-sent by the host when a sync rewrites it: the live layer set is kept,
    // what was read on the keymap going away is not.
    function load(data, now) {
      if (!data.layout || !data.layers) return;
      // What is still waiting to be settled was drawn with the keymap going away: said with that
      // one, whose layers and keys it names.
      if (s.data) settle(now, true);
      s.strokes = [];
      s.data = data;
      s.momentary = []; s.oneShot = null;
      s.activatorOf = {}; s.drawnSince = {}; s.held.clear();
      s.modSource = {}; s.lastPress = null; s.comboEntry = null;
      // The board is drawn afresh: nothing on it is lit, and no pill is up.
      s.lights.clear(); s.pills.clear(); s.comboPill = null;
      cancelAll("light:"); cancelAll("pill:");
      // Keystrokes on the keymap going away are not followed by the next one's keys.
      s.down.clear();
      s.baseLayers = [data.base];
      // A board that draws its letters as capitals, as `keymap parse` does: there the legend is the
      // keycap, not the character, so a lowercase letter placed from what was typed is its
      // capital's key (tokenFor) -- the rule host/play.py spells text by.
      const letters = (data.layers[data.base] || []).map(k => k.tap).filter(t => typeof t === "string" && isLetter(t));
      s.capitals = letters.length > 0 && letters.every(t => t !== t.toLowerCase());
      s.posOf = View.posOf(data);       // a session is kept by ZMK position
    }

    // ---------- the answer ----------

    // The keys marked as what brought each layer up: an inferred layer's every activator; a layer the
    // keyboard reports, the key that brought it in when positions say which, else every key that can.
    function activators() {
      const out = new Set();
      for (const m of s.momentary) for (const a of activatorsOf(m.layer)) out.add(a);
      if (s.oneShot) for (const a of activatorsOf(s.oneShot)) out.add(a);
      if (s.live) {
        for (const name of liveStack()) {
          if (name === base()) continue;
          if (s.activatorOf[name] !== undefined) { if (s.activatorOf[name] !== null) out.add(s.activatorOf[name]); }
          else for (const a of activatorsOf(name)) out.add(a);
        }
      }
      return [...out];
    }

    function board() {
      const lit = [...s.lights.keys()];
      return {
        ready: !!s.data, secure: s.secure,
        layers: s.data ? stack() : [],
        live: s.live ? { ids: s.live.ids.slice(), at: s.live.at } : null,
        oneShot: s.oneShot, momentary: s.momentary.map(m => m.layer),
        activators: s.data ? activators() : [],
        mods: Object.assign({}, s.mods), modSource: Object.assign({}, s.modSource),
        held: [...s.held], lit, chord: lit.filter(idx => s.lights.get(idx).combo),
        pills: [...s.pills.values()].map(p => ({ id: p.id, positions: p.positions.slice(), key: p.key, until: p.until })),
      };
    }
    function answer() {
      const out = { board: board(), said, label, next: nextAt() };
      said = []; label = null;
      return out;
    }

    function read(m, now) {
      if (typeof now !== "number" || !isFinite(now)) throw new TypeError("keystrokes: read needs the time it is");
      runDue(now);
      if (m && typeof m === "object") {
        if (m.kind === "keymap") load(m, now);
        else if (m.kind === "secure") secure(!!m.on);
        else if (m.kind === "touch") {
          s.touch = Array.isArray(m.ids) ? m.ids.slice() : null;
          const own = s.data && (s.live ? liveStack() : [base()]);
          s.touchFollows = !!(s.touch && own && View.drawnStack(s.data, s.touch).join("\n") === own.join("\n"));
        }
        else if (m.kind === "press" && s.data && !s.secure) press(m.pos, m.sent === true, now);
        else if (m.kind === "release" && s.data) release(m.pos, now);
        else if (m.kind === "key" && s.data) {
          if (m.type === "flagsChanged") {
            if (m.flags && typeof m.flags === "object" && !Array.isArray(m.flags)) setMods(m.flags, now);
          } else if (m.type === "keyDown" && !s.secure) {
            if (!m.repeat) keyDown(m, now);
            label = labelOf(m, now);
          }
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
      // For the page's tests that look past the board, until they are moved onto it: the keys down
      // by press, the keystrokes waiting to be told a tap from a hold, the last press, and how many
      // keystrokes wait to be settled.
      inside: () => ({ down: new Map(s.down), strokes: s.strokes.slice(), lastPress: s.lastPress, unsettled: s.ledger.length }),
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
