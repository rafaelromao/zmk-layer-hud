/* zmk-layer-hud — what a key report types, in the page: hudfeed's SignalDecoder (host/hudfeed.py),
 * ported, and what a text-only keyboard does with it.
 *
 * The touch keyboard's engine sends what a keyboard sends: report snapshots, a modifier byte and the
 * usages held. A desktop host presses those keys and its operating system types them. Two things
 * need the characters instead: the typed-keys strip, which shows the feed's key events, and a
 * tablet's keyboard, which can only insert text (iOS) or prefers to (Android).
 *
 *   ZmkHidText.decoder({altgr, deadKeyMs, compose}).feed(report, t) / .flush(t)
 *       -> the feed's key events {kind:"key", type, name, chars, code, flags, repeat[, composed]},
 *          with `mods`, the modifier byte, beside them. A dead key waits deadKeyMs for its letter
 *          and is one accented character with it, as on the feed (US-International).
 *   ZmkHidText.textEvents(events, {shortcuts})
 *       -> what a text sink does: {kind:"text", text}, {kind:"key", key, mods} for the named keys a
 *          text field knows (backspace, delete, enter, tab, escape, arrows, home, end, page up and
 *          down, F-keys), or {kind:"chord", chars, name, usage, mods} for a ⌘ or Ctrl shortcut --
 *          left out with shortcuts:false, for a sink that cannot send one. Each keeps usage and mods.
 *
 * The tables are hudfeed's, written out by host/hidtables_gen.py (hud/hidtables.js). DOM-free, no
 * timers: the page, node's tests and a bare JavaScriptCore all run it.
 */
(function (root) {
  "use strict";
  const T = (typeof module === "object" && module.exports) ? require("./hidtables.js") : root.ZmkHidTables;
  const RALT = T.RALT;

  function flagsOf(mods) {
    const f = { cmd: false, ctrl: false, alt: false, shift: false, fn: false };
    for (const bit in T.MOD_BITS) if (mods & Number(bit)) f[T.MOD_BITS[bit]] = true;
    return f;
  }

  function keyMessage(usage, down, flags, altgr, mods) {
    const type = down ? "keyDown" : "keyUp";
    const pair = (table) => table[`${usage},${flags.shift ? 1 : 0}`];
    if (altgr && pair(T.ALTGR_CHARS) !== undefined) {
      const chars = pair(T.ALTGR_CHARS);
      return { kind: "key", type, name: chars, chars, code: usage, flags: Object.assign({}, flags, { alt: false }), repeat: false, mods };
    }
    let chars = "";
    if (flags.alt && !flags.cmd && !flags.ctrl && pair(T.ALT_CHARS) !== undefined) chars = pair(T.ALT_CHARS);
    else if (T.CHARS[usage]) chars = T.CHARS[usage][flags.shift ? 1 : 0];
    else if (T.CONTROL_CHARS[usage] !== undefined) chars = T.CONTROL_CHARS[usage];
    const name = T.NAMED[usage] || (chars && chars !== " " ? chars : "usage" + usage.toString(16).padStart(2, "0"));
    return { kind: "key", type, name, chars, code: usage, flags, repeat: false, mods };
  }

  function decoder(options) {
    const opts = options || {};
    const compose = opts.compose !== false;
    const altgrOn = !!opts.altgr;
    const deadKeyMs = opts.deadKeyMs == null ? T.DEAD_KEY_MS : opts.deadKeyMs;
    let mods = 0, held = [], pending = null;

    function flush(now) {
      if (pending && (now == null || now >= pending.deadline)) {
        const p = pending;
        pending = null;
        return [p.down].concat(p.up ? [p.up] : []);
      }
      return [];
    }
    function down(msg, now) {
      if (!compose) return [msg];
      const out = [];
      if (pending) {
        const p = pending;
        pending = null;
        if (now != null && now <= p.deadline && msg.chars.length === 1 && /\p{L}/u.test(msg.chars)) {
          const dead = p.down.chars;
          const composed = T.DEAD_KEY_SPECIAL[dead + msg.chars] || (msg.chars + T.DEAD_KEYS[dead]).normalize("NFC");
          if ([...composed].length === 1) {
            return [Object.assign({}, msg, { chars: composed, name: composed, composed: [p.down.code, msg.code] })];
          }
        }
        out.push(p.down);
        if (p.up) out.push(p.up);
      }
      if (T.DEAD_KEYS[msg.chars] !== undefined && now != null) {
        pending = { down: msg, up: null, deadline: now + deadKeyMs };
        return out;
      }
      out.push(msg);
      return out;
    }
    function up(msg) {
      if (pending && pending.up === null && pending.down.code === msg.code) { pending.up = msg; return []; }
      return [msg];
    }
    function feed(report, now) {
      const out = [];
      const keys = (report.keys || []).filter(Boolean);
      if (report.mods !== mods) {
        mods = report.mods;
        out.push({ kind: "key", type: "flagsChanged", name: "", chars: "", code: 0, flags: flagsOf(mods), repeat: false, mods });
      }
      const flags = flagsOf(mods);
      const altgr = altgrOn && !!(mods & RALT);
      for (const k of held) if (!keys.includes(k)) out.push(...up(keyMessage(k, false, flags, altgr, mods)));
      for (const k of keys) if (!held.includes(k)) out.push(...down(keyMessage(k, true, flags, altgr, mods), now));
      held = keys;
      return out;
    }
    return { feed, flush, pending: () => (pending ? pending.down.chars : null) };
  }

  // The named keys a text field knows, in a text sink's words; hudfeed's NAMED spells them.
  const TEXT_KEYS = { "return": "enter", "delete": "backspace", "forwarddelete": "delete", "tab": "tab",
    "escape": "escape", "left": "left", "right": "right", "up": "up", "down": "down", "home": "home", "end": "end",
    "pageup": "pageup", "pagedown": "pagedown", "insert": "insert" };
  for (let i = 1; i <= 24; i++) TEXT_KEYS["f" + i] = "f" + i;

  function textEvents(events, options) {
    const shortcuts = !options || options.shortcuts !== false;
    const out = [];
    for (const ev of events) {
      if (ev.kind !== "key" || ev.type !== "keyDown") continue;
      const base = { usage: ev.code, mods: ev.mods || 0 };
      if (ev.flags.cmd || ev.flags.ctrl) {
        if (shortcuts) out.push(Object.assign({ kind: "chord", chars: ev.chars, name: ev.name }, base));
        continue;
      }
      const named = TEXT_KEYS[T.NAMED[ev.code]];
      if (named) { out.push(Object.assign({ kind: "key", key: named }, base)); continue; }
      if (ev.chars) out.push(Object.assign({ kind: "text", text: ev.chars }, base));
    }
    return out;
  }

  const ZmkHidText = { decoder, textEvents, flagsOf };
  if (typeof module === "object" && module.exports) module.exports = ZmkHidText;
  else root.ZmkHidText = ZmkHidText;
})(typeof globalThis !== "undefined" ? globalThis : this);
