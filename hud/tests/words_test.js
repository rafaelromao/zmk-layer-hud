#!/usr/bin/env node
/* zmk-layer-hud — every way to type every legend, down every channel it can arrive by.
 *
 *   node hud/tests/words_test.js [--keymap FILE] [--verbose] [--max N]
 *
 * A keyboard running the module reaches the page two ways at once: its own channel says which
 * layers are up and which keys went down, and its HID reports say what was typed. Either can be
 * missing -- no Input Monitoring grant, a position channel that dropped, positions not fresh, a
 * keyboard without the module -- and the two travel on separate threads, so they arrive in either
 * order. The board and the strip have to be right for every one of those, for every way the
 * keymap has of typing each character: its key, a combo, another layer, a held Shift.
 *
 * The ways come from host/ways.py, which states ZMK's rules itself rather than asking hud.js (see
 * there for why). The reports are hudfeed's own decoder run backwards (host/uskeys.py). Each way is
 * replayed on a fresh page per channel; each word is typed on one page, key after key, so a way is
 * also tested next to its neighbours. Needs python3 (PYTHON=... to choose one); skips without it.
 */
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { loadPage } = require("./dom.js");

const REPO = path.join(__dirname, "..", "..");
const FIXTURE = path.join(__dirname, "fixtures", "diamond.json");
const CHORD_MS = 5;       // between the keys of a chord: well inside any combo term
const HELD_LEAD_MS = 120; // a Shift goes down this long before the letter: outside the combo term
const HOLD_MS = 90;       // how long a key stays down
const REPORT_MS = 5;      // between the reports of one legend (a macro types back to back)
const LAYER_LEAD_MS = 100; // a layer that is up came up before the key: with its activator, earlier
const POKE_GAP_MS = 90;    // host/hudpoke.py --gap-ms: how far apart it sends the characters it types

function pythons() {
  const chosen = process.env.PYTHON;
  return (chosen ? [chosen] : []).concat([path.join(REPO, ".venv/bin/python3"), "python3"]);
}

function generate(keymap) {
  let lastErr = null;
  for (const py of pythons()) {
    try {
      const out = execFileSync(py, [path.join(REPO, "host/ways.py"), "--cases", keymap],
                               { encoding: "utf8", maxBuffer: 64 << 20 });
      return out.trim().split("\n").filter(Boolean).map(l => JSON.parse(l));
    } catch (e) { lastErr = e; }
  }
  return { error: lastErr };
}

// ---------- reading the page ----------

// A legend is written as text or as a glyph's markup (hud.js setLegend); the shim keeps one or
// the other, the way a browser's element holds text or children.
const legendOf = el => el.textContent || el.innerHTML;
const glyphLegend = (km, text, glyph) =>
  glyph && km.glyphs && km.glyphs[glyph] ? '<span class="glyph">' + km.glyphs[glyph] + "</span>" : (text || "");

/* `before`: what was already on screen when this keystroke began. A combo's pill stays for
 * combo_pill_ms (a second) by design, and a key lit from a report alone stays for press_ms, so in
 * a word the previous keystroke's are often still fading while the next key goes down; only what
 * this keystroke drew is its own. */
const litKeys = p => p.document.querySelectorAll(".key")
  .map((e, i) => (e.classList.contains("pressed") ? i : -1)).filter(i => i >= 0);
function observe(p, before) {
  const lit = litKeys(p);
  const fresh = before ? lit.filter(i => !before.lit.has(i)) : lit;
  const pills = p.document.querySelectorAll(".combo-tap").filter(e => !before || !before.pills.has(e)).map(legendOf);
  const strip = p.document.getElementById("keys").children.map(c => c.textContent);
  return { lit, fresh, pills, strip };
}
const snapshot = p => ({ pills: new Set(p.document.querySelectorAll(".combo-tap")), lit: new Set(litKeys(p)) });

// ---------- replaying a way ----------

function press(p, c) {
  for (const pos of c.held_zmk) { p.hud.pressAt(pos); p.clock.advance(HELD_LEAD_MS); }
  c.zmk.forEach((pos, i) => { if (i) p.clock.advance(CHORD_MS); p.hud.pressAt(pos); });
}
function release(p, c) {
  p.clock.advance(HOLD_MS);
  c.zmk.forEach((pos, i) => { if (i) p.clock.advance(CHORD_MS); p.hud.releaseAt(pos); });
  for (const pos of c.held_zmk) p.hud.releaseAt(pos);
}
function report(p, c) {
  c.events.forEach((ev, i) => { if (i) p.clock.advance(REPORT_MS); p.hud.key(ev); });
}

