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
const View = require("../view.js");

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
// Everything due up to `t`, each at its own time: the answer, with what was said on the way.
function runTo(ks, t) { return ks.tick(t); }
// A keymap read, and everything said from then on kept, by reads and ticks alike.
function session() {
  const ks = fresh(), said = [];
  return {
    said,
    read(m, t) { const o = ks.read(m, t); said.push(...o.said); return o; },
    to(t) { const o = ks.tick(t); said.push(...o.said); return o; },
  };
}

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

// A base key's ZMK position, for keystrokes that only need to have happened.
const E = Number(Object.entries(DIAMOND.positions).find(([, i]) => i === DIAMOND.layers[DIAMOND.base].findIndex(k => k.tap === "e"))[0]);
const PRESS_MS = (DIAMOND.hud && DIAMOND.hud.press_ms) || K.DEFAULTS.press_ms;

(function aDropRightAfterAKeyWaitsForItsFlash() {
  const ks = fresh();
  const at = 1100, due = at + PRESS_MS;
  ks.read({ kind: "layers", ids: [ALPHA2] }, 1000);
  ks.read({ kind: "press", pos: E }, at);
  ks.read({ kind: "release", pos: E }, at + 10);
  let out = ks.read({ kind: "layers", ids: [] }, at + 20);
  check(out.board.layers[0] === "alpha2" && !says(out, "layers").length, "a layer falling away right after a key stays while the key flashes");
  out = ks.read({ kind: "layers", ids: [] }, at + 40);
  out = runTo(ks, due - 1);
  check(out.board.layers[0] === "alpha2" && !says(out, "layers").length, "said again, it keeps its time: still up just before press_ms after the key");
  out = runTo(ks, due);
  check(out.board.layers[0] === DIAMOND.base && eq(says(out, "layers"), [{ say: "layers", ids: [] }]), "and gone at it");
})();

(function theNextKeyAppliesAHeldDropAtOnce() {
  const ks = fresh();
  const at = 1100;
  ks.read({ kind: "layers", ids: [ALPHA2] }, 1000);
  ks.read({ kind: "press", pos: E }, at);
  ks.read({ kind: "release", pos: E }, at + 10);
  ks.read({ kind: "layers", ids: [] }, at + 20);
  const out = ks.read({ kind: "press", pos: E }, at + 30);
  check(out.board.layers[0] === DIAMOND.base && says(out, "layers").length === 1,
        "a key going down applies the drop it was waiting behind");
})();

// ---------- the reports ----------

const MOMENTARY_MS = (DIAMOND.hud && DIAMOND.hud.momentary_ms) || K.DEFAULTS.momentary_ms;
const keyDown = (chars, more) => Object.assign({ kind: "key", type: "keyDown", name: null, chars, flags: {} }, more || {});
const posOfIdx = idx => Number(Object.entries(DIAMOND.positions).find(([, i]) => i === idx)[0]);

(function aCharacterOnlyAHeldLayerTypesBringsItUpForAWhile() {
  const ks = fresh();                                  // no layers from the keyboard: inferred
  let out = ks.read(keyDown("\\"), 1000);
  check(eq(out.board.momentary, ["numbers"]) && out.board.layers[0] === "numbers", `\\ is on Numbers alone: held, inferred (${out.board.momentary})`);
  out = runTo(ks, 1000 + MOMENTARY_MS);
  check(eq(out.board.momentary, ["numbers"]), "still up momentary_ms after it");
  out = runTo(ks, 1000 + MOMENTARY_MS + 20);
  check(eq(out.board.momentary, []) && out.board.layers[0] === DIAMOND.base, "and then it lets go");
})();

