#!/usr/bin/env node
/* zmk-layer-hud — the touch keyboard for native shells (hud/kbd.js) and the board it describes
 * (hud/view.js), on a clock the test moves: what a tap types, holds and layers, a dead key, what the
 * board says and where its keys go. The iPad keyboard runs exactly this in JavaScriptCore.
 *
 *   node hud/tests/kbd_test.js [--verbose]
 *
 *   base:  a   '   ␣/nav (a layer-tap)   ⇧ (a plain Shift)   e   ✕ (a mouse button: not here)
 *   nav:   1   ▽   (held)                ⇧                    ▽   ▽
 * The first three keys are the left half, the rest the right, a key's width apart.
 */
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const Kbd = require("../kbd.js");
const View = require("../view.js");

const VERBOSE = process.argv.includes("--verbose");
const fail = [];
let checks = 0;
function check(ok, what) { checks++; if (!ok) fail.push(what); else if (VERBOSE) console.log("ok   " + what); }
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// The keymap the iPad keyboard's golden answers are made on too (apps/fixtures).
const MSG = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "..", "apps", "fixtures", "kbd-keymap.json"), "utf8"));

const fresh = () => Kbd.create(JSON.stringify(MSG));
const texts = (ops) => ops.map((o) => (o.kind === "text" ? o.text : `<${o.key || o.chars}>`)).join("");

function tap(k, idx, t, ms) {
  const a = k.down(idx, t), b = k.up(idx, t + (ms || 40));
  return { ops: a.ops.concat(b.ops), refused: a.refused.concat(b.refused), next: b.next };
}

(function aTapTypesItsKey() {
  const k = fresh();
  const r = tap(k, 0, 1000);
  check(texts(r.ops) === "a", `a tap types its letter: ${JSON.stringify(r.ops)}`);
  check(eq(r.ops[0], { kind: "text", text: "a", usage: 4, mods: 0 }), "with its usage and modifiers");
  check(r.next === null, "and leaves nothing to wait for");
})();

(function shiftHeldShiftsTheLetterAndTheLegends() {
  const k = fresh();
  k.down(3, 1000);
  check(k.board().keys[0].tap === "A", `Shift held, the board's letters are capitals: ${k.board().keys[0].tap}`);
  const r = tap(k, 0, 1100);
  k.up(3, 1200);
  check(texts(r.ops) === "A", `and a letter typed under it is one: ${texts(r.ops)}`);
  check(k.board().keys[0].tap === "a", "let go, they are small again");
})();

(function aHoldIsTheLayerAndTheBoardSaysSo() {
  const k = fresh();
  const d = k.down(2, 1000);
  check(d.next !== null && d.next > 1000, `a layer-tap down waits to be a hold: next ${d.next}`);
  const h = k.tick(1000 + 300);
  check(h.changed, "past hold_ms the board changes");
  const b = k.board();
  check(b.keys[0].tap === "1" && b.banner.name === "Nav", `to the layer: ${b.keys[0].tap}, banner ${b.banner.name}`);
  check(b.keys[1].tap === "'" && b.keys[1].cls.includes("trans"), "a transparent key shows the layer under it, marked");
  check(b.keys[2].cls.includes("pressed") && b.keys[2].cls.includes("held"), `the key held is lit: ${b.keys[2].cls}`);
  check(b.banner.sub === "touch · held", `and the banner says what glass holds: ${b.banner.sub}`);
  const r = tap(k, 0, 1400);
  check(texts(r.ops) === "1", `a key typed on it is the layer's: ${texts(r.ops)}`);
  k.up(2, 1500);
  check(k.board().keys[0].tap === "a" && k.board().banner.name === "Base", "let go, the base again");
})();

(function aQuickTapOfTheLayerTapIsItsTap() {
  const k = fresh();
  const r = tap(k, 2, 1000, 50);
  check(texts(r.ops) === " ", `a quick tap of a layer-tap types its tap: ${JSON.stringify(r.ops)}`);
})();

(function aDeadKeyWaitsForItsLetterOrComesOutAlone() {
  const k = fresh();
  const a = tap(k, 1, 1000);
  check(a.ops.length === 0 && a.next !== null, `a dead key types nothing yet and asks to be called back: ${a.next}`);
  const e = tap(k, 4, 1010);
  check(texts(e.ops) === "é", `with its letter it is the accented one: ${texts(e.ops)}`);
  const b = tap(k, 1, 2000);
  check(texts(k.tick(b.next).ops) === "'", "alone, it comes out when its time is up");
  check(k.tick(b.next + 1000).ops.length === 0, "once");
})();