/* What each channel delivers, in the order it delivers it. `look` is when the screen is read:
 * while the keys are down, or just after they came up (release_ms has not run out yet). */
const CHANNELS = [
  { name: "positions + reports, report on press", layers: true, positions: true, reports: true,
    run(p, c, b) { press(p, c); report(p, c); const seen = observe(p, b); release(p, c); return seen; } },
  { name: "positions + reports, report on release", layers: true, positions: true, reports: true,
    run(p, c, b) { press(p, c); release(p, c); report(p, c); return observe(p, b); } },
  { name: "report before positions", layers: true, positions: true, reports: true,
    run(p, c, b) { report(p, c); press(p, c); const seen = observe(p, b); release(p, c); return seen; } },
  { name: "reports only", layers: true, positions: false, reports: true,
    run(p, c, b) { report(p, c); return observe(p, b); } },
  { name: "positions only", layers: true, positions: true, reports: false,
    run(p, c, b) { press(p, c); const seen = observe(p, b); release(p, c); return seen; } },
  { name: "reports, no layers", layers: false, positions: false, reports: true,
    run(p, c, b) { report(p, c); return observe(p, b); } },
];

/* Typing sent in -- `zmk-layer-hud poke --type`, a WebSocket client -- is a character and nothing
 * else: no positions, no layers of its own, and a word on whether combos are how it is typed
 * (`combos`, false unless the sender says; hudfeed stamps the default). Off, a character is drawn
 * by a way that is not a combo wherever the keymap has one (z: Alpha 2's key), and by its combo
 * only when nothing else types it. On, the layers already up come first, combos and all (z: the
 * r+a chord). The keyboard may be connected and holding the page on its base, or not there. */
const sendIn = combos => (p, c, b) => {
  c.events.forEach((ev, i) => { if (i) p.clock.advance(REPORT_MS); p.hud.key({ ...ev, combos }); });
  return observe(p, b);
};
const keysOnly = c => { const k = c.anywhere.filter(a => a.length === 1); return k.length ? k : c.anywhere; };
const baseFirst = c => (c.on_base.length ? c.on_base : c.anywhere);
const SENT = [
  { name: "typed in, combos off, keyboard on its base", live: [], layers: true, positions: false, reports: true, run: sendIn(false), alts: keysOnly },
  { name: "typed in, combos on, keyboard on its base", live: [], layers: true, positions: false, reports: true, run: sendIn(true), alts: baseFirst },
  { name: "typed in, combos off, no keyboard", live: null, layers: false, positions: false, reports: true, run: sendIn(false), alts: keysOnly },
  { name: "typed in, combos on, no keyboard", live: null, layers: false, positions: false, reports: true, run: sendIn(true), alts: baseFirst },
];

// ---------- what must be on screen ----------

const list = xs => "[" + [...xs].sort((a, b) => a - b).join(",") + "]";
const sameSet = (a, b) => a.length === b.size && a.every(x => b.has(x));
// A glyph's markup is its id, for reading: the whole SVG says nothing a failure line needs.
const show = x => JSON.stringify(x).replace(/<span class=\\"glyph\\"><svg[^>]*id=\\"([^"\\]+)\\"[\s\S]*?<\/span>/g, "⟨$1⟩");

/* `also`: ways that are right answers besides this case's own. A report alone cannot say whether
 * it belongs to the key before a layer went away or to the one after -- the drop and the report
 * travel on separate channels -- so while a drop may still be in flight, either stack will do. */
