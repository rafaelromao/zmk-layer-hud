#!/usr/bin/env node
/* zmk-layer-hud — the legends, typed: host/actions.py's fallback, the engine and the translator
 * agreeing with the drawing.
 *
 *   node hud/tests/engine_legends_test.js [--verbose]
 *
 * host/ways.py lists every way a keyboard types each legend of a fixture keymap -- the layer the
 * keyboard reports, the keys struck, the keys held -- and the key events that type it. This builds
 * the fixture's actions from its legends (host/actions.py --message), puts the engine on that
 * layer, strikes those keys on it as a keyboard would, decodes what it sends with hud/hidtext.js,
 * and wants the same characters: the end-to-end proof that a drawing with no keymap behind it types
 * what it draws. Needs python3 (PYTHON=... to choose one); skips without it.
 */
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const ZmkEngine = require("../engine.js");
const ZmkHidText = require("../hidtext.js");

const REPO = path.join(__dirname, "..", "..");
const FIXTURES = ["example-3x5.json", "romak-34.json", "diamond.json"];
const VERBOSE = process.argv.includes("--verbose");
const fail = [];
let checks = 0;

function python(args) {
  const tried = [process.env.PYTHON, path.join(REPO, ".venv/bin/python3"), "python3"].filter(Boolean);
  for (const py of tried) {
    try { return execFileSync(py, args, { encoding: "utf8", cwd: REPO, stdio: ["ignore", "pipe", "ignore"], maxBuffer: 1 << 28 }); } catch (e) { /* the next */ }
  }
  return null;
}

function typedBy(actions, msg, c) {
  const eng = ZmkEngine.create(actions, {});
  const dec = ZmkHidText.decoder({ altgr: !!msg.altgr });
  const keyDowns = [];
  const take = (events) => {
    for (const e of events) if (e.kind === "report") keyDowns.push(...dec.feed(e, e.t).filter((k) => k.type === "keyDown"));
  };
  let t = 0;
  take(eng.adopt(c.live, t));
  // Keys held for the way (a Shift held by hand) go down first and stay past any hold-tap's term.
  for (const pos of c.held_zmk || []) take(eng.down({ pos }, t));
  if ((c.held_zmk || []).length) { t += 600; take(eng.tick(t)); }
  if (c.combo) {
    // The chip for these keys that fires on the layer the keyboard is on (the highest live one).
    const want = [...c.zmk].sort((a, b) => a - b).join(",");
    const top = eng.state().top;
    const i = actions.combos.findIndex((x) => x.positions.join(",") === want && (!x.layers || x.layers.includes(top)));
    if (i < 0) return null;
    t += 10; take(eng.down({ combo: i }, t));
    t += 20; take(eng.up({ combo: i }, t));
  } else {
    for (const pos of c.zmk) { t += 10; take(eng.down({ pos }, t)); t += 20; take(eng.up({ pos }, t)); }
  }
  for (const pos of c.held_zmk || []) { t += 10; take(eng.up({ pos }, t)); }
  t += 1000;
  take(eng.tick(t));
  keyDowns.push(...dec.flush(t + 1000).filter((k) => k.type === "keyDown"));
  return keyDowns;
}

// The named keys a legend may spell as a word (uskeys.SPELLINGS): `Tab` on a typing layer is the
// Tab key to the touch keyboard, where ways.py, reading characters, spells it.
const namesOut = python(["-c", "import sys, json; sys.path.insert(0, 'host'); import uskeys; "
                         + "print(json.dumps(uskeys.SPELLINGS))"]);
const WORDS = namesOut ? JSON.parse(namesOut) : {};

let cases = 0, skipped = 0;
for (const fixture of FIXTURES) {
  const file = path.join(__dirname, "fixtures", fixture);
  const actionsOut = python([path.join(REPO, "host/actions.py"), "--message", file]);
  const casesOut = python([path.join(REPO, "host/ways.py"), "--cases", file]);
  if (actionsOut === null || casesOut === null) { console.log("engine_legends_test: no python3, skipping"); process.exit(0); }
  const msg = JSON.parse(fs.readFileSync(file, "utf8"));
  const actions = JSON.parse(actionsOut);
  const letters = (msg.layers[msg.base] || []).map((k) => k.tap).filter((t) => t && t.length === 1 && /\p{L}/u.test(t));
  const capitals = letters.length && letters.every((t) => t === t.toUpperCase());
  for (const line of casesOut.trim().split("\n").filter(Boolean)) {
    const c = JSON.parse(line);
    if (c.kind !== "case" || c.conditional || !c.events) { skipped++; continue; }
    cases++;
    const got = typedBy(actions, msg, c);
    let want = c.events.filter((e) => e.type === "keyDown").map((e) => e.chars);
    // A board drawn in capitals, as `keymap parse` draws a keymap: its Q key types q.
    if (capitals && !(c.held || []).length) want = want.map((ch) => (/^\p{Lu}$/u.test(ch) ? ch.toLowerCase() : ch));
    let gotChars = got && got.map((e) => e.chars);
    const word = WORDS[c.legend.toLowerCase().replace(/\s+/g, "")];
    if (word && got) { want = [word]; gotChars = got.map((e) => e.name); }
    checks++;
    if (!got || JSON.stringify(gotChars) !== JSON.stringify(want)) {
      fail.push(`${fixture} ${JSON.stringify(c.legend)} on ${c.layer}${c.combo ? " (combo)" : ""} keys ${c.zmk}${(c.held_zmk || []).length ? " holding " + c.held_zmk : ""}: typed ${JSON.stringify(gotChars)}, wants ${JSON.stringify(want)}`);
    } else if (VERBOSE) {
      console.log(`ok ${fixture} ${c.legend}`);
    }
  }
}

for (const f of fail.slice(0, 40)) console.log("FAIL " + f);
if (fail.length > 40) console.log(`... and ${fail.length - 40} more`);
console.log(`${cases} ways to type a legend, typed by the engine from the legends (${skipped} left out), ${checks} checks, ${fail.length} failures`);
process.exit(fail.length ? 1 : 0);
