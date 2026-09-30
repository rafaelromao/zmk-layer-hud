/* zmk-layer-hud — the landing page's demo, without the page: how a visitor's typing becomes the
 * feed's key messages, and how a compiled demo is played. Nothing here touches the DOM, so node
 * requires the file as it ships (site/tests/demo_test.js); site.js wires it to the page.
 *
 * Both hand the HUD what a keyboard would, through hud.receive:
 *   Translator  browser key and input events -> {kind:"key", type, name, chars, flags, ...}, in the
 *               feed's own vocabulary (host/hudfeed.py key_message), so the page places them as it
 *               places typing sent in.
 *   Player      a demo compiled by host/play.py ({timeline: [[wait_ms, message], ...], duration_ms})
 *               -> the same messages at the same moments, on a clock the tests can drive.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.demo = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // ---------- the translator ----------

  // What the browser names a key -> [name, chars] as the feed sends it (hudfeed NAMED,
  // CONTROL_CHARS). Tab is left out on purpose: it is how focus leaves the field.
  const NAMED = {
    Enter: ["return", "\r"], Escape: ["escape", "\x1b"], Backspace: ["delete", "\x7f"],
    Delete: ["forwarddelete", ""], ArrowRight: ["right", ""], ArrowLeft: ["left", ""],
    ArrowDown: ["down", ""], ArrowUp: ["up", ""], Home: ["home", ""], End: ["end", ""],
    PageUp: ["pageup", ""], PageDown: ["pagedown", ""], CapsLock: ["capslock", ""],
    PrintScreen: ["printscreen", ""], ScrollLock: ["scrolllock", ""], Pause: ["pause", ""],
    Insert: ["insert", ""], ContextMenu: ["menu", ""],
  };
  for (let i = 1; i <= 24; i++) NAMED["F" + i] = ["f" + i, ""];
  // Characters that are named keys when they arrive as text: a soft keyboard's Enter has no keydown.
  const TEXT = { " ": ["space", " "], "\n": ["return", "\r"], "\t": ["tab", "\t"] };
  const MODIFIERS = new Set(["Shift", "Control", "Alt", "Meta", "AltGraph", "OS"]);
  // The named keys that also change the field, so the input event after them is not typing again.
  const EDITS = new Set(["Enter", "Backspace", "Delete"]);
  // Text that arrives without being typed key by key: it only moves the field on.
  const NOT_TYPED = /^(insertFromPaste|insertFromDrop|insertFromYank|insertReplacementText|historyUndo|historyRedo)/;

  /* The part of `b` that differs from `a`: what an input event inserted and what it removed. */
  function diff(a, b) {
    let p = 0;
    while (p < a.length && p < b.length && a[p] === b[p]) p++;
    let s = 0;
    while (s < a.length - p && s < b.length - p && a[a.length - 1 - s] === b[b.length - 1 - s]) s++;
    return { removed: a.slice(p, a.length - s), inserted: b.slice(p, b.length - s) };
  }

  /* Printable text is read from the field, not from keydown: an input event's value, diffed
   * against the last one, is the committed character whatever produced it -- a dead key and its
   * letter, an IME, a phone's keyboard, which each fire their events in their own order. The diff
   * runs on every input that is not composing and on compositionend; running it twice changes
   * nothing, so which of the two a browser fires last does not matter. Named keys and ⌘/⌃ chords
   * come from keydown, which is the only place they appear. */
  class Translator {
    constructor(send) {
      this.send = send;
      this.combos = false;          // typing sent in: are combos how it is typed (`poke --combos`)
      this.value = "";              // the field as the last diff left it
      this.flags = { cmd: false, ctrl: false, alt: false, shift: false };
      this.skip = false;            // the input this keydown causes only moves the field on
    }

    message(name, chars) {
      // `sent`: typing sent in, never the keyboard's own. `synthetic`: no firmware position stands
      // behind it, so it lights its key even just after a played demo's presses (hud.js handleKey).
      return { kind: "key", type: "keyDown", name, chars, flags: Object.assign({}, this.flags),
               repeat: false, combos: this.combos, sent: true, synthetic: true };
    }

    // AltGr arrives as Ctrl+Alt on Windows and Linux; it types characters, it is not a chord.
    readFlags(e) {
      const altGr = !!(e.getModifierState && e.getModifierState("AltGraph"));
      return { cmd: !!e.metaKey, ctrl: !!e.ctrlKey && !altGr, alt: !!e.altKey && !altGr, shift: !!e.shiftKey };
    }

    setFlags(flags) {
      const f = this.flags;
      if (f.cmd === flags.cmd && f.ctrl === flags.ctrl && f.alt === flags.alt && f.shift === flags.shift) return;
      this.flags = flags;
      this.send({ kind: "key", type: "flagsChanged", name: "", chars: "", flags: Object.assign({}, flags),
                  repeat: false, combos: this.combos, sent: true, synthetic: true });
    }

    keydown(e) {
      // Each keydown decides for the input it causes, if any: an arrow or an Escape causes none,
      // and must not leave the next character to be skipped.
      this.skip = false;
      this.setFlags(this.readFlags(e));
      if (MODIFIERS.has(e.key) || e.isComposing || e.keyCode === 229 || e.key === "Unidentified" ||
          e.key === "Dead" || e.key === "Process" || e.key === "Tab") return;
      // A held key's repeats never reach the feed (it reports what the keyboard sends), so neither
      // the repeat nor the text it inserts is typing.
      if (e.repeat) { this.skip = true; return; }
      const named = NAMED[e.key];
      if (named) { this.skip = EDITS.has(e.key); this.send(this.message(named[0], named[1])); return; }
      if ((this.flags.cmd || this.flags.ctrl) && e.key.length === 1) {
        this.skip = true;
        this.send(this.message(e.key, e.key));
      }
    }

    keyup(e) { this.setFlags(this.readFlags(e)); }

    input(e, value) { if (!e.isComposing) this.commit(value, e.inputType || ""); }

    compositionend(e, value) { this.commit(value, "insertCompositionText"); }

    commit(value, inputType) {
      const before = this.value;
      this.value = value;
      if (this.skip || NOT_TYPED.test(inputType)) { this.skip = false; return; }
      const { removed, inserted } = diff(before, value);
      if (!inserted && removed) { this.send(this.message("delete", "\x7f")); return; }   // a phone's backspace
      for (const ch of Array.from(inserted)) {
        const named = TEXT[ch];
        this.send(named ? this.message(named[0], named[1]) : this.message(ch, ch));
      }
    }

    // The field changed under it (cleared, trimmed): take it as it is, typing nothing.
    reset(value) { this.value = value || ""; this.skip = false; }

    blur() { this.setFlags({ cmd: false, ctrl: false, alt: false, shift: false }); }
  }

  // ---------- the player ----------

  // A timer this late means the page stalled (a busy frame, a laptop waking). Sending what was
  // due at once would press keys that were meant to be apart inside one combo term and draw pills
  // the script never had, so the rest of the demo moves on by the stall instead.
  const LATE_MS = 60;
  const clockOfWindow = () => ({
    now: () => Date.now(),
    setTimeout: (fn, ms) => setTimeout(fn, ms),
    clearTimeout: id => clearTimeout(id),
  });

  /* One message per timer, each at t0 + the waits before it, like host/play.py play(). What it
   * holds down and the layers it put up are remembered, so stop() can hand the board back empty
   * whenever it is interrupted. */
  class Player {
    constructor(send, clock) {
      this.send = send;
      this.clock = clock || clockOfWindow();
      this.timer = null;
      this.demo = null;
      this.held = new Set();
      this.layers = [];
      this.playing = false;
    }

    play(demo, opts) {
      this.stop();
      this.demo = demo;
      this.loop = !!(opts && opts.loop);
      this.playing = !!(demo && demo.timeline && demo.timeline.length);   // nothing to loop over otherwise
      if (this.playing) this.begin();
    }

    begin() {
      this.i = 0;
      this.at = 0;
      this.t0 = this.clock.now();
      this.next();
    }

    next() {
      const timeline = this.demo.timeline || [];
      if (this.i >= timeline.length) {
        // The end: the script's own tail (the strip fading), then again or done.
        const rest = Math.max(0, (this.demo.duration_ms || this.at) - (this.clock.now() - this.t0));
        this.timer = this.clock.setTimeout(() => {
          this.timer = null;
          if (this.loop) this.begin(); else this.playing = false;
        }, rest);
        return;
      }
      const due = this.t0 + this.at + timeline[this.i][0];
      this.timer = this.clock.setTimeout(() => this.fire(), Math.max(0, due - this.clock.now()));
    }

    fire() {
      this.timer = null;
      const [wait, msg] = this.demo.timeline[this.i];
      this.at += wait;
      const late = this.clock.now() - (this.t0 + this.at);
      if (late > LATE_MS) this.t0 += late;
      this.deliver(msg);
      this.i++;
      this.next();
    }

    deliver(msg) {
      const m = Object.assign({}, msg, { sent: true });   // played, never the keyboard's own
      if (m.kind === "press") this.held.add(m.pos);
      else if (m.kind === "release") this.held.delete(m.pos);
      else if (m.kind === "layers") this.layers = (m.ids || []).slice();
      this.send(m);
    }

    stop() {
      if (this.timer !== null) this.clock.clearTimeout(this.timer);
      this.timer = null;
      this.playing = false;
      for (const pos of this.held) this.send({ kind: "release", pos, sent: true });
      this.held.clear();
      if (this.layers.length) this.send({ kind: "layers", ids: [], sent: true });
      this.layers = [];
    }
  }

  return { Translator, Player, NAMED, LATE_MS, diff };
});
