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