(function aKeyTheFeedCannotNameIsNamedByTheBoard() {
  const ks = fresh();
  ks.read({ kind: "layers", ids: [] }, 900);
  const b = DIAMOND.layers[DIAMOND.base].findIndex(k => k.tap === "b");
  ks.read({ kind: "press", pos: posOfIdx(b) }, 1000);
  let out = ks.read(keyDown("", { name: "usage9c" }), 1010);
  check(out.label === "b", `a usage the feed has no name for reads as its key: ${out.label}`);
  ks.read({ kind: "release", pos: posOfIdx(b) }, 1050);
  out = ks.read(keyDown("", { name: "usage9c" }), 2500);
  check(out.label === null, "but not from a press long gone");
  out = ks.read(keyDown("x", { name: "x" }), 2600);
  check(out.label === null, "and a key the feed names needs none");
  const combo = DIAMOND.combos.find(c => c.layers.includes(DIAMOND.base) && c.positions.length === 2 && c.key.tap);
  ks.read({ kind: "press", pos: posOfIdx(combo.positions[0]) }, 5000);
  ks.read({ kind: "press", pos: posOfIdx(combo.positions[1]) }, 5005);
  out = ks.read(keyDown("", { name: "usage9d" }), 5010);
  check(out.label === combo.key.tap, `a combo's keystroke reads as its legend, not its last key: ${out.label}`);
})();

// ---------- what is said, once nothing can take it back ----------

const TERM = (DIAMOND.combo_term || 50) + ((DIAMOND.hud && DIAMOND.hud.combo_slack_ms) || K.DEFAULTS.combo_slack_ms);
const onBase = c => c.layers.includes(DIAMOND.base);
const posKey = idxs => idxs.map(posOfIdx).sort((a, b) => a - b).join(",");
const counted = said => said.filter(s => ["press", "combo", "guess"].includes(s.say));

(function aKeyIsSaidOnceNothingCanTakeItBack() {
  const k = session();
  k.read({ kind: "layers", ids: [] }, 900);
  k.read({ kind: "press", pos: E }, 1000);
  k.read({ kind: "release", pos: E }, 1040);
  k.to(1000 + TERM - 1);
  check(!counted(k.said).length, "not within the combo term: a combo could still claim it");
  k.to(1000 + TERM);
  const e = DIAMOND.layers[DIAMOND.base].findIndex(key => key.tap === "e");
  check(eq(counted(k.said), [{ say: "press", idx: e, pos: E, layer: DIAMOND.base, own: true, t: 1000 }]), `then once, on its layer: ${JSON.stringify(k.said)}`);
  check(eq(k.said.filter(s => s.say === "keystroke").map(s => s.as), ["tap"]), "and it typed: a tap");
})();

(function aBiggerChordTakesTheSmallerCombosPlace() {
  const big = DIAMOND.combos.find(c => onBase(c) && c.key.tap === "meh ␣");
  const small = DIAMOND.combos.find(c => onBase(c) && c.positions.length === 2 && c.positions.every(p => big.positions.includes(p)) &&
                                          c.positions.includes(big.positions[0]));
  const [a, b] = small.positions, c = big.positions.find(p => !small.positions.includes(p));
  const k = session();
  k.read({ kind: "layers", ids: [] }, 900);
  k.read({ kind: "press", pos: posOfIdx(a) }, 1000);
  k.read({ kind: "press", pos: posOfIdx(b) }, 1005);
  k.read({ kind: "press", pos: posOfIdx(c) }, 1010);
  for (const p of [a, b, c]) k.read({ kind: "release", pos: posOfIdx(p) }, 1200);
  k.to(1300);
  const combos = k.said.filter(s => s.say === "combo");
  check(eq(combos.map(s => s.key), [posKey(big.positions)]), `one chord, one combo -- ${JSON.stringify(big.key.tap)}, not ${JSON.stringify(small.key.tap)}: ${JSON.stringify(combos)}`);
  check(k.said.filter(s => s.say === "press").length === 3, "and each of its keys pressed once");
  check(eq(k.said.filter(s => s.say === "keystroke").map(s => s.as), ["chord"]), "a chord is one keystroke");
})();

(function aGuessItsPositionTakesBackIsNotSaid() {
  const k = session();
  k.read({ kind: "layers", ids: [] }, 900);
  k.read(keyDown("e", { name: "e" }), 1000);              // the report first, its position after
  k.read({ kind: "press", pos: E }, 1010);
  k.read({ kind: "release", pos: E }, 1050);
  k.to(2000);
  check(eq(counted(k.said).map(s => s.say), ["press"]), `the position's press is said, the guess is not: ${JSON.stringify(counted(k.said))}`);
})();

