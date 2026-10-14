#!/usr/bin/env node
/* zmk-layer-hud — the keystrokes (hud/keystrokes.js) through their own interface: messages read at
 * the times a test gives, what comes due ticked, and what the board then shows and what was said.
 * No DOM and no clock but the numbers passed in: the page's own suites (hud_test, words_test,
 * stats_test, ...) check the same behaviour through the page.
 *
 *   node hud/tests/keystrokes_test.js [--verbose]
 */
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const K = require("../keystrokes.js");

const VERBOSE = process.argv.includes("--verbose");
const fail = [];
let checks = 0;
function check(ok, what) { checks++; if (!ok) fail.push(what); else if (VERBOSE) console.log("ok   " + what); }
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

const DIAMOND = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", "diamond.json"), "utf8"));
const idOf = drawer => Number(Object.entries(DIAMOND.zmk_layers).find(([, z]) => z && z.drawer === drawer)[0]);
const ALPHA2 = idOf("alpha2"), NAV = idOf("nav");
const fresh = () => { const ks = K.create(); ks.read(DIAMOND, 0); return ks; };
const says = (out, kind) => out.said.filter(s => s.say === kind);

// ---------- the layers ----------

(function beforeAKeymapNothingIsRead() {
  const ks = K.create();
  const out = ks.read({ kind: "layers", ids: [ALPHA2] }, 10);
  check(!out.board.ready && eq(out.board.layers, []) && !out.said.length, "before a keymap, nothing is read");
})();

(function aKeymapStartsOnItsBase() {
  const out = K.create().read(DIAMOND, 0);
  check(out.board.ready && eq(out.board.layers, [DIAMOND.base]) && out.board.live === null, "a keymap starts on its base, nothing reported");
  check(out.next === null, "and waits for nothing");
})();

(function aLayerThatComesUpAppliesAtOnce() {
  const ks = fresh();
  const out = ks.read({ kind: "layers", ids: [ALPHA2] }, 1000);
  check(eq(out.board.layers, ["alpha2", DIAMOND.base]), `alpha2 over the base: ${out.board.layers}`);
  check(eq(says(out, "layers"), [{ say: "layers", ids: [ALPHA2] }]), "and is said");
  check(eq(out.board.live.ids, [ALPHA2]) && out.board.live.at === 1000, "the keyboard's ids, as of when they came");
})();

(function aDropWithNoKeyStruckAppliesAtOnce() {
  const ks = fresh();
  ks.read({ kind: "layers", ids: [ALPHA2] }, 1000);
  const out = ks.read({ kind: "layers", ids: [] }, 5000);
  check(eq(out.board.layers, [DIAMOND.base]) && says(out, "layers").length === 1 && out.next === null,
        "a layer falling away long after the last key goes at once");
})();

(function layersNoLongerReportedAreEmulatedAgain() {
  const ks = fresh();
  ks.read({ kind: "layers", ids: [ALPHA2] }, 1000);
  const out = ks.read({ kind: "layers", ids: null }, 2000);
  check(out.board.live === null && eq(out.board.layers, [DIAMOND.base]), "ids null: the keyboard's layers are not known");
})();

(function theTouchKeyboardsLayersAreWhatKeysResolveOn() {
  const ks = fresh();
  ks.read({ kind: "layers", ids: [ALPHA2] }, 1000);
  let out = ks.read({ kind: "touch", ids: [NAV] }, 1100);
  check(out.board.layers[0] === "nav", `the touch keyboard's nav over the keyboard's alpha2: ${out.board.layers}`);
  out = ks.read({ kind: "touch", ids: null }, 1200);
  check(out.board.layers[0] === "alpha2", "off again: the keyboard's own");
})();

(function whatIsNotAMessageIsLeftAlone() {
  const ks = fresh();
  const out = ks.read({ kind: "nonsense", ids: [ALPHA2] }, 1000);
  check(eq(out.board.layers, [DIAMOND.base]) && !out.said.length, "an unknown kind changes nothing");
  check(eq(ks.read(null, 1001).board.layers, [DIAMOND.base]), "nor does no message");
  let threw = false;
  try { ks.read({ kind: "layers", ids: [] }, NaN); } catch (e) { threw = e instanceof TypeError; }
  check(threw, "a time that is not a number is the caller's mistake: TypeError");
})();

(function aFeedPlaysOnItsOwnClock() {
  const r = K.play([[0, DIAMOND], [1000, { kind: "layers", ids: [ALPHA2] }], [2000, { kind: "layers", ids: [NAV] }]], 3000);
  check(r.board.layers[0] === "nav" && eq(r.said.map(s => s.ids), [[ALPHA2], [NAV]]), "play reads each message at its time");
})();

console.log(`the keystrokes: ${checks} checks, ${fail.length} failures`);
if (fail.length) {
  for (const f of fail) console.log("FAIL " + f);
  process.exit(1);
}