function expected(km, c, ch, activators, also) {
  const problems = [];
  return (seen, strip) => {
    problems.length = 0;
    if (ch.positions) {
      // The keyboard said which keys went down: those, and nothing else.
      const want = new Set([...c.keys, ...c.held]);
      if (!sameSet(seen.lit, want)) problems.push(`lit ${list(seen.lit)}, struck ${list(want)}`);
      if (c.combo) {
        const pill = glyphLegend(km, c.pill.tap, c.pill.glyph);
        if (seen.pills.length !== 1 || seen.pills[0] !== pill)
          problems.push(`pills ${show(seen.pills)}, want [${show(pill)}]`);
      } else if (seen.pills.length) {
        problems.push(`pills ${show(seen.pills)} for a single key`);
      }
    } else {
      // From the character alone: any way this stack has of typing it is right -- anywhere at all
      // without layers -- plus the keys that explain how the layer came up.
      // Longest first: a chord that explains every lit key beats one of its own keys plus an
      // activator that happens to be among them.
      const alts = (ch.alts ? ch.alts(c) : ch.layers ? c.alternatives : c.anywhere).concat(also || []).slice().sort((a, b) => b.length - a.length);
      const extra = new Set([...c.held, ...activators]);
      const fits = a => a.every(i => seen.lit.includes(i)) && seen.fresh.every(i => a.includes(i) || extra.has(i));
      // It has to be this keystroke's: keys still lit from the one before explain nothing about
      // it -- unless the same keys were struck again, which lights nothing new at all.
      const match = alts.find(a => fits(a) && a.some(i => seen.fresh.includes(i))) ||
                    (seen.fresh.every(i => extra.has(i)) ? alts.find(fits) : null);
      if (!match) {
        problems.push(`lit ${list(seen.lit)}, want one of ${alts.map(list).join(" ")}`);
      } else if (match.length > 1) {
        const own = (c.chord_pills || {})[[...match].sort((a, b) => a - b).join(",")];
        const want = [c.legend, c.legend.toLowerCase(), c.legend.toUpperCase()].map(l => glyphLegend(km, l, null))
          .concat(own ? [glyphLegend(km, own.tap, own.glyph)] : []);
        if (seen.pills.length !== 1 || !want.includes(seen.pills[0]))
          problems.push(`pills ${show(seen.pills)} for the chord ${list(match)}, want one of ${show(want)}`);
      } else if (seen.pills.length) {
        problems.push(`pills ${show(seen.pills)} for the single key ${list(match)}`);
      }
    }
    const text = seen.strip.join("");
    if (strip !== false) {
      if (ch.reports && !c.accepts.includes(text)) problems.push(`strip ${show(text)}, typed ${show(c.legend)}`);
      if (!ch.reports && text) problems.push(`strip ${show(text)} with no reports`);
    }
    return problems.slice();
  };
}

// ---------- the runs ----------

function fresh(km, ch, c) {
  const p = loadPage();
  p.hud.load(km);
  if (ch.layers) { p.hud.setLayers(c.live); p.clock.advance(LAYER_LEAD_MS); }
  return p;
}