(function aKeyThatCanBeHeldIsATapOrAHold() {
  const holder = DIAMOND.layers[DIAMOND.base].findIndex(key => key.hold && key.hold.includes("⇧"));
  const pos = posOfIdx(holder);
  const strokes = k => k.said.filter(s => s.say === "keystroke" || s.say === "hold").map(s => s.as || s.say);
  let k = session();
  k.read({ kind: "layers", ids: [] }, 900);
  k.read({ kind: "press", pos }, 1000);
  k.read({ kind: "release", pos }, 1080);
  k.to(2000);
  check(eq(strokes(k), ["tap"]), `let go quickly, a home-row mod typed: ${strokes(k)}`);
  k = session();
  k.read({ kind: "layers", ids: [] }, 900);
  k.read({ kind: "press", pos }, 1000);
  k.read({ kind: "release", pos }, 1600);
  k.to(2000);
  check(eq(strokes(k), ["hold"]), `held past HOLD_MS, it typed nothing: ${strokes(k)}`);
})();

(function aSecretTakesWhatWaitsWithIt() {
  const k = session();
  k.read({ kind: "layers", ids: [] }, 900);
  k.read({ kind: "press", pos: E }, 1000);
  k.read({ kind: "secure", on: true }, 1010);
  k.read({ kind: "press", pos: E }, 1020);
  k.to(3000);
  check(!counted(k.said).length, "nothing typed before a secret, or during it, is said");
  k.read({ kind: "secure", on: false }, 3100);
  k.read({ kind: "press", pos: E }, 3200);
  k.to(4000);
  check(counted(k.said).length === 1, "and typing after it is");
})();

// ---------- what the board shows ----------

const RELEASE_MS = (DIAMOND.hud && DIAMOND.hud.release_ms) || K.DEFAULTS.release_ms;
const PILL_MS = (DIAMOND.hud && DIAMOND.hud.combo_pill_ms) || K.DEFAULTS.combo_pill_ms;
const eIdx = DIAMOND.layers[DIAMOND.base].findIndex(key => key.tap === "e");

(function aKeyIsLitWhileItIsDownAndAMomentAfter() {
  const k = session();
  k.read({ kind: "layers", ids: [] }, 900);
  let out = k.read({ kind: "press", pos: E }, 1000);
  check(eq(out.board.lit, [eIdx]) && eq(out.board.held, [eIdx]), "a key down is lit, and held");
  out = k.to(3000);
  check(eq(out.board.lit, [eIdx]), "for as long as it is down");
  k.read({ kind: "release", pos: E }, 3000);
  out = k.to(3000 + RELEASE_MS - 1);
  check(eq(out.board.lit, [eIdx]) && eq(out.board.held, []), "up, it is still lit for release_ms");
  out = k.to(3000 + RELEASE_MS);
  check(eq(out.board.lit, []), "and then out");
})();

(function aBiggerChordsPillTakesTheSmallerOnesPlace() {
  const big = DIAMOND.combos.find(c => onBase(c) && c.key.tap === "meh ␣");
  const small = DIAMOND.combos.find(c => onBase(c) && c.positions.length === 2 && c.positions.every(p => big.positions.includes(p)) &&
                                          c.positions.includes(big.positions[0]));
  const k = session();
  k.read({ kind: "layers", ids: [] }, 900);
  for (const p of small.positions) k.read({ kind: "press", pos: posOfIdx(p) }, 1000);
  let out = k.read({ kind: "press", pos: posOfIdx(big.positions.find(p => !small.positions.includes(p))) }, 1005);
  check(eq(out.board.pills.map(p => p.key.tap), [big.key.tap]), `one pill, the bigger chord's: ${out.board.pills.map(p => p.key.tap)}`);
  check(eq(out.board.chord.slice().sort((a, b) => a - b), big.positions.slice().sort((a, b) => a - b)), "its keys lit as a combo's");
  out = k.to(1005 + PILL_MS - 1);
  check(out.board.pills.length === 1, "up for combo_pill_ms");
  out = k.to(1005 + PILL_MS);
  check(out.board.pills.length === 0, "and then down");
})();

