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
const crypto = require("node:crypto");
const { loadPage } = require("./dom.js");
const cases = require("./cases.js");

const REPO = path.join(__dirname, "..", "..");
const FIXTURE = path.join(__dirname, "fixtures", "diamond.json");

function parseArgs(argv) {
  const opt = { keymap: FIXTURE, verbose: false, max: 40, layer: null, signature: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--keymap") opt.keymap = argv[++i];
    else if (a === "--verbose" || a === "-v") opt.verbose = true;
    else if (a === "--max") opt.max = Number(argv[++i]);
    else if (a === "--layer") opt.layer = argv[++i];
    else if (a === "--signature") opt.signature = true;
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
  // Markup for a glyph, plain text otherwise — the same read the browser runner makes, so the
  // two can be compared without one of them escaping < and & and the other not.
  const legend = e => (!e ? "" : (e.children.length ? e.innerHTML : e.textContent));
  return {
    async load(data) { page.hud.load(data); },
    async setLayers(ids) { page.hud.setLayers(ids); },
    async press(pos) { held.add(pos); page.hud.pressAt(pos); },
    async release(pos) { held.delete(pos); page.hud.releaseAt(pos); },
    async advance(ms) { page.clock.advance(ms); },
    // Everything the page has counted (hud.js ledger), for the sweep's deltas.
    async tally() { return page.hud.stats.local(); },

    /* Back to a clean page: let go of everything and run time past the longest thing hud.js
     * schedules (held_timeout_ms 5000), which also drains recentPos, the macro `recent` list and
     * the strip's idle fade. pending() is then the invariant — a timer still standing means the
     * page leaked one, and the next case would inherit it. */
    async reset(ids, isBase) {
      for (const pos of [...held]) { held.delete(pos); page.hud.releaseAt(pos); }
      page.clock.advance(6000);
      // Say the layers went away too. A real keyboard reports [] when the thumb comes up; without
      // it the page still believes the previous case's layer is held, and setLayers holds the next
      // report back to keep a flash under the right legends (hud.js setLayers).
      page.hud.setLayers([]);
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
    async activators() {
      const out = [];
      page.hud.state.keyEls.forEach((e, i) => { if (e.classList.contains("activator")) out.push(i); });
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

/* A rehearsal's uinput character has no matching firmware position report. The feed may still
 * send a synthetic position for its sticky thumb, so prove that the subsequent synthetic character
 * highlights its key while an unmarked hardware character remains suppressed by the freshness
 * guard. This exercises hud.js itself, not a reimplementation of its timers or resolver. */
function checkSyntheticFreshness(page, data) {
  const alpha2 = Object.entries(data.zmk_layers || {})
    .find(([, layer]) => layer.drawer === "alpha2");
  const thumb = Object.entries(data.positions || {})
    .find(([, index]) => Number(index) === 22);
  const q = (data.layers.alpha2 || []).findIndex(key => key.tap === "q");
  if (!alpha2 || !thumb || q < 0) return ["fixture lacks Alpha 2 q or its sticky thumb position"];

  page.hud.load(data);
  page.hud.setLayers([Number(alpha2[0])]);
  page.hud.pressAt(Number(thumb[0]));
  page.hud.releaseAt(Number(thumb[0]));
  page.clock.advance(Number(data.hud.release_ms || 60) + 1);
  page.hud.key({ type: "keyDown", name: "q", chars: "q", code: 16, flags: {}, synthetic: true });
  const lit = () => page.hud.state.keyEls.flatMap((el, idx) =>
    el.classList.contains("pressed") ? [idx] : []);
  if (lit().join() !== String(q)) {
    return [`synthetic q after a thumb position should light ${q}; lit [${lit()}]`];
  }

  page.clock.advance(Number(data.hud.press_ms || 320) + 1);
  page.hud.key({ type: "keyDown", name: "q", chars: "q", code: 16, flags: {} });
  if (lit().length) {
    return [`unmarked hardware q inside the freshness window should remain suppressed; lit [${lit()}]`];
  }
  return [];
}

/* Framed by another page (index.html?embed, the landing page's demo), the page around the frame
 * has the keyboard. The dev keydown listener, which takes every key but F5 and ⌘ chords, must not
 * be installed there: it would swallow Tab, which is the way out of the frame. On a plain http page
 * it still is, since that is how the page is tried in a browser. A Tab keydown shows both. */
function checkEmbed() {
  const fails = [];
  for (const embed of [false, true]) {
    const page = loadPage({ protocol: "http:", search: embed ? "?embed" : "" });
    let typed = 0, prevented = 0;
    page.hud.key = () => { typed++; };   // the listener calls hud.key on this same object
    page.dispatch("keydown", { key: "Tab", metaKey: false, preventDefault: () => { prevented++; } });
    const where = embed ? "?embed" : "a plain http page";
    const want = embed ? 0 : 1;
    if (typed !== want) fails.push(`${where}: Tab reached hud.key ${typed} times, want ${want}`);
    if (prevented !== want) fails.push(`${where}: Tab was prevented ${prevented} times, want ${want}`);
    if (page.document.documentElement.classList.contains("embed") !== embed) {
      fails.push(`${where}: <html> ${embed ? "lacks" : "has"} the embed class`);
    }
  }
  return fails;
}

/* ?ws=, ?keymap=, ?script= and ?timeline= say what the page connects to and fetches. The hosts
 * load the page from disk and the demo from 127.0.0.1; the published copy on GitHub Pages must
 * honour none of them, or a link could point it at any server and have it draw that server's
 * glyphs as markup. */
function checkLocalOnly() {
  const fails = [];
  const query = "?keymap=x.json&ws=ws://127.0.0.1:8766/t&demo=0&script=s.json";
  const cases = [
    { opts: { protocol: "https:", hostname: "someone.github.io", search: query, quiet: true }, fetched: 0, sockets: 0, where: "github.io" },
    { opts: { protocol: "http:", hostname: "example.com", search: query, quiet: true }, fetched: 0, sockets: 0, where: "example.com" },
    { opts: { protocol: "file:", search: query }, fetched: 2, sockets: 1, where: "file:" },
    { opts: { protocol: "http:", hostname: "localhost", search: query }, fetched: 2, sockets: 1, where: "localhost" },
    { opts: { protocol: "http:", hostname: "127.0.0.1", search: query }, fetched: 2, sockets: 1, where: "127.0.0.1" },
  ];
  for (const c of cases) {
    const page = loadPage(c.opts);
    if (page.fetched.length !== c.fetched) fails.push(`${c.where}: fetched ${JSON.stringify(page.fetched)}, want ${c.fetched} requests`);
    if (page.sockets.length !== c.sockets) fails.push(`${c.where}: opened ${JSON.stringify(page.sockets)}, want ${c.sockets} sockets`);
  }
  return fails;
}

/* A glyph is markup the page draws, and the page sees every keystroke. One that is anything but a
 * drawing is not drawn: the legend shows the glyph's text spelling instead. The rules are
 * host/keymap.py's (glyph_problem); here they are applied to what reaches the page. The published
 * board's own glyphs, all of them real icons, must all draw. */
function checkGlyphs() {
  const fails = [];
  const hostile = [
    '<svg onload="x()"><path d="M0 0"/></svg>',
    "<svg><script>x()</script></svg>",
    "<svg><foreignObject><div>x</div></foreignObject></svg>",
    '<svg><use href="https://e.example/x.svg#a"/></svg>',
    '<svg><a href="javascript:1"><path d="M0 0"/></a></svg>',
    '<svg><image href="https://e.example/x"/></svg>',
    "<svg><style>path{fill:red}</style></svg>",
    '<svg style="background:url(x)"><path d="M0 0"/></svg>',
    '<!DOCTYPE svg [<!ENTITY x "y">]><svg>&x;</svg>',
    "<svg/onload=x()>",
    '<svg title=">" onload="x()"></svg>',
    "<svg><!--><script>x()</script>--></svg>",
    "<svg><![CDATA[x]]></svg>",
    "<div>x</div>",
    "x<svg></svg>",
    "<svg></svg>x",
    "<svg></svg><svg onload=x()></svg>",
  ];
  const place = (data, layerName) => {
    // Which ZMK layer ids put `layerName` on top: none for the base layer.
    if (layerName === data.base) return [];
    const id = Object.keys(data.zmk_layers || {}).find(i => data.zmk_layers[i].drawer === layerName || data.zmk_layers[i].name === layerName);
    return id === undefined ? null : [Number(id)];
  };
  const tapOf = (page, idx) => page.hud.state.keyEls[idx].querySelector(".tap");

  // A key with a glyph on the fixture's base layer, to try each hostile glyph on.
  const data = JSON.parse(fs.readFileSync(FIXTURE, "utf8"));
  const baseKeys = data.layers[data.base] || [];
  const idx = baseKeys.findIndex(k => k.glyph && data.glyphs[k.glyph]);
  if (idx < 0) return ["the fixture's base layer has no key with a glyph"];
  for (const svg of hostile) {
    const d = JSON.parse(JSON.stringify(data));
    d.glyphs[baseKeys[idx].glyph] = svg;
    const page = loadPage();
    page.hud.load(d);
    page.hud.setLayers([]);
    const tap = tapOf(page, idx);
    if (tap.children.length !== 0) fails.push(`drew ${svg}`);
    else if (!tap.textContent) fails.push(`${svg}: refused, but the legend shows nothing in its place`);
  }

  // The published board: every glyph on every layer draws.
  const board = JSON.parse(fs.readFileSync(path.join(REPO, "docs", "boards", "diamond.json"), "utf8"));
  const page = loadPage();
  page.hud.load(board);
  let drawn = 0;
  for (const layerName of board.layer_order) {
    const ids = place(board, layerName);
    if (ids === null) continue;
    page.hud.setLayers(ids);
    (board.layers[layerName] || []).forEach((k, i) => {
      if (!k.glyph || !board.glyphs[k.glyph] || k.type === "trans") return;
      const tap = tapOf(page, i);
      if (tap.children.length !== 1) fails.push(`${layerName} key ${i}: ${k.glyph} did not draw`);
      else drawn++;
    });
  }
  if (drawn < 20) fails.push(`only ${drawn} glyphs of the published board were drawn; the check proves little`);
  return fails;
}

/* A board drawn in capitals, the way `keymap parse` draws letters (the committed 3x5 sample): the
 * page places typing sent in by itself, and a lowercase letter is found on its capital's key -- the
 * legend is the keycap. h and H light the same key. */
function checkCapitals() {
  const data = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", "example-3x5.json"), "utf8"));
  const idx = (data.layers[data.base] || []).findIndex(k => k.tap === "H");
  if (idx < 0) return ["the 3x5 fixture has no H on its base layer"];
  const page = loadPage();
  page.hud.load(data);
  page.hud.setLayers([]);
  const lit = () => page.hud.state.keyEls.flatMap((el, i) => (el.classList.contains("pressed") ? [i] : []));
  const fails = [];
  for (const ch of ["h", "H"]) {
    page.hud.key({ type: "keyDown", name: ch, chars: ch, flags: { shift: ch === "H" }, repeat: false, combos: false });
    if (lit().join() !== String(idx)) fails.push(`typed-in ${ch} should light the H key (${idx}); lit [${lit()}]`);
    page.clock.advance(6000);
  }
  return fails;
}

/* A modifier rings the key that turned it on, not every key that carries it: the home-row Shift
 * held, or a Shift whose report came before its position. With no positions from the firmware
 * there is no telling which, and every key carrying it is ringed. */
function checkModRings(data) {
  const carries = k => !!k && ((k.hold && k.hold.includes("⇧")) || k.tap === "⇧");
  const idxs = (data.layers[data.base] || []).flatMap((k, i) => (carries(k) ? [i] : []));
  const posOf = idx => Object.entries(data.positions || {}).find(([, i]) => Number(i) === idx);
  const [a, b] = idxs.filter(posOf);
  if (a === undefined || b === undefined) return ["the fixture has fewer than two positioned keys carrying ⇧ on its base"];
  const ringed = page => page.hud.state.keyEls.flatMap((el, i) => (el.classList.contains("mod") ? [i] : []));
  const flags = (page, shift) => page.hud.key({ type: "flagsChanged", flags: shift ? { shift: true } : {} });
  const fails = [];

  let page = loadPage();
  page.hud.load(data);
  page.hud.setLayers([]);
  page.hud.pressAt(Number(posOf(a)[0]));
  flags(page, true);
  if (ringed(page).join() !== String(a)) fails.push(`Shift held on ${a} should ring it alone; ringed [${ringed(page)}]`);
  page.hud.releaseAt(Number(posOf(a)[0]));
  flags(page, false);
  if (ringed(page).length) fails.push(`Shift let go should ring nothing; ringed [${ringed(page)}]`);
  flags(page, true);                                   // a sticky Shift: on once its key was tapped and let go
  if (ringed(page).join() !== String(a)) fails.push(`Shift just after ${a} was tapped should ring it; ringed [${ringed(page)}]`);
  flags(page, false);
  page.clock.advance(Number(data.hud.activator_ms || 400) + 1);   // past a tap that could have made it
  flags(page, true);                                   // the report first, its key's position after
  if (ringed(page).length) fails.push(`Shift before its key should ring nothing yet; ringed [${ringed(page)}]`);
  page.hud.pressAt(Number(posOf(b)[0]));
  if (ringed(page).join() !== String(b)) fails.push(`and then ${b}, the key that came; ringed [${ringed(page)}]`);

  page = loadPage();                                   // no positions at all: every carrier
  page.hud.load(data);
  page.hud.setLayers([]);
  flags(page, true);
  if (ringed(page).join() !== idxs.join()) fails.push(`without positions every ⇧ key should ring [${idxs}]; ringed [${ringed(page)}]`);
  return fails;
}

/* A layer picked from the stats' layer tile is drawn until the next keystroke, from the firmware's
 * positions or the keyboard's reports alike; then the board follows the keyboard again. */
function checkLayerPick(data) {
  const other = (data.layer_order || []).find(n => n !== data.base);
  const pos = Object.keys(data.positions || {})[0];
  if (!other || pos === undefined) return ["the fixture needs a second layer and a key position"];
  const page = loadPage();
  page.hud.load(data);
  page.hud.setLayers([]);
  const banner = () => page.document.getElementById("layerName").textContent;
  const before = banner();
  const fails = [];
  page.hud.pickLayer(other);
  if (page.hud.state.pick !== other || banner() === before) fails.push(`picking ${other} should draw it; banner ${banner()}`);
  page.hud.pressAt(Number(pos));
  if (page.hud.state.pick !== null || banner() !== before) fails.push(`a key down should hand the board back; banner ${banner()}`);
  page.hud.releaseAt(Number(pos));
  page.clock.advance(6000);
  page.hud.pickLayer(other);
  page.hud.key({ type: "keyDown", name: "a", chars: "a", code: 0, flags: {}, synthetic: true });
  if (page.hud.state.pick !== null) fails.push("a key typed should hand the board back too");
  page.hud.pickLayer("no such layer");
  if (page.hud.state.pick !== null) fails.push("a layer the keymap does not have is not picked");
  // The tile's combobox: auto, then every drawn layer; choosing one picks it, and it shows the pick.
  const select = page.document.querySelector(".layer-pick");
  if (!select) return fails.concat(["the layer tile has no combobox"]);
  const values = select.childNodes.map(o => o.value);
  if (JSON.stringify(values) !== JSON.stringify([""].concat(data.layer_order || [])))
    fails.push(`the combobox lists ${JSON.stringify(values)}`);
  select.value = other;
  for (const fn of select.listeners.get("change")) fn({ type: "change" });
  if (page.hud.state.pick !== other) fails.push("choosing a layer in the combobox should pick it");
  page.hud.pickLayer(null);
  if (select.value !== "") fails.push(`after auto the combobox should say auto, not ${select.value}`);
  return fails;
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

  const freshnessFailures = checkSyntheticFreshness(page, data);
  if (freshnessFailures.length) {
    for (const failure of freshnessFailures) console.error(`FAIL synthetic-freshness: ${failure}`);
    process.exit(1);
  }

  const embedFailures = checkEmbed();
  if (embedFailures.length) {
    for (const failure of embedFailures) console.error(`FAIL embed: ${failure}`);
    process.exit(1);
  }

  const localFailures = checkLocalOnly();
  if (localFailures.length) {
    for (const failure of localFailures) console.error(`FAIL local-only: ${failure}`);
    process.exit(1);
  }

  const glyphFailures = checkGlyphs();
  if (glyphFailures.length) {
    for (const failure of glyphFailures) console.error(`FAIL glyphs: ${failure}`);
    process.exit(1);
  }

  const pickFailures = checkLayerPick(data);
  if (pickFailures.length) {
    for (const failure of pickFailures) console.error(`FAIL layer-pick: ${failure}`);
    process.exit(1);
  }

  const modFailures = checkModRings(data);
  if (modFailures.length) {
    for (const failure of modFailures) console.error(`FAIL mod-rings: ${failure}`);
    process.exit(1);
  }

  const capitalFailures = checkCapitals();
  if (capitalFailures.length) {
    for (const failure of capitalFailures) console.error(`FAIL capitals: ${failure}`);
    process.exit(1);
  }

  const started = Date.now();
  const r = await cases.sweep(nodeDriver(page), data, opt);
  const ms = Date.now() - started;

  // The same sweep runs in a real browser (hud/tests/browser.js). Comparing the two by this
  // digest is what says the DOM in hud/tests/dom.js is telling the truth.
  const signature = r.fail.map(f => `${f.check}|${f.where}|${f.expected}|${f.actual}`).sort();
  if (opt.signature) {
    console.log(`${r.checked} checks, ${r.fail.length} failures, signature ` +
                crypto.createHash("sha256").update(signature.join("\n")).digest("hex").slice(0, 32));
    process.exit(0);
  }

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
