#!/usr/bin/env node
/* zmk-layer-hud — a demo script, played on the page the way the demo plays it.
 *
 *   node hud/tests/play_test.js [--verbose]
 *
 * host/play.py turns a script into what a keyboard would send, and when. This sends it to the page
 * at those moments, on the tests' clock, and checks each keystroke as it lands: the keys down are
 * the keys lit, a chord draws its pill and a single key none, and what was typed ends the strip.
 * The committed scripts, each on the keymap it was written for. Needs python3 (PYTHON=... to
 * choose one); skips without it.
 */
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { loadPage } = require("./dom.js");

const REPO = path.join(__dirname, "..", "..");
const FIXTURES = path.join(__dirname, "fixtures");
const PAIRS = [["docs/demo-type.json", "example-3x5.json"], ["docs/demo-3x5.json", "example-3x5.json"],
               ["docs/demo-vim.json", "diamond.json"]];

function pythons() {
  const chosen = process.env.PYTHON;
  return (chosen ? [chosen] : []).concat([path.join(REPO, ".venv/bin/python3"), "python3"]);
}

function play(args) {
  let lastErr = null;
  for (const py of pythons()) {
    try {
      const out = execFileSync(py, [path.join(REPO, "host/play.py"), ...args], { encoding: "utf8", cwd: REPO, stdio: ["ignore", "pipe", "ignore"] });
      return out.trim().split("\n").filter(Boolean).map(l => JSON.parse(l));
    } catch (e) { lastErr = e; }
  }
  return { error: lastErr };
}

function main() {
  const verbose = process.argv.includes("--verbose") || process.argv.includes("-v");
  const fail = [];
  let checked = 0;
  for (const [script, fixture] of PAIRS) {
    const keymap = path.join(FIXTURES, fixture);
    const timeline = play([script, "--keymap", keymap, "--timeline"]);
    const strokes = play([script, "--keymap", keymap, "--strokes"]);
    if (timeline.error || strokes.error) {
      console.log("play_test: skipped, host/play.py needs python3 (set PYTHON=/path/to/python3)");
      process.exit(0);
    }
    const data = JSON.parse(fs.readFileSync(keymap, "utf8"));
    const posOf = new Map(Object.entries(data.positions || {}).map(([p, i]) => [Number(i), Number(p)]));
    const page = loadPage();
    page.hud.load(data);
    const lit = () => page.hud.state.keyEls.map((e, i) => (e.classList.contains("pressed") ? posOf.get(i) : null))
      .filter(p => p !== null && p !== undefined).sort((a, b) => a - b);
    const pills = () => page.board.querySelectorAll(".combo-pill");
    const strip = () => page.document.getElementById("keys").children.map(c => c.textContent);

    // Everything on one clock: the messages when they are sent, and each keystroke looked at just
    // before it goes down and once its reports are in.
    const at = [];
    let t = 0;
    for (const [wait, msg] of timeline) { t += wait; at.push([t, 1, msg]); }
    for (const s of strokes) { at.push([s.at_ms, 0, s]); at.push([s.check_ms, 2, s]); }
    at.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    let now = 0;
    for (const [time, kind, x] of at) {
      if (time > now) { page.clock.advance(time - now); now = time; }
      if (kind === 1) { page.hud.receive(x); continue; }
      if (kind === 0) { x.before = new Set(pills()); continue; }
      checked++;
      const where = `${script} step ${x.step} ${JSON.stringify(x.text)} at ${x.at_ms} ms`;
      const want = [...x.down].sort((a, b) => a - b);
      if (lit().join(",") !== want.join(",")) fail.push(`${where}: lit [${lit()}], down [${want}]`);
      const fresh = pills().filter(p => !x.before.has(p));
      if (x.combo ? fresh.length !== 1 : fresh.length) fail.push(`${where}: ${fresh.length} new pills for ${x.combo ? "a chord" : "a single key"}`);
      const chips = strip();
      if (x.text.length === 1 && x.text.trim() && !(chips.length && chips[chips.length - 1].endsWith(x.text))) {
        fail.push(`${where}: strip ${JSON.stringify(chips)}`);
      }
      if (verbose) console.log(`ok   ${where}`);
    }
    checked++;
    page.clock.advance(3000);
    if (page.hud.state.held.size) fail.push(`${script}: ${page.hud.state.held.size} keys still held after the script`);
    page.clock.advance(12000);
    if (page.clock.pending()) fail.push(`${script}: ${page.clock.pending()} timers left once it is over`);

    // A live GIF's frame (hud.js replayTo, docs/make-gif.sh --live): a fresh page, played up to a
    // moment on its own clock, must show that moment as the page that lived through it did.
    const capture = play([script, "--keymap", keymap, "--capture"])[0];
    for (const s of strokes.filter((s, i) => i % 3 === 0)) {
      checked++;
      const frame = loadPage();
      frame.hud.replayTo(data, capture, s.check_ms);
      const got = frame.hud.state.keyEls.map((e, i) => (e.classList.contains("pressed") ? posOf.get(i) : null))
        .filter(p => p !== null && p !== undefined).sort((a, b) => a - b);
      const want = [...s.down].sort((a, b) => a - b);
      if (got.join(",") !== want.join(",")) fail.push(`${script} frame at ${s.check_ms} ms: lit [${got}], down [${want}]`);
      if (s.strike.some(p => frame.hud.state.keyEls[data.positions ? data.positions[String(p)] : p].style.getPropertyValue("--heat") === "")) {
        fail.push(`${script} frame at ${s.check_ms} ms: a key just struck has no glow`);
      }
    }
  }
  for (const f of fail) console.log("FAIL " + f);
  console.log(`${PAIRS.length} scripts played, ${checked} keystrokes checked, ${fail.length} failures`);
  process.exit(fail.length ? 1 : 0);
}

main();