(function whatCannotTypeIsSaidAndNothingIsTyped() {
  const k = fresh();
  const r = tap(k, 5, 1000);
  check(eq(r.refused, [5]) && r.ops.length === 0, `a mouse button refuses on a text keyboard: ${JSON.stringify(r)}`);
  const m = JSON.parse(JSON.stringify(MSG));
  delete m.positions["0"];
  const k2 = Kbd.create(m);
  const s = k2.down(0, 1000);
  check(!s.changed && !k2.board().keys[0].cls.includes("pressed"), "a key with no position is drawn, never typed");
  let threw = "";
  try { Kbd.create(Object.assign({}, MSG, { actions: { error: "no keymap captured" } })); } catch (x) { threw = x.message; }
  check(threw === "no keymap captured", `a keymap that cannot type is a sentence: ${threw}`);
})();

(function releaseAllLetsEverythingGo() {
  const k = fresh();
  k.down(3, 1000);
  k.down(2, 1000);
  k.tick(1400);
  k.releaseAll(1500);
  const b = k.board();
  check(b.banner.name === "Base" && b.keys.every((f) => !f.cls.includes("pressed")) && b.keys[0].tap === "a",
        "everything up, the base drawn, nothing lit");
})();

(function aComboIsAChipToTap() {
  const k = fresh();
  const chips = k.chips.call(null, 1400, 200, true);     // as JSKbd calls it: detached
  check(chips.length === 1 && chips[0].tap === "⎋" && eq(chips[0].keys, [0, 1]), `the base's combo is a chip: ${JSON.stringify(chips)}`);
  const g = k.geometry(1400, 200, true);
  check(Math.abs(chips[0].x - (g.centers[0].x + g.centers[1].x) / 2) < 1e-6, "between its keys");
  const d = k.chord(0, true, 1000);
  check(k.board().keys[0].cls.includes("pressed") && k.board().keys[1].cls.includes("pressed") &&
        eq(k.chips(1400, 200, true)[0].cls, ["pressed"]), "held, the chip and its keys light");
  const u = k.chord(0, false, 1040);
  check(texts(d.ops.concat(u.ops)) === "<escape>", `tapped, it is the combo: ${JSON.stringify(d.ops.concat(u.ops))}`);
  check(!k.chord(0, false, 1100).changed, "a chip let go twice is let go once");
  k.down(2, 2000);
  k.tick(2300);
  check(k.chips(1400, 200, true).length === 0, "on nav, which the combo is not on, no chip");
  const off = Kbd.create(Object.assign({}, MSG, { touch: Object.assign({}, MSG.touch, { combos: "off" }) }));
  check(off.chips(1400, 200, true).length === 0, "touch.combos: off draws none");
})();

(function theBoardsGeometryPutsTheHalvesAtTheSides() {
  const k = fresh();
  const g = k.geometry(1400, 200, true);
  const right = g.rects[5].x + g.rects[5].w, left = g.rects[0].x;
  // A key's slot starts a gap from the side, and the key is drawn half a gap inside its slot.
  check(Math.abs(left - 1.5 * g.gap) < 1e-6 && Math.abs(1400 - 1.5 * g.gap - right) < 1e-6,
        `split, the halves meet the sides a gap in: ${left.toFixed(1)}..${right.toFixed(1)}`);
  check(g.rects[2].x + g.rects[2].w < g.rects[3].x - 100, "with the room between them");
  check(g.height <= 200, `no taller than allowed: ${g.height}`);
  const whole = k.geometry(1400, 200, false);
  check(Math.abs(whole.rects[0].x - (1400 - 700 * whole.scale) / 2 - whole.gap / 2) < 1e-6, "in one piece, centred");
  check(View.halves(MSG.layout).left.size === 3, "the left half is the three keys before the gap");
  const row = (xs) => ({ width: Math.max(...xs) + 50, height: 100, keys: xs.map((x) => ({ x, y: 50, w: 100, h: 100, r: 0 })) });
  // The Diamond's thumb clusters come within 0.43 of a key of each other: still two halves.
  check(View.halves(row([50, 150, 250, 393, 493, 593])) !== null, "halves a little under half a key apart are halves");
  check(View.halves(row([50, 150, 250, 375, 475, 575])) === null, "a quarter of a key apart is one board");
  // A unibody's cluster set apart near one end (a nav cluster) is not a split.
  check(View.halves(row([50, 150, 250, 350, 450, 550, 650, 790])) === null, "a gap near the end is not the middle");
})();

console.log(`the touch keyboard for native shells: ${checks} checks, ${fail.length} failures`);
if (fail.length) {
  for (const f of fail) console.log("FAIL " + f);
  process.exit(1);
}
