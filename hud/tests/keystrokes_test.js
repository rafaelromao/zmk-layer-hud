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

(function aNamedKeyLightsWhateverTheDrawingSpellsItWith() {
  // keymap-drawer's own parse spells named keys as words, in any case: Tab, BSpace, Page Down. A
  // drawing's Delete is the key that deletes forwards; the feed's `delete` is Backspace.
  const X35 = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", "example-3x5.json"), "utf8"));
  const at = (layer, legend) => X35.layers[layer].findIndex(k => k.tap === legend);
  for (const [name, layer, legend] of [["tab", "DEF", "Tab"], ["escape", "DEF", "Esc"], ["delete", "DEF", "BSpace"],
                                       ["return", "DEF", "Enter"], ["forwarddelete", "DEF", "Delete"],
                                       ["pagedown", "NAV", "Page Down"], ["capslock", "FUN", "Caps Lock"]]) {
    const ks = K.create();
    ks.read(X35, 0);
    const out = ks.read(keyDown("", { name }), 1000);
    // A key off the base lights with what brings its layer up.
    check(out.board.lit.includes(at(layer, legend)), `${name} lights the key drawn ${JSON.stringify(legend)}: ${out.board.lit}`);
  }
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

(function aChordStruckWithCtrlReadsItsCtrlLegend() {
  // A mod-morph's chord (keymap's DIAG_SYMBOL): what it makes is the modifiers held when it fires,
  // so its pill reads the state it was struck in -- the drawing's own tap and shifted without Ctrl,
  // its ctrl and ctrl+shift legends with it.
  const morph = mutate => {
    const doc = JSON.parse(JSON.stringify(DIAMOND));
    mutate(doc.combos[0].key);                       // positions [0,1] on the base, no hold
    const ks = K.create();
    ks.read(doc, 0);
    return { key: doc.combos[0].key, read: (m, t) => ks.read(m, t), to: t => ks.tick(t) };
  };
  const SHIFT_KEY = 23;                              // the drawing's sticky shift: held as a key
  const strike = (m, flags, held = []) => {
    m.read({ kind: "layers", ids: [] }, 900);
    for (const i of held) m.read({ kind: "press", pos: posOfIdx(i) }, 600);
    if (flags) m.read({ kind: "key", type: "flagsChanged", flags }, 990);
    for (const p of DIAMOND.combos[0].positions) m.read({ kind: "press", pos: posOfIdx(p) }, 1000);
    return m.to(1400).board.pills;
  };
  const ctrl = k => { k.left = "=="; k.right = "!="; };

  {
    // The ctrl report trails the chord's keys, as it can when positions and modifiers come on
    // different channels: the pill still reads the ctrl legend.
    const m = morph(k => { k.tap = "("; k.shifted = ")"; ctrl(k); });
    m.read({ kind: "layers", ids: [] }, 900);
    for (const p of DIAMOND.combos[0].positions) m.read({ kind: "press", pos: posOfIdx(p) }, 1000);
    m.read({ kind: "key", type: "flagsChanged", flags: { ctrl: true } }, 1005);
    const pills = m.to(1400).board.pills;
    check(pills.length === 1 && pills[0].key.tap === "==",
          `a ctrl report that trails the chord still makes its ctrl legend: ${JSON.stringify(pills.map(p => p.key))}`);
  }

  {
    // Ctrl held on its own key, and the report says it was let go as the chord fires: ZMK masks the
    // modifiers of a mod-morph from the report while it runs, so the held key is what says Ctrl is down.
    const m = morph(k => { k.tap = "("; k.shifted = ")"; ctrl(k); });
    m.read({ kind: "layers", ids: [] }, 900);
    m.read({ kind: "press", pos: posOfIdx(7) }, 600);
    m.read({ kind: "key", type: "flagsChanged", flags: { ctrl: true } }, 610);
    const [first, second] = DIAMOND.combos[0].positions;
    m.read({ kind: "press", pos: posOfIdx(first) }, 1000);
    m.read({ kind: "key", type: "flagsChanged", flags: { ctrl: false } }, 1000);
    m.read({ kind: "press", pos: posOfIdx(second) }, 1000);
    const pills = m.to(1400).board.pills;
    check(pills.length === 1 && pills[0].key.tap === "==",
          `a masked report does not let go of a held Ctrl key: ${JSON.stringify(pills.map(p => p.key))}`);
  }

  {
    // Command held on its own key: the premise (not checked on a keyboard) is that ZMK sends a
    // shortcut for the chord, not what its four legends say, so that chord draws no pill.
    const m = morph(k => { k.tap = "("; k.shifted = ")"; ctrl(k); });
    m.read({ kind: "layers", ids: [] }, 900);
    m.read({ kind: "press", pos: posOfIdx(16) }, 600);
    for (const p of DIAMOND.combos[0].positions) m.read({ kind: "press", pos: posOfIdx(p) }, 1000);
    const pills = m.to(1400).board.pills;
    check(pills.length === 0, `a chord with Command held draws no pill: ${JSON.stringify(pills.map(p => p.key))}`);
  }

  let pills = strike(morph(k => { k.tap = "("; k.shifted = ")"; ctrl(k); }), { ctrl: true });
  check(pills.length === 1 && pills[0].key.tap === "==" && pills[0].key.shifted === "!=",
        `struck with Ctrl, its ctrl legend: ${JSON.stringify(pills.map(p => p.key))}`);

  pills = strike(morph(k => { k.tap = "("; k.shifted = ")"; ctrl(k); }), { ctrl: true }, [SHIFT_KEY]);
  check(pills.length === 1 && pills[0].key.tap === "!=" && pills[0].key.shifted === "==",
        `struck with Ctrl and Shift, its ctrl+shift legend: ${JSON.stringify(pills.map(p => p.key))}`);

  pills = strike(morph(k => { k.tap = "("; k.shifted = ")"; ctrl(k); }), null);
  check(pills.length === 1 && pills[0].key.tap === "(" && pills[0].key.shifted === ")",
        `struck with nothing held, the legend it always had: ${JSON.stringify(pills.map(p => p.key))}`);

  pills = strike(morph(k => { k.tap = "("; k.shifted = ")"; }), { ctrl: true });
  check(pills.length === 1 && pills[0].key.tap === "(" && pills[0].key.shifted === ")",
        `a chord that says nothing about Ctrl is drawn as it always was: ${JSON.stringify(pills.map(p => p.key))}`);

  pills = strike(morph(k => { k.tap = "("; k.shifted = ")"; ctrl(k); }), null, [SHIFT_KEY]);
  check(pills.length === 1 && pills[0].key.tap === ")" && pills[0].key.shifted === "(",
        `struck with Shift, its shifted legend leads: ${JSON.stringify(pills.map(p => p.key))}`);

  {
    // A chord whose own key carries Ctrl (a home-row Ctrl): it is not a Ctrl held, so the plain
    // chord keeps its legend.
    const doc = JSON.parse(JSON.stringify(DIAMOND));
    doc.combos[0].positions = [7, 0];
    doc.combos[0].key.tap = "("; doc.combos[0].key.shifted = ")"; ctrl(doc.combos[0].key);
    const ks = K.create(); ks.read(doc, 0);
    ks.read({ kind: "layers", ids: [] }, 900);
    ks.read({ kind: "press", pos: posOfIdx(7) }, 1000);
    ks.read({ kind: "press", pos: posOfIdx(0) }, 1000);
    const got = ks.tick(1400).board.pills;
    check(got.length === 1 && got[0].key.tap === "(",
          `a chord whose own key is a Ctrl keeps its plain legend: ${JSON.stringify(got.map(p => p.key))}`);
  }

  pills = strike(morph(k => { k.tap = "("; k.shifted = ")"; ctrl(k); }), { ctrl: true, shift: true });
  check(pills.length === 1 && pills[0].key.tap === "==" && pills[0].key.shifted === "!=",
        `a shift in the output report alone is not a shift held: ${JSON.stringify(pills.map(p => p.key))}`);
})();

(function whatAFourStateChordLeavesAloneAndWhereItStopsAtTheKeys() {
  // The edges of the above: what the reading of held modifiers must not spill into (the board's own
  // modifiers, a chord's count, a chord without `left`), and what it does not read.
  const CHORD = DIAMOND.combos[0].positions;          // [0, 1] on the base
  const CTRL_KEY = 7, ALT_KEY = 8, CMD_KEY = 16, SHIFT_KEY = 23;   // n/⌃, s/⌥, p/⌘ held; ⇧ itself
  const rig = mutate => {
    const doc = JSON.parse(JSON.stringify(DIAMOND));
    if (mutate) mutate(doc.combos[0].key, doc);
    const ks = K.create(), said = [];
    ks.read(doc, 0);
    return {
      said,
      read(m, t) { const o = ks.read(m, t); said.push(...o.said); return o; },
      to(t) { const o = ks.tick(t); said.push(...o.said); return o; },
    };
  };
  const four = k => { k.tap = "("; k.shifted = ")"; k.left = "=="; k.right = "!="; };
  const down = (m, i, t) => m.read({ kind: "press", pos: posOfIdx(i) }, t);
  const up = (m, i, t) => m.read({ kind: "release", pos: posOfIdx(i) }, t);
  const chord = (m, t) => { for (const p of CHORD) down(m, p, t); };
  const flags = (m, f, t) => m.read({ kind: "key", type: "flagsChanged", flags: f }, t);
  const legends = pills => JSON.stringify(pills.map(p => [p.key.tap, p.key.shifted]));
  const said = m => m.said.map(e => e.say + (e.as ? ":" + e.as : ""));

  // The board's own modifiers are the report's, as they were: a key's release is not a report.
  for (const [what, key, flag] of [["Ctrl", CTRL_KEY, "ctrl"], ["Shift", SHIFT_KEY, "shift"]]) {
    const m = rig();
    m.read({ kind: "layers", ids: [] }, 100);
    down(m, key, 600); flags(m, { [flag]: true }, 800);
    flags(m, { [flag]: false }, 1990);                // the report goes before the position does
    const out = up(m, key, 2000);
    check(!out.board.mods[flag] && out.board.modSource[flag] === undefined,
          `${what} let go in the report, its key let go after: no ${what} held, none ringed: ${JSON.stringify([out.board.mods, out.board.modSource])}`);
    check(!m.to(4000).board.mods[flag], `${what} does not come back by itself`);
  }
  {
    const m = rig();
    m.read({ kind: "layers", ids: [] }, 100);
    down(m, CTRL_KEY, 600);
    const out = flags(m, { alt: true }, 700);
    check(!out.board.mods.ctrl && out.board.mods.alt, `a key that can be a Ctrl is not a Ctrl on the board: ${JSON.stringify(out.board.mods)}`);
  }
  {
    // A chord on a home-row Ctrl of its own, and an Alt reported meanwhile: its hold is the Alt.
    const m = rig((k, doc) => { doc.combos[0].positions = [CTRL_KEY, 0]; four(k); });
    m.read({ kind: "layers", ids: [] }, 900);
    down(m, CTRL_KEY, 1000); down(m, 0, 1000);
    flags(m, { alt: true }, 1100);
    const out = m.to(1500);
    check(out.board.pills.length === 1 && out.board.pills[0].hold === "⌥" && !out.board.mods.ctrl,
          `the chord's own Ctrl key is no Ctrl held: ${JSON.stringify(out.board.pills.map(p => p.hold))} ${JSON.stringify(out.board.mods)}`);
  }

  // Command or Option held outside a four-state chord: no pill, and still the chord that fired.
  for (const [what, key] of [["Command", CMD_KEY], ["Option", ALT_KEY]]) {
    const m = rig(four);
    m.read({ kind: "layers", ids: [] }, 900);
    down(m, key, 600); chord(m, 1000);
    for (const p of CHORD) up(m, p, 1100);
    up(m, key, 1200);
    const out = m.to(3000), got = said(m);
    check(out.board.pills.length === 0, `${what} held: a four-state chord draws no pill`);
    check(got.includes("combo") && got.includes("keystroke:chord") && !got.includes("keystroke:tap"),
          `${what} held: it still counts as one chord, not as its keys: ${got}`);
    check(m.said.filter(e => e.say === "press").length === 3, `${what} held: its keys are still presses`);
  }
  {
    // Without `left` it is not a four-state chord: the pill is as it always was.
    const m = rig(k => { k.tap = "("; k.shifted = ")"; });
    m.read({ kind: "layers", ids: [] }, 900);
    down(m, CMD_KEY, 600); chord(m, 1000);
    const pills = m.to(1400).board.pills;
    check(legends(pills) === '[["(",")"]]', `Command held, a chord without left: its pill as always: ${legends(pills)}`);
    check(said(m).includes("combo"), "and counted as a chord");
  }

  // A glyph goes with its legend: the page draws a glyph in place of the text it is given.
  {
    const glyphs = k => { four(k); k.glyph = "mdi:tap"; k.glyph_shifted = "mdi:shifted"; k.glyph_hold = "mdi:hold"; };
    const read = (held, f) => {
      const m = rig(glyphs);
      m.read({ kind: "layers", ids: [] }, 900);
      for (const i of held) down(m, i, 600);
      if (f) flags(m, f, 990);
      chord(m, 1000);
      return m.to(1400).board.pills.map(p => p.key);
    };
    let [key] = read([], { ctrl: true });
    check(key.tap === "==" && !key.glyph && !key.glyph_shifted && !key.glyph_hold,
          `a ctrl legend is text, with none of the tap's glyphs: ${JSON.stringify(key)}`);
    [key] = read([SHIFT_KEY], null);
    check(key.tap === ")" && key.glyph === "mdi:shifted" && key.shifted === "(" && key.glyph_shifted === "mdi:tap",
          `the shifted legend takes its glyph along as the legends trade places: ${JSON.stringify(key)}`);
    [key] = read([], null);
    check(key.tap === "(" && key.glyph === "mdi:tap" && key.glyph_shifted === "mdi:shifted",
          `struck plain, its glyphs as drawn: ${JSON.stringify(key)}`);
  }

  // Which chords read Shift, and which legends they need.
  {
    const read = (mutate, held, f) => {
      const m = rig(mutate);
      m.read({ kind: "layers", ids: [] }, 900);
      for (const i of held) down(m, i, 600);
      if (f) flags(m, f, 990);
      chord(m, 1000);
      return legends(m.to(1400).board.pills);
    };
    const plain = k => { k.tap = "("; k.shifted = ")"; };
    check(read(plain, [SHIFT_KEY]) === '[["(",")"]]', "a Shift key held, a chord without left: as it always was");
    check(read(k => { plain(k); k.right = "!="; }, [SHIFT_KEY, CTRL_KEY], { ctrl: true }) === '[["(",")"]]',
          "right without left says nothing, a Ctrl and a Shift held or not");
    check(read(k => { plain(k); k.left = "=="; }, [], { ctrl: true }) === '[["==",""]]', "left without right: Ctrl reads left, nothing small");
    check(read(k => { plain(k); k.left = "=="; }, [SHIFT_KEY], { ctrl: true }) === '[["==",""]]', "left without right, Shift too: left still");
  }

  // Held keys are what they were when they went down.
  {
    const m = rig(four);                                // the ⎋ chord on [0, 1], on alpha2 as on the base
    down(m, CTRL_KEY, 600);                             // a Ctrl on the base layer's home row
    flags(m, { ctrl: true }, 800);
    m.read({ kind: "layers", ids: [ALPHA2] }, 900);  // where that position is another key
    down(m, CHORD[0], 1000);
    flags(m, { ctrl: false }, 1000);                    // masked, as the morph runs
    down(m, CHORD[1], 1000);
    const out = m.to(1400);
    check(out.board.layers[0] === "alpha2" && legends(out.board.pills) === '[["==","!="]]',
          `a Ctrl held across a layer change is still a Ctrl: ${out.board.layers} ${legends(out.board.pills)}`);
  }

  // What is not read, written down as it is: a Shift that only the report says (a sticky Shift, caps
  // word) and a hold-tap that has not turned into its hold. Neither is checked on a keyboard.
  {
    const m = rig(four);
    m.read({ kind: "layers", ids: [] }, 800);
    down(m, SHIFT_KEY, 850); flags(m, { shift: true }, 855); up(m, SHIFT_KEY, 900);
    chord(m, 1000);
    check(legends(m.to(1400).board.pills) === '[["(",")"]]', "a Shift only in the report is not read (limitation)");
  }
  {
    const m = rig(four);
    m.read({ kind: "layers", ids: [] }, 800);
    down(m, CTRL_KEY, 900); chord(m, 1000);
    check(legends(m.to(1400).board.pills) === '[["==","!="]]', "a hold-tap down counts as held, resolved or not (premise)");
  }

  // A report that trails the chord can only be one for a modifier held before it, not a key after it.
  for (const [what, key, flag] of [["Shift", SHIFT_KEY, "shift"], ["Ctrl", CTRL_KEY, "ctrl"]]) {
    const m = rig(four);
    m.read({ kind: "layers", ids: [] }, 900);
    chord(m, 1000);
    down(m, key, 1100); flags(m, { [flag]: true }, 1105);
    check(legends(m.to(1400).board.pills) === '[["(",")"]]', `a ${what} key that goes down after the chord is not a state it was struck in`);
  }

  // Without a report of what is typed, a chord that may read Ctrl waits for the reports; a chord that
  // cannot is drawn at once.
  {
    const wait = mutate => {
      const m = rig(mutate);
      m.read({ kind: "layers", ids: [] }, 900);
      chord(m, 1000);
      return [m.to(1299).board.pills.length, m.to(1301).board.pills.length];
    };
    check(eq(wait(four), [0, 1]), "a four-state chord is drawn when combo_decide_ms is up");
    check(eq(wait(k => { k.tap = "("; k.shifted = ")"; }), [1, 1]), "a chord without left is drawn at once");
  }
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

(function aLayerDropHeldBackIsAppliedByTheNextKey() {
  const k = session();
  k.read({ kind: "layers", ids: [ALPHA2] }, 900);
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