function main() {
  const argv = process.argv.slice(2);
  const opt = { keymap: FIXTURE, verbose: argv.includes("--verbose") || argv.includes("-v"), max: 40 };
  if (argv.includes("--keymap")) opt.keymap = argv[argv.indexOf("--keymap") + 1];
  if (argv.includes("--max")) opt.max = Number(argv[argv.indexOf("--max") + 1]);

  const rows = generate(opt.keymap);
  if (rows.error) {
    console.log("words_test: skipped, the cases need python3 (set PYTHON=/path/to/python3)");
    process.exit(0);
  }
  const km = JSON.parse(fs.readFileSync(opt.keymap, "utf8"));
  const allActivators = [...new Set((km.activators || []).map(a => a.idx))];
  const cases = rows.filter(r => r.kind === "case");
  const words = rows.filter(r => r.kind === "word");

  const fail = [];
  let checks = 0;
  const t0 = Date.now();

  // Every way, alone, down every channel.
  for (const c of cases) {
    for (const ch of CHANNELS) {
      checks++;
      const p = fresh(km, ch, c);
      const problems = expected(km, c, ch, allActivators)(ch.run(p, c));
      if (problems.length) fail.push({ what: `${JSON.stringify(c.legend)} on ${c.state} by ${list(c.keys)}${c.held.length ? " ⇧" + list(c.held) : ""}`, ch: ch.name, problems });
    }
  }

  // Typing sent in, once per legend: a report cannot say which way it was struck, so every way of
  // a legend is the same message here.
  const firstOf = new Map();
  for (const c of cases) if (!firstOf.has(c.legend)) firstOf.set(c.legend, c);
  for (const c of firstOf.values()) {
    for (const ch of SENT) {
      checks++;
      const p = loadPage();
      p.hud.load(km);
      if (ch.layers) { p.hud.setLayers(ch.live); p.clock.advance(LAYER_LEAD_MS); }
      const problems = expected(km, c, ch, allActivators)(ch.run(p, c));
      if (problems.length) fail.push({ what: `${JSON.stringify(c.legend)} sent in`, ch: ch.name, problems });
    }
  }

  // The keys that type a legend with a given layer set up, for a step whose layers just changed.
  const waysOn = new Map();
  for (const c of cases) {
    const k = `${c.legend}\u0000${JSON.stringify(c.live)}`;
    if (!waysOn.has(k)) waysOn.set(k, []);
    waysOn.get(k).push(c.keys);
  }
  const pressMs = (km.hud && km.hud.press_ms) || 320;

  // Every word, key after key on one page, down every channel.
  for (const w of words) {
    for (const ch of CHANNELS) {
      const p = loadPage();
      p.hud.load(km);
      let broke = null;
      if (ch.layers) { p.hud.setLayers(w.steps[0].live); p.clock.advance(LAYER_LEAD_MS); }
      for (const [i, step] of w.steps.entries()) {
        checks++;
        const prev = i ? w.steps[i - 1] : null;
        const inFlight = prev && w.gap_ms < pressMs && JSON.stringify(prev.live) !== JSON.stringify(step.live)
          ? waysOn.get(`${step.legend}\u0000${JSON.stringify(prev.live)}`) || [] : [];
        const problems = expected(km, step, ch, allActivators, inFlight)(ch.run(p, step, snapshot(p)), false);
        if (problems.length && !broke) broke = { at: i, step, problems };
        // Between two keys, the layer the next one needs comes up -- or this one's goes away, a
        // one-shot's right after the key it served -- and then the next key goes down.
        const next = w.steps[i + 1];
        if (next && ch.layers) p.hud.setLayers(next.live);
        if (next) p.clock.advance(w.gap_ms);
      }
      const text = observe(p).strip.join("");
      checks++;
      const spelled = w.steps.map(s => s.legend).join("");
      if (ch.reports && text !== spelled && !broke) broke = { at: w.steps.length, problems: [`strip ${show(text)}, typed ${show(spelled)}`] };
      if (broke) {
        const how = w.steps.map(s => `${s.legend}:${s.state}${list(s.keys)}`).join(" ");
        fail.push({ what: `word ${JSON.stringify(w.word)} (${how}) at key ${broke.at + 1}`, ch: ch.name, problems: broke.problems });
      }
    }
  }

  // Every word sent in, as `zmk-layer-hud poke --type` sends it: a character every gap_ms.
  const spelled = new Map();
  for (const w of words) if (!spelled.has(w.word)) spelled.set(w.word, w);
  for (const w of spelled.values()) {
    for (const ch of SENT) {
      const p = loadPage();
      p.hud.load(km);
      if (ch.layers) { p.hud.setLayers(ch.live); p.clock.advance(LAYER_LEAD_MS); }
      let broke = null;
      for (const [i, step] of w.steps.entries()) {
        if (i) p.clock.advance(POKE_GAP_MS);
        checks++;
        const problems = expected(km, step, ch, allActivators)(ch.run(p, step, snapshot(p)), false);
        if (problems.length && !broke) broke = { at: i, problems };
      }
      checks++;
      const text = observe(p).strip.join(""), want = w.steps.map(s => s.legend).join("");
      if (text !== want && !broke) broke = { at: w.steps.length, problems: [`strip ${show(text)}, typed ${show(want)}`] };
      if (broke) fail.push({ what: `word ${JSON.stringify(w.word)} sent in, at key ${broke.at + 1}`, ch: ch.name, problems: broke.problems });
    }
  }

  // Report failures grouped by what went wrong, so one cause reads as one line with its count.
  const groups = new Map();
  for (const f of fail) {
    const key = `${f.ch}: ${f.problems[0].replace(/⟨[^⟩]*⟩/g, "⟨…⟩").replace(/[\[\d,\]]+/g, "#").replace(/"(?:[^"\\]|\\.)*"/g, "\"…\"")}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(f);
  }
  let shown = 0;
  for (const [key, fs_] of [...groups.entries()].sort((a, b) => b[1].length - a[1].length)) {
    console.log(`FAIL x${fs_.length}  ${key}`);
    for (const f of fs_.slice(0, opt.verbose ? fs_.length : 3)) console.log(`      ${f.what}: ${f.problems.join("; ")}`);
    if (!opt.verbose && ++shown >= opt.max) { console.log(`  ... and ${groups.size - shown} more kinds`); break; }
  }
  const legends = new Set(cases.map(c => c.legend)).size;
  console.log(`${cases.length} ways to type ${legends} legends and ${new Set(words.map(w => w.word)).size} words ` +
              `(${words.length} spellings), ${CHANNELS.length} channels from the keyboard and ${SENT.length} typed in: ` +
              `${checks} checks, ${fail.length} failures (${Date.now() - t0} ms)`);
  process.exit(fail.length ? 1 : 0);
}

main();
