/* zmk-layer-hud — the touch keyboard's virtual firmware: ZMK's keymap and behaviours, run on the
 * actions host/actions.py compiles (docs/touch.md), as a pure state machine.
 *
 * A key going down or up is a position event; it goes where ZMK sends it -- past an undecided
 * hold-tap, which captures it, past a tap dance, to the keymap, which resolves the binding on the
 * highest active layer -- and what the behaviours raise are keycode events, which pass the sticky
 * keys, caps word and key repeat before they reach the HID report. The order of those listeners,
 * the hold-tap decision tables, how a captured event is released, how a sticky key lets go: each is
 * a port of the ZMK source it names (app/src/behaviors/behavior_hold_tap.c, behavior_sticky_key.c,
 * keymap.c, hid.c, hid_listener.c, ...), held to vectors taken from ZMK's own tests
 * (hud/tests/fixtures/zmk_vectors.json).
 *
 * Glass is not a switch, so with `touch` set a few things change (docs/touch.md): a hold-tap waits
 * touch.hold_ms and decides balanced, a hold let go with nothing else touched latches -- one-shot,
 * then locked on a second tap -- a one-shot waits for the next key however long it takes, and a held
 * key repeats where the host does not repeat it.
 *
 * Nothing here schedules itself: time is an argument, `nextDeadline()` says when `tick()` has work,
 * and ZmkEngine.drive() arms one timer for a page. No DOM, no Date, no console: the same file runs in
 * node's tests, the panels' web views, a tablet's browser, Android's WebView and iOS's JavaScriptCore.
 */