(function aGuessedPillGoesWhenTheChordsPositionsCome() {
  const z = DIAMOND.combos.find(c => onBase(c) && c.key.tap === "z");
  const k = session();
  k.read({ kind: "layers", ids: [] }, 900);
  let out = k.read(keyDown("z", { name: "z", combos: true }), 1000);
  check(eq(out.board.pills.map(p => p.key.tap), ["z"]), "typed-in z with combos: the z chord's pill");
  const id = out.board.pills[0].id;
  // Sent in: the keyboard's own presses for the chord, a moment later.
  for (const p of z.positions) out = k.read({ kind: "press", pos: posOfIdx(p) }, 1010);
  check(out.board.pills.length === 1 && out.board.pills[0].id !== id, "taken down for the chord's own pill, one pill all the same");
})();

(function theTouchKeyboardsLayersAreWhatAPressResolvesOn() {
  const k = session();
  k.read({ kind: "layers", ids: [] }, 900);
  k.read({ kind: "touch", ids: [NAV, 0] }, 950);
  k.read({ kind: "press", pos: E }, 1000);
  k.to(2000);
  const press = k.said.find(s => s.say === "press");
  check(press && press.layer === View.resolve(DIAMOND, eIdx, View.drawnStack(DIAMOND, [NAV, 0])).layer,
        `a press counts where the touch keyboard's layers put it: ${press && press.layer}`);
})();

(function aTouchKeyboardShowingTheKeyboardsLayersFollowsThem() {
  const k = session();
  k.read({ kind: "layers", ids: [ALPHA2] }, 900);
  k.read({ kind: "touch", ids: [ALPHA2, 0] }, 950);             // the keyboard's own, nothing on glass
  k.read({ kind: "press", pos: E }, 1000);
  k.read({ kind: "release", pos: E }, 1010);
  k.read({ kind: "layers", ids: [] }, 1020);                    // held back for the key's flash
  const out = k.read({ kind: "press", pos: E }, 1030);         // ...and applied by the next key
  check(out.board.layers[0] === DIAMOND.base, `the next key resolves on the layers really up: ${out.board.layers}`);
})();

(function aSecretPutsOutWhatIsLit() {
  const k = session();
  k.read({ kind: "layers", ids: [] }, 900);
  k.read(keyDown("z", { name: "z", combos: true }), 1000);
  k.read({ kind: "press", pos: E }, 1010);
  const out = k.read({ kind: "secure", on: true }, 1020);
  check(eq(out.board.lit, []) && eq(out.board.pills, []) && eq(out.board.held, []) && out.board.secure, "nothing lit, no pill, nothing held");
})();

// ---------- a chord held for its modifiers ----------

(function aChordHeldForItsModifiersReadsItsHoldUntilItIsLetGo() {
  const q = DIAMOND.combos.find(c => onBase(c) && c.key.tap === "q");
  const k = session();
  k.read({ kind: "layers", ids: [] }, 900);
  for (const p of q.positions) k.read({ kind: "press", pos: posOfIdx(p) }, 1000);
  k.read({ kind: "key", type: "flagsChanged", flags: { ctrl: true, alt: true } }, 1250);
  let out = k.to(1300);
  check(eq(out.board.pills.map(p => p.hold), ["⌃⌥"]) && out.board.pills[0].until === null, `held, the pill reads ⌃⌥ and stays: ${JSON.stringify(out.board.pills)}`);
  check(eq(out.board.modSource.ctrl, q.positions), "and the chord's keys turned the modifiers on");
  out = k.to(3000);
  check(out.board.pills.length === 1, "still up while the chord is down");
  const id = out.board.pills[0].id;
  out = k.read({ kind: "release", pos: posOfIdx(q.positions[0]) }, 3100);
  check(!out.board.pills.length && eq(out.board.faded, [id]), "let go, it fades out");
})();

(function aPositionToldTwiceIsOnePress() {
  const k = session();
  k.read({ kind: "layers", ids: [] }, 900);
  k.read({ kind: "press", pos: E }, 1000);
  k.read({ kind: "press", pos: E }, 1001);                    // the same press, by BLE as well as USB
  k.read({ kind: "release", pos: E }, 1100);
  k.read({ kind: "release", pos: E }, 1101);
  k.to(2000);
  check(counted(k.said).length === 1, `one press said: ${JSON.stringify(counted(k.said))}`);
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
