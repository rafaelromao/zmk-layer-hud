#!/usr/bin/env node
/* zmk-layer-hud — the HUD page's test suite.
 *
 *   node hud/tests/hud_test.js [--keymap FILE] [--verbose] [--max N] [--layer NAME]
 *
 * Runs hud/hud.js and hud/keys.js exactly as they ship (see hud/tests/dom.js) over a keymap
 * message, and sweeps it: every key on every layer it can be shown on, every combo on every layer
 * it is declared on, and every press that must NOT draw a combo. The cases come from the message
 * (hud/tests/cases.js), so a different keymap sweeps itself.
 *
 * Default keymap is the committed fixture. `--keymap hud/keymap.json` runs against a live dump of
 * your own board, which is the full-fidelity version of the same sweep.
 */
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { loadPage } = require("./dom.js");
const cases = require("./cases.js");

const REPO = path.join(__dirname, "..", "..");
const FIXTURE = path.join(__dirname, "fixtures", "diamond.json");

function parseArgs(argv) {
  const opt = { keymap: FIXTURE, verbose: false, max: 40, layer: null };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--keymap") opt.keymap = argv[++i];
    else if (a === "--verbose" || a === "-v") opt.verbose = true;
    else if (a === "--max") opt.max = Number(argv[++i]);
    else if (a === "--layer") opt.layer = argv[++i];
    else if (a === "--help" || a === "-h") { console.log(fs.readFileSync(__filename, "utf8").split("*/")[0]); process.exit(0); }
    else if (!a.startsWith("-")) opt.keymap = a;
  }
  return opt;
}

/* The page, driven the way the host drives it: positions in, DOM out. Every call the sweep makes
 * goes through window.hud — nothing reaches into the renderer's internals except state.keyEls,
 * which is the page's own handle on the board. */
function nodeDriver(page) {
  const held = new Set();
  const legend = e => (e ? (e.innerHTML || e.textContent) : "");
  return {
    async load(data) { page.hud.load(data); },
    async setLayers(ids) { page.hud.setLayers(ids); },
    async press(pos) { held.add(pos); page.hud.pressAt(pos); },
    async release(pos) { held.delete(pos); page.hud.releaseAt(pos); },
    async advance(ms) { page.clock.advance(ms); },

    /* Back to a clean page: let go of everything and run time past the longest thing hud.js
     * schedules (held_timeout_ms 5000), which also drains recentPos, the macro `recent` list and
     * the strip's idle fade. pending() is then the invariant — a timer still standing means the
     * page leaked one, and the next case would inherit it. */
    async reset(ids, isBase) {
      for (const pos of [...held]) { held.delete(pos); page.hud.releaseAt(pos); }
      page.clock.advance(6000);
      if (ids !== null && ids !== undefined) page.hud.setLayers(isBase ? [] : [ids]);
    },
    pending: () => page.clock.pending(),

    async legends() {
      return page.hud.state.keyEls.map(e => ({
        tap: legend(e.querySelector(".tap")),
        hold: legend(e.querySelector(".hold")),
        shifted: legend(e.querySelector(".shifted")),
      }));
    },
    async lit() {
      const out = [];
      page.hud.state.keyEls.forEach((e, i) => { if (e.classList.contains("pressed")) out.push(i); });
      return out;
    },
    async pills() {
      return page.board.querySelectorAll(".combo-pill").map(p => ({
        tap: legend(p.querySelector(".combo-tap")),
        sub: legend(p.querySelector(".combo-sub")),
      }));
    },
  };
}

// The shim must define every id index.html does, or hud.js renders into nothing and the whole
// sweep passes for the wrong reason.
function checkPageIds(page) {
  const html = fs.readFileSync(path.join(REPO, "hud", "index.html"), "utf8");
  const missing = [...html.matchAll(/id="([^"]+)"/g)].map(m => m[1]).filter(id => !page.document.getElementById(id));
  return missing;
}

async function main() {
  const opt = parseArgs(process.argv.slice(2));
  if (!fs.existsSync(opt.keymap)) {
    console.error(`hud_test: no keymap at ${opt.keymap}`);
    process.exit(2);
  }
  const data = JSON.parse(fs.readFileSync(opt.keymap, "utf8"));
  const page = loadPage();

  const missing = checkPageIds(page);
  if (missing.length) {
    console.error(`hud_test: hud/tests/dom.js is missing ids index.html defines: ${missing.join(", ")}`);
    process.exit(1);
  }

  const started = Date.now();
  const r = await cases.sweep(nodeDriver(page), data, opt);
  const ms = Date.now() - started;

  const byCheck = new Map();
  for (const f of r.fail) byCheck.set(f.check, (byCheck.get(f.check) || 0) + 1);

  if (r.notes.length && opt.verbose) {
    console.log("notes:");
    for (const n of r.notes) console.log("  " + n);
    console.log("");
  }
  if (r.contested.length && opt.verbose) {
    console.log(`${r.contested.length} position sets are declared on more than one live layer; with two held, the page shows:`);
    for (const c of r.contested) {
      console.log(`  [${c.positions}] holding ${c.holding.join(" + ")} -> ${JSON.stringify(c.shows)}  (candidates: ${c.candidates.map(t => JSON.stringify(t)).join(", ")})`);
    }
    console.log("");
  }

  let shown = 0;
  for (const f of r.fail) {
    if (!opt.verbose && shown >= opt.max) { console.log(`  ... and ${r.fail.length - shown} more`); break; }
    console.log(`FAIL ${f.check} ${f.where}: want ${JSON.stringify(f.expected)} got ${JSON.stringify(f.actual)}`);
    shown++;
  }
  if (r.fail.length) {
    console.log("");
    console.log("by check: " + [...byCheck].sort((a, b) => b[1] - a[1]).map(([k, n]) => `${k} ${n}`).join(", "));
  }
  console.log(`${path.relative(REPO, opt.keymap)}: ${r.live.length} of ${r.live.length + r.unreachable.length} drawer layers reachable, ` +
              `${data.combos.length} combos, ${r.notes.length} notes`);
  console.log(`${r.checked} checks, ${r.fail.length} failures (${ms} ms)`);
  process.exit(r.fail.length ? 1 : 0);
}

main().catch(e => { console.error(e); process.exit(2); });