(function (root) {
  "use strict";

  const KEY_PAGE = 7, CONSUMER_PAGE = 12;
  const COMBO_BASE = 100000;                 // a chip's virtual position, past any real one
  const MAX_CAPTURED = 40;
  const BUBBLE = 0, CAPTURED = 1;
  const UNDECIDED = "undecided", TAP = "tap", HOLD_INTERRUPT = "hold-interrupt", HOLD_TIMER = "hold-timer";
  const isMod = (page, id) => page === KEY_PAGE && id >= 0xE0 && id <= 0xE7;
  const isHold = (status) => status === HOLD_INTERRUPT || status === HOLD_TIMER;

  function create(actions, options) {
    const opts = options || {};
    const touch = opts.touch || null;
    const sink = Object.assign({ repeat: "os", pages: [KEY_PAGE, CONSUMER_PAGE], mouse: "none" }, opts.sink || {});
    const trace = !!opts.trace;
    const table = actions.table || [];
    const defaultLayer = actions.default || 0;
    const layerIds = Object.keys(actions.layers || {}).map(Number).sort((a, b) => a - b);
    const combos = actions.combos || [];
    const conditional = actions.conditional || [];

    // ---------- time, timers, output ----------
    let now = 0, timerSeq = 0, out = [];
    let timers = [];
    function at(t, fn) {
      const timer = { at: t, fn, seq: timerSeq++, live: true };
      timers.push(timer);
      return timer;
    }
    function cancel(timer) { if (timer) timer.live = false; }
    function runDue(t, inclusive) {
      for (;;) {
        timers = timers.filter((x) => x.live);
        let next = null;
        for (const x of timers) {
          if ((inclusive ? x.at <= t : x.at < t) && (!next || x.at < next.at || (x.at === next.at && x.seq < next.seq))) next = x;
        }
        if (!next) return;
        next.live = false;
        now = Math.max(now, next.at);
        next.fn(next.at);
      }
    }
    function emit(ev) { out.push(Object.assign({ t: now }, ev)); }
    function flush() { const o = out; out = []; return o; }

    // ---------- layers (keymap.c, conditional_layer.c) ----------
    const active = new Set([defaultLayer]), locks = new Set();
    let lastLayers = "";
    function layerActive(id, state) { return id === defaultLayer || (state || active).has(id); }
    function setLayer(id, on, locking) {
      if (!layerIds.includes(id)) return;
      if (id === defaultLayer && !on) return;
      if (!locking && !on && locks.has(id)) return;
      const before = [active.has(id), locks.has(id)];
      if (on) active.add(id); else active.delete(id);
      if (locking) { if (on) locks.add(id); else locks.delete(id); }
      if (before[0] !== active.has(id) || before[1] !== locks.has(id)) layersChanged();
    }
    const activate = (id, locking) => setLayer(id, true, locking);
    const deactivate = (id, locking) => setLayer(id, false, locking);
    function toggle(id, locking) {
      if (layerActive(id) && (!locking || locks.has(id))) deactivate(id, locking);
      else activate(id, locking);
    }
    function toLayer(id, locking) {
      for (let i = layerIds.length - 1; i >= 0; i--) deactivate(layerIds[i], locking);
      activate(id, locking);
    }
    let conditionalBusy = false, conditionalAgain = false;
    const lockedByConditional = new Set();
    function layersChanged() {
      if (conditional.length) {
        conditionalAgain = true;
        if (!conditionalBusy) {
          conditionalBusy = true;
          while (conditionalAgain) {
            conditionalAgain = false;
            const want = new Set(), thens = new Set();
            for (const c of conditional) {
              thens.add(c.then);
              if (c.if.every((l) => layerActive(l))) want.add(c.then);
              if (c.if.every((l) => locks.has(l) || l === defaultLayer)) lockedByConditional.add(c.then);
            }
            for (const l of [...thens].sort((a, b) => a - b)) {
              const locking = lockedByConditional.has(l);
              if (want.has(l)) {
                if (!layerActive(l) || (locking && !locks.has(l))) activate(l, locking);
              } else {
                if (layerActive(l) && (!locks.has(l) || locking)) deactivate(l, locking);
                lockedByConditional.delete(l);
              }
            }
          }
          conditionalBusy = false;
        }
      }
      const ids = [...active].sort((a, b) => b - a);
      const key = ids.join(",") + "|" + [...locks].sort((a, b) => b - a).join(",");
      if (key !== lastLayers) {
        lastLayers = key;
        emit({ kind: "layers", ids, locks: [...locks].sort((a, b) => b - a) });
      }
    }
    function highestActive() {
      for (let i = layerIds.length - 1; i >= 0; i--) if (layerActive(layerIds[i])) return layerIds[i];
      return defaultLayer;
    }

    // ---------- the HID report (hid.c, hid_listener.c) ----------
    const explicitCounts = [0, 0, 0, 0, 0, 0, 0, 0];
    let implicit = 0, masked = 0, keys = [], consumer = [], buttons = 0, seq = 0, lastReport = "";
    function explicitMods() { let m = 0; for (let i = 0; i < 8; i++) if (explicitCounts[i] > 0) m |= 1 << i; return m; }
    function reportMods() { return (explicitMods() & ~masked) | implicit; }
    function pressed(page, id) {
      if (isMod(page, id)) return explicitCounts[id - 0xE0] > 0;
      return (page === CONSUMER_PAGE ? consumer : keys).includes(id);
    }
    function hidPress(page, id) {
      if (isMod(page, id)) { explicitCounts[id - 0xE0]++; return; }
      const list = page === CONSUMER_PAGE ? consumer : keys;
      if (!list.includes(id)) list.push(id);
    }
    function hidRelease(page, id) {
      if (isMod(page, id)) { if (explicitCounts[id - 0xE0] > 0) explicitCounts[id - 0xE0]--; return; }
      if (page === CONSUMER_PAGE) consumer = consumer.filter((x) => x !== id);
      else keys = keys.filter((x) => x !== id);
    }
    function registerMods(mods, on) {
      for (let i = 0; i < 8; i++) {
        if (!(mods & (1 << i))) continue;
        if (on) explicitCounts[i]++;
        else if (explicitCounts[i] > 0) explicitCounts[i]--;
      }
    }
    function report() {
      const r = { mods: reportMods(), keys: keys.slice(), consumer: consumer.slice(), mouse: { buttons } };
      const key = JSON.stringify(r);
      if (key === lastReport) return;
      lastReport = key;
      emit(Object.assign({ kind: "report", seq: ++seq }, r));
    }
    function hidListener(ev) {
      if (trace) emit({ kind: "keycode", state: ev.state, page: ev.page, id: ev.id, implicit: ev.implicit, explicit: ev.explicit });
      if (!sink.pages.includes(ev.page)) {
        if (ev.state) emit({ kind: "unsupported", why: `usage page ${ev.page}` });
        return BUBBLE;
      }
      if (ev.state) {
        if (!isMod(ev.page, ev.id) && pressed(ev.page, ev.id)) { hidRelease(ev.page, ev.id); report(); }
        hidPress(ev.page, ev.id);
        registerMods(ev.explicit, true);
        implicit = ev.implicit;
        report();
        if (touch && sink.repeat === "synthetic" && ev.page === KEY_PAGE && !isMod(ev.page, ev.id)) startRepeat(ev.id);
      } else {
        hidRelease(ev.page, ev.id);
        registerMods(ev.explicit, false);
        implicit = 0;
        report();
        // The key let go stops repeating now, not when its next repeat finds it gone.
        if (repeating === ev.id) { cancel(repeatTimer); repeatTimer = null; repeating = null; }
      }
      return BUBBLE;
    }

    // Synthetic repeat, for a sink whose host does not repeat a held key itself.
    let repeatTimer = null, repeating = null;
    function startRepeat(id) {
      cancel(repeatTimer);
      repeating = id;
      const again = (t, first) => {
        repeatTimer = at(t + (first ? touch.repeat_delay_ms : touch.repeat_ms), (when) => {
          if (keys[keys.length - 1] !== id) return;
          keys = keys.filter((x) => x !== id); report();
          keys.push(id); report();
          again(when, false);
        });
      };
      again(now, true);
    }

    // ---------- keycode events: hold-tap, sticky keys, caps word, key repeat, HID ----------
    function keycodeEvent(key, state, t) {
      const mods = key.mods || 0;
      const page = key.page || KEY_PAGE;
      return isMod(page, key.id)
        ? { page, id: key.id, implicit: 0, explicit: mods, state, t }
        : { page, id: key.id, implicit: mods, explicit: 0, state, t };
    }
    const keycodeListeners = [holdTapKeycode, stickyKeycode, capsWordKeycode, keyRepeatKeycode, latchKeycode, hidListener];
    function raiseKeycode(ev, from) {
      for (let i = from || 0; i < keycodeListeners.length; i++) {
        if (keycodeListeners[i](ev) === CAPTURED) return;
      }
    }
    const keycodeIndex = (fn) => keycodeListeners.indexOf(fn);

    // ---------- position events: hold-tap, tap dance, keymap ----------
    const positionListeners = [holdTapPosition, tapDancePosition, latchPosition, keymapPosition];
    function raisePosition(ev, from) {
      for (let i = from || 0; i < positionListeners.length; i++) {
        if (positionListeners[i](ev) === CAPTURED) return;
      }
    }

    // ---------- the keymap (keymap.c) ----------
    const atPress = new Map();   // position -> the action its press resolved to, for its release
    const snapshots = new Map(); // position -> the layers active when it was last pressed
    function resolve(pos, snapshot) {
      for (let i = layerIds.length - 1; i >= 0; i--) {
        const id = layerIds[i];
        if (!layerActive(id, snapshot)) continue;
        const a = bindingAt(id, pos);
        if (!a || a.op === "trans") continue;
        return a;
      }
      return null;
    }
    function bindingAt(id, pos) {
      if (pos >= COMBO_BASE) {
        const c = combos[pos - COMBO_BASE];
        return c ? table[c.action] : null;
      }
      const slots = actions.layers[String(id)];
      return slots && pos < slots.length ? table[slots[pos]] : null;
    }
    function keymapPosition(ev) {
      if (ev.state) {
        let action = null;
        if (ev.pos >= COMBO_BASE) {
          const c = combos[ev.pos - COMBO_BASE];
          if (c && (!c.layers || c.layers.includes(highestActive()))) action = table[c.action];
        } else {
          snapshots.set(ev.pos, new Set(active));
          action = resolve(ev.pos, snapshots.get(ev.pos));
        }
        if (!action) return BUBBLE;
        atPress.set(ev.pos, action);
        invoke(action, ev, true);
      } else {
        // A release goes to what the press resolved to. One with no press before it (the
        // firmware's own tests have some) resolves on the layers of that key's last press.
        const action = atPress.has(ev.pos) ? atPress.get(ev.pos)
          : ev.pos < COMBO_BASE ? resolve(ev.pos, snapshots.get(ev.pos) || new Set([defaultLayer])) : null;
        if (!action) return BUBBLE;
        atPress.delete(ev.pos);
        invoke(action, ev, false);
      }
      return BUBBLE;
    }

    // ---------- behaviours ----------
    const deviceState = new WeakMap();
    function device(action, make) {
      if (!deviceState.has(action)) deviceState.set(action, make());
      return deviceState.get(action);
    }
    function invoke(action, ev, down) {
      if (!action) return;
      switch (action.op) {
        case "kp":
          raiseKeycode(keycodeEvent(action.key, down, ev.t));
          return;
        case "kt":
          if (!down) return;
          if (action.mode === "on" || (action.mode !== "off" && !pressed(action.key.page, action.key.id))) {
            raiseKeycode(keycodeEvent(action.key, true, ev.t));
          } else {
            raiseKeycode(keycodeEvent(action.key, false, ev.t));
          }
          return;
        case "mo":
          if (down) activate(action.layer, action.locking); else deactivate(action.layer, action.locking);
          return;
        case "to":
          if (down) toLayer(action.layer, action.locking);
          return;
        case "tog":
          if (!down) return;
          if (action.mode === "on") activate(action.layer, action.locking);
          else if (action.mode === "off") deactivate(action.layer, action.locking);
          else toggle(action.layer, action.locking);
          return;
        case "trans": case "none":
          return;
        case "ht":
          if (down) holdTapPressed(action, ev); else holdTapReleased(action, ev);
          return;
        case "sk": case "sl":
          if (down) stickyPressed(action, ev); else stickyReleased(action, ev);
          return;
        case "morph": {
          const d = device(action, () => ({ pressed: null }));
          if (down) {
            if (d.pressed) return;
            if (explicitMods() & action.mods) {
              masked = action.mods & ~(action.keep || 0);
              d.pressed = action.morphed;
            } else {
              d.pressed = action.plain;
            }
            invoke(d.pressed, ev, true);
          } else {
            if (!d.pressed) return;
            const p = d.pressed;
            d.pressed = null;
            invoke(p, ev, false);
            masked = 0;
            report();
          }
          return;
        }
        case "td":
          if (down) tapDancePressed(action, ev); else tapDanceReleased(action, ev);
          return;
        case "macro":
          queueMacro(action, ev, down);
          return;
        case "repeat": {
          const d = repeatDevice(action);
          if (down) {
            if (!d.last) return;
            d.current = Object.assign({}, d.last, { state: true, t: ev.t });
            raiseKeycode(d.current);
          } else {
            if (!d.current) return;
            d.current = Object.assign({}, d.current, { state: false, t: ev.t });
            raiseKeycode(d.current);
          }
          return;
        }
        case "caps_word":
          if (down) { const d = capsDevice(action); d.active = !d.active; }
          return;
        case "mouse":
          if (action.button != null) {
            if (sink.mouse === "none") { if (down) emit({ kind: "unsupported", input: ev.pos, raw: action.raw, why: "mouse" }); return; }
            buttons = down ? (buttons | action.button) : (buttons & ~action.button);
            report();
          } else if (down) {
            emit({ kind: "unsupported", input: ev.pos, raw: action.raw, why: "mouse move" });
          }
          return;
        case "system":
          if (down) emit({ kind: "unsupported", input: ev.pos, raw: action.raw, why: action.kind });
          return;
        default:
          if (down) emit({ kind: "unsupported", input: ev.pos, raw: action.raw, why: action.why || action.op });
      }
    }

    // ---------- hold-tap (behavior_hold_tap.c) ----------
    let activeHoldTaps = [], undecided = null;
    const captured = new Array(MAX_CAPTURED).fill(null);
    const lastTapped = { pos: null, t: -1e15 };
    function htConfig(action) {
      return {
        term: touch ? touch.hold_ms : action.term,
        flavor: touch && touch.flavor !== "firmware" ? "balanced" : action.flavor,
        quickTap: action.quick_tap == null ? -1 : action.quick_tap,
        priorIdle: action.prior_idle == null ? -1 : action.prior_idle,
        retro: !!action.retro_tap && !(touch && touch.latch),
        triggers: action.trigger_positions || [],
        triggerOnRelease: !!action.trigger_on_release,
        whileUndecided: !!action.hold_while_undecided,
        linger: !!action.hold_while_undecided_linger,
      };
    }
    function isQuickTap(ht) {
      if (lastTapped.t + ht.cfg.priorIdle > ht.t) return true;
      return lastTapped.pos === ht.pos && lastTapped.t + ht.cfg.quickTap > ht.t;
    }
    function decide(ht, moment) {
      if (ht.status !== UNDECIDED || ht !== undecided) return;
      if (ht.cfg.whileUndecided && moment === "key-down") { pressHold(ht); return; }
      const f = ht.cfg.flavor;
      const pick = {
        "hold-preferred": { "key-up": TAP, "other-key-down": HOLD_INTERRUPT, timer: HOLD_TIMER, "quick-tap": TAP },
        "balanced": { "key-up": TAP, "other-key-up": HOLD_INTERRUPT, timer: HOLD_TIMER, "quick-tap": TAP },
        "tap-preferred": { "key-up": TAP, timer: HOLD_TIMER, "quick-tap": TAP },
        "tap-unless-interrupted": { "key-up": TAP, "other-key-down": HOLD_INTERRUPT, timer: TAP, "quick-tap": TAP },
      }[f] || {};
      if (!pick[moment]) return;
      ht.status = pick[moment];
      // Positional hold-tap: a hold only when the first other key pressed is a trigger key.
      if (ht.cfg.triggers.length && ht.firstOther !== -1 && !ht.cfg.triggers.includes(ht.firstOther)) ht.status = TAP;
      undecided = null;
      pressBinding(ht);
      releaseCaptured();
    }
    function pressHold(ht) { invoke(ht.action.hold, { pos: ht.pos, t: ht.t }, true); }
    function releaseHold(ht) { invoke(ht.action.hold, { pos: ht.pos, t: ht.t }, false); }
    function pressTap(ht) { lastTapped.pos = ht.pos; lastTapped.t = ht.t; invoke(ht.action.tap, { pos: ht.pos, t: ht.t }, true); }
    function releaseTap(ht) { invoke(ht.action.tap, { pos: ht.pos, t: ht.t }, false); }
    function pressBinding(ht) {
      if (ht.cfg.retro && ht.status === HOLD_TIMER) return;
      if (isHold(ht.status)) {
        if (!ht.cfg.whileUndecided) pressHold(ht);
      } else {
        if (ht.cfg.whileUndecided && !ht.cfg.linger) releaseHold(ht);
        pressTap(ht);
      }
    }
    function releaseBinding(ht) {
      if (ht.cfg.retro && ht.status === HOLD_TIMER) return;
      if (isHold(ht.status)) releaseHold(ht); else releaseTap(ht);
    }
    function holdTapPressed(action, ev) {
      if (undecided) return;
      const ht = { pos: ev.pos, t: ev.t, action, cfg: htConfig(action), status: UNDECIDED, firstOther: -1, timer: null };
      activeHoldTaps.push(ht);
      undecided = ht;
      if (isQuickTap(ht)) decide(ht, "quick-tap");
      decide(ht, "key-down");
      ht.timer = at(ht.t + ht.cfg.term, () => decide(ht, "timer"));
    }
    function holdTapReleased(action, ev) {
      const ht = activeHoldTaps.find((h) => h.pos === ev.pos);
      if (!ht) return;
      cancel(ht.timer);
      if (ev.t > ht.t + ht.cfg.term) decide(ht, "timer");
      decide(ht, "key-up");
      if (ht.cfg.retro && ht.status === HOLD_TIMER) {
        releaseBinding(ht);
        ht.status = TAP;
        pressBinding(ht);
      }
      releaseBinding(ht);
      if (ht.cfg.whileUndecided && ht.cfg.linger) releaseHold(ht);
      activeHoldTaps = activeHoldTaps.filter((h) => h !== ht);
    }
    function retroTapInterrupt(ignore) {
      for (const ht of activeHoldTaps) {
        if (ht.pos === ignore || !ht.cfg.retro) continue;
        if (ht.status === HOLD_TIMER) { ht.status = HOLD_INTERRUPT; pressBinding(ht); }
      }
    }
    function capture(item) {
      for (let i = 0; i < MAX_CAPTURED; i++) if (!captured[i]) { captured[i] = item; return; }
    }
    function capturedKeydown(pos) {
      for (let i = 0; i < MAX_CAPTURED; i++) {
        const c = captured[i];
        if (!c) return false;
        if (c.kind === "pos" && c.ev.pos === pos && c.ev.state) return true;
      }
      return false;
    }
    function releaseCaptured() {
      if (undecided) return;
      for (let i = 0; i < MAX_CAPTURED; i++) {
        const c = captured[i];
        if (!c) return;
        captured[i] = null;
        if (c.kind === "pos") raisePosition(c.ev, 0);
        else raiseKeycode(c.ev, keycodeIndex(holdTapKeycode));
      }
    }
    function holdTapPosition(ev) {
      retroTapInterrupt(ev.pos);
      if (!undecided) return BUBBLE;
      if (undecided.cfg.triggerOnRelease !== ev.state && undecided.firstOther === -1) undecided.firstOther = ev.pos;
      if (undecided.pos === ev.pos) return BUBBLE;
      if (ev.t > undecided.t + undecided.cfg.term) decide(undecided, "timer");
      if (!undecided) return BUBBLE;
      if (!ev.state && !capturedKeydown(ev.pos)) return BUBBLE;
      capture({ kind: "pos", ev });
      decide(undecided, ev.state ? "other-key-down" : "other-key-up");
      return CAPTURED;
    }
    function holdTapKeycode(ev) {
      if (ev.state && !isMod(ev.page, ev.id) && ev.t > lastTapped.t) { lastTapped.pos = null; lastTapped.t = ev.t; }
      if (!undecided || !isMod(ev.page, ev.id)) return BUBBLE;
      if (undecided.cfg.whileUndecided && undecided.status === UNDECIDED) return BUBBLE;
      capture({ kind: "kc", ev });
      return CAPTURED;
    }

    // ---------- sticky keys and layers (behavior_sticky_key.c) ----------
    let stickies = [];
    function stickyInner(action) {
      return action.op === "sk" ? { op: "kp", key: action.key } : { op: "mo", layer: action.layer, locking: false };
    }
    function releaseAfter(action) {
      if (touch) return touch.sticky_ms > 0 ? touch.sticky_ms : null;
      return action.release_after;
    }
    function stickyPress(s, t) { invoke(s.inner, { pos: s.pos, t }, true); }
    function stickyRelease(s, t) {
      stickies = stickies.filter((x) => x !== s);
      cancel(s.timer);
      invoke(s.inner, { pos: s.pos, t }, false);
    }
    function stickyTimeout(s) {
      if (s.action.lazy) stickies = stickies.filter((x) => x !== s);
      else stickyRelease(s, s.releaseAt);
    }
    function stickyPressed(action, ev) {
      const same = stickies.find((s) => s.pos === ev.pos && s.action === action);
      if (touch && same && same.locked) { same.unlock = true; return; }
      if (touch && touch.latch && same && same.timerStarted && !same.used && ev.t - same.releasedAt <= touch.lock_ms) {
        same.locked = true; same.lockPress = true; cancel(same.timer);
        return;
      }
      if (same) { cancel(same.timer); stickyRelease(same, ev.t); }
      const s = { pos: ev.pos, action, inner: stickyInner(action), timerStarted: false, releaseAt: 0, used: null,
                  timer: null, locked: false, releasedAt: 0 };
      stickies.push(s);
      if (!action.lazy) stickyPress(s, ev.t);
    }
    function stickyReleased(action, ev) {
      const s = stickies.find((x) => x.pos === ev.pos && x.action === action);
      if (!s) return;
      if (s.lockPress) { s.lockPress = false; return; }
      if (s.unlock) { stickyRelease(s, ev.t); return; }
      if (s.locked) return;
      if (s.used) { stickyRelease(s, ev.t); return; }
      s.timerStarted = true;
      s.releasedAt = ev.t;
      const after = releaseAfter(action);
      if (after != null) {
        s.releaseAt = ev.t + after;
        s.timer = at(s.releaseAt, () => stickyTimeout(s));
      }
    }
    function stickyKeycode(ev) {
      const before = [], after = [];
      for (const s of stickies.slice()) {
        if (s.locked) continue;
        if (s.inner.op === "kp" && s.inner.key.id === ev.id && (s.inner.key.page || KEY_PAGE) === ev.page &&
            ((s.inner.key.mods || 0) === ev.implicit || isMod(ev.page, ev.id))) continue;
        if (ev.state) {
          if (s.action.ignore_mods && isMod(ev.page, ev.id)) continue;
          if (s.used) continue;
          cancel(s.timer);
          if (s.releaseAt && ev.t > s.releaseAt) { stickyTimeout(s); continue; }
          if (s.action.lazy) before.push(s);
          if (s.timerStarted && s.action.quick_release) after.push(s);
          s.used = { page: ev.page, id: ev.id };
        } else if (s.timerStarted && s.used && s.used.page === ev.page && s.used.id === ev.id) {
          cancel(s.timer);
          after.push(s);
        }
      }
      for (const s of before) stickyPress(s, ev.t);
      if (!after.length) return BUBBLE;
      raiseKeycode(ev, keycodeIndex(stickyKeycode) + 1);
      for (const s of after) stickyRelease(s, ev.t);
      return CAPTURED;
    }

    // ---------- caps word, key repeat (behavior_caps_word.c, behavior_key_repeat.c) ----------
    const capsDevices = new Map(), repeatDevices = new Map();
    function capsDevice(action) {
      const k = JSON.stringify([action.mods, action.continue]);
      if (!capsDevices.has(k)) capsDevices.set(k, { active: false, action });
      return capsDevices.get(k);
    }
    function repeatDevice(action) {
      const k = JSON.stringify(action.pages || [KEY_PAGE]);
      if (!repeatDevices.has(k)) repeatDevices.set(k, { last: null, current: null, pages: action.pages || [KEY_PAGE] });
      return repeatDevices.get(k);
    }
    function capsWordKeycode(ev) {
      if (!ev.state) return BUBBLE;
      for (const d of capsDevices.values()) {
        if (!d.active) continue;
        const alpha = ev.page === KEY_PAGE && ev.id >= 0x04 && ev.id <= 0x1D;
        const numeric = ev.page === KEY_PAGE && ev.id >= 0x1E && ev.id <= 0x27;
        if (alpha) ev.implicit |= d.action.mods;
        const listed = (d.action.continue || []).some((c) => (c.page || KEY_PAGE) === ev.page && c.id === ev.id &&
          ((c.mods || 0) & (ev.implicit | explicitMods())) === (c.mods || 0));
        if (!alpha && !numeric && !isMod(ev.page, ev.id) && !listed) d.active = false;
      }
      return BUBBLE;
    }
    // A behaviour's device exists from the start, as ZMK's do: key repeat remembers the last key
    // before it is ever pressed.
    (function devices(list) {
      for (const a of list) {
        if (!a || typeof a !== "object") continue;
        if (a.op === "repeat") repeatDevice(a);
        if (a.op === "caps_word") capsDevice(a);
        devices([a.hold, a.tap, a.plain, a.morphed].concat(a.taps || [], (a.steps || []).map((s) => s.tap || s.press || s.release)));
      }
    })(table.concat(combos.map((c) => table[c.action])));
    function keyRepeatKeycode(ev) {
      if (!ev.state) return BUBBLE;
      for (const d of repeatDevices.values()) {
        if (d.pages.includes(ev.page)) d.last = Object.assign({}, ev, { implicit: ev.implicit | explicitMods() });
      }
      return BUBBLE;
    }

    // ---------- tap dance (behavior_tap_dance.c) ----------
    let dances = [];
    function tapDancePressed(action, ev) {
      let d = dances.find((x) => x.pos === ev.pos);
      if (!d) { d = { pos: ev.pos, action, counter: 0, pressed: true, decided: false, timer: null, releaseAt: 0 }; dances.push(d); }
      d.pressed = true;
      cancel(d.timer);
      if (d.counter < action.taps.length) d.counter++;
      if (d.counter === action.taps.length) { danceDecide(d, ev.t); return; }
      d.releaseAt = ev.t + action.term;
      d.timer = at(d.releaseAt, () => { danceDecide(d, d.releaseAt); if (!d.pressed) danceRelease(d, d.releaseAt); });
    }
    function danceDecide(d, t) { d.decided = true; invoke(d.action.taps[d.counter - 1], { pos: d.pos, t }, true); }
    function danceRelease(d, t) { dances = dances.filter((x) => x !== d); invoke(d.action.taps[d.counter - 1], { pos: d.pos, t }, false); }
    function tapDanceReleased(action, ev) {
      const d = dances.find((x) => x.pos === ev.pos);
      if (!d) return;
      d.pressed = false;
      if (d.decided) danceRelease(d, ev.t);
    }
    function tapDancePosition(ev) {
      if (!ev.state) return BUBBLE;
      for (const d of dances.slice()) {
        if (d.pos === ev.pos || d.decided) continue;
        cancel(d.timer);
        danceDecide(d, ev.t);
        if (!d.pressed) danceRelease(d, ev.t);
      }
      return BUBBLE;
    }

    // ---------- macros (behavior_macro.c, behavior_queue.c) ----------
    const queue = [];
    let queueTimer = null;
    function queueMacro(action, ev, down) {
      const steps = action.steps || [];
      const pause = steps.findIndex((s) => s.pause);
      const part = down ? (pause < 0 ? steps : steps.slice(0, pause)) : (pause < 0 ? [] : steps.slice(pause + 1));
      for (const s of part) {
        if (s.tap) { queue.push({ action: s.tap, down: true, wait: s.ms || 0, pos: ev.pos }); queue.push({ action: s.tap, down: false, wait: s.wait || 0, pos: ev.pos }); }
        else if (s.press) queue.push({ action: s.press, down: true, wait: s.wait || 0, pos: ev.pos });
        else if (s.release) queue.push({ action: s.release, down: false, wait: s.wait || 0, pos: ev.pos });
      }
      if (!queueTimer) processQueue();
    }
    function processQueue() {
      queueTimer = null;
      while (queue.length) {
        const item = queue.shift();
        invoke(item.action, { pos: item.pos, t: now }, item.down);
        if (item.wait > 0) { queueTimer = at(now + item.wait, processQueue); return; }
      }
    }

    // ---------- the touch keyboard: latches, interruptions ----------
    // A hold let go with nothing else touched is not let go: one finger cannot chord, so the
    // modifier or layer it held stays for the next key (one-shot), or until it is tapped again
    // (locked, when the second tap comes within lock_ms).
    const inputs = new Map();    // position -> {interrupted, t}
    let latches = [];            // {pos, kind: "layer"|"mod", state: "oneshot"|"locked", t, release(t), used}
    function holder(pos) {
      const action = atPress.get(pos);
      if (!action) return null;
      const holdOf = (a) => {
        if (!a) return null;
        if (a.op === "mo") return "layer";
        if (a.op === "kp" && isMod(a.key.page || KEY_PAGE, a.key.id)) return "mod";
        return null;
      };
      if (action.op === "ht") {
        const ht = activeHoldTaps.find((h) => h.pos === pos);
        return ht && isHold(ht.status) ? holdOf(action.hold) : null;
      }
      return holdOf(action);
    }
    function latchPosition(ev) {
      if (!touch || !touch.latch) return BUBBLE;
      const latch = latches.find((l) => l.pos === ev.pos);
      if (ev.state) {
        if (latch) {
          if (latch.state === "oneshot" && !latch.used && ev.t - latch.t <= touch.lock_ms) {
            latch.state = "locked"; latch.swallow = true;
            emitLatches();
            return CAPTURED;
          }
          if (latch.state === "locked") { latch.swallow = true; latch.unlock = true; return CAPTURED; }
          endLatch(latch, ev.t);
        }
        return BUBBLE;
      }
      if (latch && latch.swallow) {
        latch.swallow = false;
        if (latch.unlock) endLatch(latch, ev.t);
        return CAPTURED;
      }
      const input = inputs.get(ev.pos);
      inputs.delete(ev.pos);
      const kind = holder(ev.pos);
      if (!kind || !input || input.interrupted) return BUBBLE;
      // Hold the release back: it is what ends the latch.
      latches.push({ pos: ev.pos, kind, state: "oneshot", t: ev.t, used: false, ev });
      emitLatches();
      if (touch.sticky_ms > 0) {
        const l = latches[latches.length - 1];
        l.timer = at(ev.t + touch.sticky_ms, (t) => { if (l.state === "oneshot") endLatch(l, t); });
      }
      return CAPTURED;
    }
    function endLatch(latch, t) {
      latches = latches.filter((l) => l !== latch);
      cancel(latch.timer);
      raisePosition(Object.assign({}, latch.ev, { t }), positionListeners.indexOf(latchPosition) + 1);
      emitLatches();
    }
    function latchKeycode(ev) {
      // A one-shot layer goes once the key typed on it is down; a one-shot modifier once that key is up.
      if (!latches.length) return BUBBLE;
      if (ev.state) {
        if (isMod(ev.page, ev.id)) return BUBBLE;
        const layersAfter = [];
        for (const l of latches) {
          if (l.state !== "oneshot" || l.used) continue;
          l.used = { page: ev.page, id: ev.id };
          if (l.kind === "layer") layersAfter.push(l);
        }
        if (!layersAfter.length) return BUBBLE;
        raiseKeycode(ev, keycodeIndex(latchKeycode) + 1);
        for (const l of layersAfter) endLatch(l, ev.t);
        return CAPTURED;
      }
      const done = latches.filter((l) => l.kind === "mod" && l.used && l.used.page === ev.page && l.used.id === ev.id);
      if (!done.length) return BUBBLE;
      raiseKeycode(ev, keycodeIndex(latchKeycode) + 1);
      for (const l of done) endLatch(l, ev.t);
      return CAPTURED;
    }
    let lastLatches = "";
    function emitLatches() {
      const list = latches.map((l) => ({ pos: l.pos, kind: l.kind, state: l.state }))
        .concat(stickies.filter((s) => s.timerStarted || s.locked).map((s) => ({
          pos: s.pos, kind: s.inner.op === "mo" ? "layer" : "mod", state: s.locked ? "locked" : "oneshot" })));
      const key = JSON.stringify(list);
      if (key === lastLatches) return;
      lastLatches = key;
      emit({ kind: "latches", latches: list });
    }

    // ---------- the outside ----------
    function inputPos(input) {
      if (input && input.combo != null) return COMBO_BASE + Number(input.combo);
      return Number(input && input.pos != null ? input.pos : input);
    }
    function step(t, fn) {
      // A timer due at the same moment as a key goes first: in ZMK the key waits in the kscan
      // queue behind work that is already due.
      runDue(t, true);
      now = Math.max(now, t);
      fn();
      emitLatches();
      return flush();
    }
    const down = new Set();
    const api = {
      down(input, t) {
        const pos = inputPos(input);
        return step(t, () => {
          if (down.has(pos)) return;
          down.add(pos);
          // Every finger already down is no longer alone: whatever it holds is a real hold. Marked
          // here, on the input itself, because a hold-tap may capture this press for a while.
          for (const v of inputs.values()) v.interrupted = true;
          inputs.set(pos, { interrupted: false, t });
          raisePosition({ pos, state: true, t });
        });
      },
      up(input, t) {
        const pos = inputPos(input);
        return step(t, () => {
          // Glass never lifts a finger it did not put down; the firmware's tests do, and the
          // firmware answers them.
          if (!down.has(pos) && touch) return;
          down.delete(pos);
          raisePosition({ pos, state: false, t });
        });
      },
      tick(t) {
        runDue(t, true);
        now = Math.max(now, t);
        emitLatches();
        return flush();
      },
      nextDeadline() {
        let next = null;
        for (const x of timers) if (x.live && (next === null || x.at < next)) next = x.at;
        return next;
      },
      idle() {
        return !down.size && !latches.length && !stickies.length && !undecided && !activeHoldTaps.length &&
          !queue.length && !dances.length;
      },
      adopt(ids, t) {
        // The physical keyboard's layers, taken while nothing on glass holds one: the touch
        // keyboard then types on the layer the board shows.
        return step(t, () => {
          if (!api.idle()) return;
          for (const id of [...active]) if (!locks.has(id) && id !== defaultLayer && !ids.includes(id)) active.delete(id);
          for (const id of ids) if (layerIds.includes(id)) active.add(id);
          layersChanged();
        });
      },
      releaseAll(t) {
        return step(t, () => {
          // An undecided hold-tap is dropped, not decided: letting go of everything is not a tap.
          if (undecided) {
            cancel(undecided.timer);
            activeHoldTaps = activeHoldTaps.filter((h) => h !== undecided);
            undecided = null;
            captured.fill(null);
          }
          for (const pos of [...down]) { down.delete(pos); inputs.delete(pos); raisePosition({ pos, state: false, t }); }
          runDue(t, true);
          for (const l of latches.slice()) endLatch(l, t);
          for (const s of stickies.slice()) stickyRelease(s, t);
          queue.length = 0;
          for (const id of [...active]) if (id !== defaultLayer && !locks.has(id)) active.delete(id);
          layersChanged();
          for (let i = 0; i < 8; i++) explicitCounts[i] = 0;
          implicit = 0; masked = 0; keys = []; consumer = []; buttons = 0;
          report();
          cancel(repeatTimer);
        });
      },
      state() {
        return {
          layers: [...active].sort((a, b) => b - a),
          locks: [...locks].sort((a, b) => b - a),
          top: highestActive(),
          mods: reportMods(),
          capsWord: [...capsDevices.values()].some((d) => d.active),
          down: [...down],
          latches: JSON.parse(lastLatches || "[]"),
          idle: api.idle(),
        };
      },
    };
    return api;
  }

  /* A page's driver: the engine's one timer, armed for its next deadline, and every event handed
   * to `emit`. Native hosts call tick() from their own run loop instead. */
  function drive(eng, host) {
    let timer = null;
    function arm() {
      if (timer !== null) host.clearTimeout(timer);
      timer = null;
      const next = eng.nextDeadline();
      if (next === null) return;
      timer = host.setTimeout(() => { timer = null; host.emit(eng.tick(host.now())); arm(); }, Math.max(0, next - host.now()));
    }
    const run = (events) => { host.emit(events); arm(); };
    return {
      down: (input) => run(eng.down(input, host.now())),
      up: (input) => run(eng.up(input, host.now())),
      adopt: (ids) => run(eng.adopt(ids, host.now())),
      releaseAll: () => run(eng.releaseAll(host.now())),
      state: () => eng.state(),
      stop() { if (timer !== null) host.clearTimeout(timer); timer = null; },
    };
  }

  const ZmkEngine = { create, drive, COMBO_BASE };
  if (typeof module === "object" && module.exports) module.exports = ZmkEngine;
  else root.ZmkEngine = ZmkEngine;
})(typeof globalThis !== "undefined" ? globalThis : this);
