#!/usr/bin/env node
/* zmk-layer-hud — the typed-keys strip against what was actually typed.
 *
 *   node hud/tests/strip_test.js [--keymap FILE] [--verbose] [--max N]
 *
 * The board is drawn from the keymap; the strip below it is drawn from the HID reports the
 * keyboard sent. This checks they agree: for every legend in the keymap that a US-layout keyboard
 * could type, host/uskeys.py sends what a keyboard would send, hudfeed's own decoder turns it back
 * into key events, and keys.js renders a chip — which must read as the legend.
 *
 * That is where the encoding cases live: an accented letter is a dead key and a letter, not one
 * keypress; a character on the Option layer arrives with a modifier held; a named key's glyph is
 * spelled by three separate tables. Needs python3 (PYTHON=... to choose one); skips without it.
 */
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { loadPage } = require("./dom.js");

const REPO = path.join(__dirname, "..", "..");
const FIXTURE = path.join(__dirname, "fixtures", "diamond.json");
const IDLE_CLEAR = 2400;         // keys.js IDLE_MS (1800) plus its fade, so each legend starts clean

function pythons() {
  const chosen = process.env.PYTHON;
  return (chosen ? [chosen] : []).concat([path.join(REPO, ".venv/bin/python3"), "python3"]);
}

// host/uskeys.py is the only thing that knows how a legend is typed; it inverts hudfeed's own
// tables, so this cannot drift from the decoder it exercises.
function corpus(keymap) {
  let lastErr = null;
  for (const py of pythons()) {
    try {
      const legends = execFileSync(py, [path.join(REPO, "host/uskeys.py"), "--corpus", keymap], { encoding: "utf8" });
      const out = execFileSync(py, [path.join(REPO, "host/uskeys.py"), "--events"], { input: legends, encoding: "utf8" });
      return out.trim().split("\n").filter(Boolean).map(l => JSON.parse(l));
    } catch (e) { lastErr = e; }
  }
  return { error: lastErr };
}

function main() {
  const argv = process.argv.slice(2);
  const opt = { keymap: FIXTURE, verbose: argv.includes("--verbose") || argv.includes("-v"), max: 40 };
  if (argv.includes("--keymap")) opt.keymap = argv[argv.indexOf("--keymap") + 1];
  if (argv.includes("--max")) opt.max = Number(argv[argv.indexOf("--max") + 1]);

  const got = corpus(opt.keymap);
  if (got.error) {
    console.log("strip_test: skipped, the corpus needs python3 (set PYTHON=/path/to/python3)");
    process.exit(0);
  }

  const page = loadPage();
  page.hud.load(JSON.parse(fs.readFileSync(opt.keymap, "utf8")));
  const strip = page.document.getElementById("keys");

  const fail = [];
  let checked = 0;
  for (const { legend, events, accepts } of got) {
    checked++;
    page.clock.advance(IDLE_CLEAR);                 // the previous chip fades and `current` clears
    if (strip.children.length) fail.push({ legend, expected: "a clean strip", actual: `${strip.children.length} chips left over` });
    for (const ev of events || []) page.keys.key(ev);
    const chips = strip.children.map(c => c.textContent);
    if (chips.length !== 1) {
      fail.push({ legend, expected: `one chip reading ${JSON.stringify(legend)}`, actual: JSON.stringify(chips) });
    } else if (!(accepts || [legend]).includes(chips[0])) {
      fail.push({ legend, expected: legend, actual: chips[0] });
    }
  }

  // A usage the feed has no name for (usage9b): the chip reads the legend of the key the keyboard
  // says it pressed (hud.js stripEvent), and with no such press, the usage as before.
  {
    const data = JSON.parse(fs.readFileSync(opt.keymap, "utf8"));
    const base = data.layers[data.base] || [];
    const pos = Object.keys(data.positions || {}).find(p => {
      const k = base[data.positions[p]];
      return k && k.type !== "trans" && k.tap;
    });
    if (pos !== undefined) {
      const legend = base[data.positions[pos]].tap;
      const unnamed = { type: "keyDown", name: "usage9b", chars: "", code: 0x9b, flags: {} };
      page.hud.setLayers([]);
      page.clock.advance(IDLE_CLEAR);
      checked++;
      page.hud.pressAt(Number(pos));
      page.hud.key(unnamed);
      page.hud.releaseAt(Number(pos));
      let chips = strip.children.map(c => c.textContent);
      if (chips.join("|") !== legend) fail.push({ legend: "usage9b", expected: legend, actual: JSON.stringify(chips) });
      page.clock.advance(IDLE_CLEAR + 1000);
      checked++;
      page.hud.key(unnamed);
      chips = strip.children.map(c => c.textContent);
      if (chips.join("|") !== "USAGE9B") fail.push({ legend: "usage9b, no press", expected: "USAGE9B", actual: JSON.stringify(chips) });
    }
    // ...and when the keys pressed are a combo, the keystroke is the combo's: the chip reads its
    // legend, as the pill does (l+o+u types cancel on the Diamond), not its last key's.
    const zmkOf = {};
    for (const [p, idx] of Object.entries(data.positions || {})) zmkOf[idx] = Number(p);
    const combo = (data.combos || []).find(c => c.layers.includes(data.base) && c.key.tap && c.key.tap.length > 1 &&
                                                c.positions.every(i => zmkOf[i] !== undefined));
    if (combo) {
      const unnamed = { type: "keyDown", name: "usage9d", chars: "", code: 0x9d, flags: {} };
      page.clock.advance(IDLE_CLEAR + 1000);
      checked++;
      for (const i of combo.positions) page.hud.pressAt(zmkOf[i]);
      page.hud.key(unnamed);
      for (const i of combo.positions) page.hud.releaseAt(zmkOf[i]);
      const chips = strip.children.map(c => c.textContent);
      if (chips.join("|") !== combo.key.tap) fail.push({ legend: "usage9d, a combo", expected: combo.key.tap, actual: JSON.stringify(chips) });
    }
  }

  let shown = 0;
  for (const f of fail) {
    if (!opt.verbose && shown >= opt.max) { console.log(`  ... and ${fail.length - shown} more`); break; }
    console.log(`FAIL strip ${JSON.stringify(f.legend)}: want ${JSON.stringify(f.expected)} got ${JSON.stringify(f.actual)}`);
    shown++;
  }
  console.log(`${checked} legends typed, ${fail.length} failures`);
  process.exit(fail.length ? 1 : 0);
}

main();
