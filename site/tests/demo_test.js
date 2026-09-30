#!/usr/bin/env node
/* zmk-layer-hud — the landing page's demo: the translator, the player, and the two on the HUD page.
 *
 *   node site/tests/demo_test.js [--verbose]
 *
 * site/demo.js is required as it ships. The translator is fed browser events in the orders browsers
 * really fire them (a dead key's input before its compositionend, a phone's keyboard with no
 * keydown worth reading), and has to hand the page the feed's own messages, once each. The player
 * runs on the tests' clock (hud/tests/dom.js). Last, both drive the real HUD page, ?embed and all,
 * on the committed 3x5 sample -- the demo compiled by host/play.py, which needs python3 (PYTHON=...
 * to choose one); that part skips without it.
 */
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { Translator, Player, LATE_MS } = require("../demo.js");
const { loadPage, Clock } = require("../../hud/tests/dom.js");

const REPO = path.join(__dirname, "..", "..");
const FIXTURE = path.join(REPO, "hud", "tests", "fixtures", "example-3x5.json");
const verbose = process.argv.includes("--verbose") || process.argv.includes("-v");
const fail = [];
let checked = 0;
function check(ok, what) {
  checked++;
  if (!ok) fail.push(what);
  else if (verbose) console.log("ok   " + what);
}
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// ---------- the translator ----------

/* A field and the events a browser fires on it, the order spelled out by each test. */
function typist() {
  const sent = [];
  const t = new Translator(m => sent.push(m));
  let value = "";
  const mods = { shiftKey: false, ctrlKey: false, altKey: false, metaKey: false };
  const kd = (key, extra = {}) => t.keydown(Object.assign({ key, repeat: false, isComposing: false, keyCode: 0 }, mods, extra));
  const ku = (key, extra = {}) => t.keyup(Object.assign({ key }, mods, extra));
  const input = (next, inputType = "insertText", extra = {}) => { value = next; t.input(Object.assign({ inputType, isComposing: false }, extra), value); };
  const type = ch => { kd(ch); input(value + ch); };
  return { t, sent, kd, ku, input, type, mods, get value() { return value; },
           compositionend: next => { value = next; t.compositionend({}, value); } };
}
const keys = sent => sent.filter(m => m.type === "keyDown").map(m => [m.name, m.chars]);

{
  const f = typist();
  for (const ch of "hi there") f.type(ch);
  check(same(keys(f.sent), [["h", "h"], ["i", "i"], ["space", " "], ["t", "t"], ["h", "h"], ["e", "e"], ["r", "r"], ["e", "e"]]),
        "letters and a space are the feed's messages, one each");
  const m = f.sent[0];
  check(m.kind === "key" && m.repeat === false && m.sent === true && m.synthetic === true && m.combos === false,
        "each is typing sent in: sent, synthetic, combos as the toggle says");
  check(same(m.flags, { cmd: false, ctrl: false, alt: false, shift: false }), "no modifiers: every flag false");
}
{
  const f = typist();
  f.mods.shiftKey = true;
  f.kd("Shift");
  f.kd("H");
  f.input("H");
  f.mods.shiftKey = false;
  f.ku("Shift");
  const flags = f.sent.filter(m => m.type === "flagsChanged").map(m => m.flags.shift);
  check(same(flags, [true, false]), "Shift down and up are flagsChanged, so the legends capitalise while it is held");
  const h = f.sent.find(m => m.type === "keyDown");
  check(h && h.name === "H" && h.chars === "H" && h.flags.shift === true, "a capital carries its Shift");
}
{
  const f = typist();
  f.mods.altKey = true;
  f.kd("™");
  f.input("™");
  check(same(keys(f.sent), [["™", "™"]]) && f.sent.find(m => m.type === "keyDown").flags.alt === true,
        "a character Option makes (⌥2 → ™) is that character, with alt");
}
{
  const f = typist();
  f.mods.metaKey = true;
  f.kd("c");
  f.mods.metaKey = false;
  f.mods.ctrlKey = true;
  f.mods.shiftKey = true;
  f.kd("A");
  const chords = f.sent.filter(m => m.type === "keyDown");
  check(same(chords.map(m => [m.name, m.flags.cmd, m.flags.ctrl, m.flags.shift]), [["c", true, false, false], ["A", false, true, true]]),
        "⌘c and ⌃⇧a are chords, from keydown");
}
{
  const f = typist();
  f.mods.ctrlKey = true;
  f.mods.altKey = true;
  f.kd("@", { getModifierState: k => k === "AltGraph" });
  f.input("@");
  const at = f.sent.filter(m => m.type === "keyDown");
  check(same(keys(f.sent), [["@", "@"]]) && !at[0].flags.ctrl && !at[0].flags.alt, "AltGr types a character, not a ⌃⌥ chord");
}
{
  const f = typist();
  f.type("a");
  f.kd("Backspace"); f.input("", "deleteContentBackward");
  f.kd("Enter"); f.input("\n", "insertLineBreak");
  f.kd("Delete");
  f.kd("Escape");
  f.kd("ArrowLeft");
  f.kd("F5");
  check(same(keys(f.sent), [["a", "a"], ["delete", "\x7f"], ["return", "\r"], ["forwarddelete", ""], ["escape", "\x1b"], ["left", ""], ["f5", ""]]),
        "named keys have the feed's names, and the input after Backspace or Enter is not typed again");
}
{
  const f = typist();
  f.kd("ArrowLeft");       // an arrow changes nothing in the field: no input follows it
  f.type("b");
  f.kd("Backspace");       // nothing to delete: no input either
  f.value;
  f.kd("c"); f.input(f.value + "c");
  check(same(keys(f.sent), [["left", ""], ["b", "b"], ["delete", "\x7f"], ["c", "c"]]),
        "a key that changes nothing does not swallow the next character");
}
{
  const f = typist();
  f.kd("Tab");
  check(f.sent.length === 0, "Tab sends nothing: it is how focus leaves the field");
}
{
  // Chrome, for ⌥e e: a Dead keydown, an input while composing, the next keydown composing, then
  // compositionend with the committed é, then an input that is not composing.
  const f = typist();
  f.mods.altKey = true;
  f.kd("Dead");
  f.input("´", "insertCompositionText", { isComposing: true });
  f.mods.altKey = false;
  f.kd("e", { isComposing: true });
  f.compositionend("é");
  f.input("é", "insertCompositionText");
  check(same(keys(f.sent), [["é", "é"]]), "a dead key and its letter are one é, whichever event comes last");
}
{
  const f = typist();
  f.input("pasted", "insertFromPaste");
  f.input("", "historyUndo");
  f.type("x");
  check(same(keys(f.sent), [["x", "x"]]), "pasting and undoing type nothing, and typing goes on after them");
}
{
  const f = typist();
  f.type("a");
  f.kd("a", { repeat: true }); f.input("aa");
  f.kd("a", { repeat: true }); f.input("aaa");
  check(same(keys(f.sent), [["a", "a"]]), "a held key's repeats are not typing: the feed never reports them");
}
{
  // A phone: the keydown says nothing (229), the text is in the input; its backspace has no keydown.
  const f = typist();
  f.kd("Unidentified", { keyCode: 229 }); f.input("h");
  f.kd("Unidentified", { keyCode: 229 }); f.input("", "deleteContentBackward");
  check(same(keys(f.sent), [["h", "h"], ["delete", "\x7f"]]), "a phone's typing and its backspace, from the input alone");
}
{
  const f = typist();
  f.t.combos = true;
  f.type("z");
  f.mods.shiftKey = true;
  f.kd("Shift");
  f.t.blur();
  check(f.sent.find(m => m.type === "keyDown").combos === true, "combos follows the toggle");
  const last = f.sent[f.sent.length - 1];
  check(last.type === "flagsChanged" && !last.flags.shift, "blur lets every modifier go");
}

// ---------- the player ----------

function clockOf(c) {
  return { now: () => c.now, setTimeout: (fn, ms) => c.setTimeout(fn, ms), clearTimeout: id => c.clearTimeout(id) };
}
const P = (pos) => ({ kind: "press", pos });
const R = (pos) => ({ kind: "release", pos });

{
  const c = new Clock(0), got = [];
  const p = new Player(m => got.push([c.now, m]), clockOf(c));
  p.play({ timeline: [[0, P(1)], [100, R(1)], [50, { kind: "layers", ids: [2] }]], duration_ms: 400 });
  c.advance(399);
  check(same(got.map(([t]) => t), [0, 100, 150]), "each message at t0 plus the waits before it");
  check(got.every(([, m]) => m.sent === true), "every played message is stamped sent");
  c.advance(1);
  check(!p.playing && c.pending() === 0, "without loop it is over at duration_ms, and leaves no timer");
}
{
  const c = new Clock(0), got = [];
  const p = new Player(m => got.push([c.now, m]), clockOf(c));
  p.play({ timeline: [[0, P(1)], [100, P(2)], [5, P(3)]], duration_ms: 500 });
  c.advance(0);
  c.now = 500;             // the page stalls: the next timer comes 400 ms late
  c.advance(0);
  c.advance(10);
  const [t2, t3] = [got[1][0], got[2][0]];
  check(got.length === 3 && t3 - t2 === 5, `a stalled page does not send the backlog at once (${t2} then ${t3})`);
  check(LATE_MS > 5, "lateness is well above a chord's spacing");
}
{
  const c = new Clock(0), got = [];
  const p = new Player(m => got.push(m), clockOf(c));
  p.play({ timeline: [[0, { kind: "layers", ids: [3] }], [10, P(32)], [10, P(16)], [500, R(16)]], duration_ms: 1000 });
  c.advance(30);
  got.length = 0;
  p.stop();
  check(same(got.map(m => m.kind === "layers" ? "layers:" + m.ids : m.kind + ":" + m.pos).sort(), ["layers:", "release:16", "release:32"]),
        "stop() in a hold lets every key go and puts the layers down");
  check(c.pending() === 0 && !p.playing, "and leaves no timer");
}
{
  const c = new Clock(0), got = [];
  const p = new Player(m => got.push(c.now), clockOf(c));
  p.play({ timeline: [[0, P(1)], [10, R(1)]], duration_ms: 100 }, { loop: true });
  c.advance(250);
  check(same(got, [0, 10, 100, 110, 200, 210]), "loop starts again after duration_ms");
  p.stop();
  const q = new Player(() => {}, clockOf(c));
  q.play({ timeline: [], duration_ms: 0 }, { loop: true });
  check(!q.playing && c.pending() === 0, "an empty demo does not loop over nothing");
}

// ---------- on the page ----------

function pythons() {
  const chosen = process.env.PYTHON;
  return (chosen ? [chosen] : []).concat([path.join(REPO, ".venv/bin/python3"), "python3"]);
}
function capture(script) {
  for (const py of pythons()) {
    try {
      const out = execFileSync(py, [path.join(REPO, "host/play.py"), script, "--keymap", FIXTURE, "--capture"],
                               { encoding: "utf8", cwd: REPO, stdio: ["ignore", "pipe", "ignore"] });
      return JSON.parse(out);
    } catch (e) { /* the next one */ }
  }
  return null;
}

const data = JSON.parse(fs.readFileSync(FIXTURE, "utf8"));
function page() {
  const pg = loadPage({ search: "?embed", protocol: "http:" });
  pg.hud.receive(data);
  pg.hud.receive({ kind: "layers", ids: [] });
  pg.lit = () => pg.hud.state.keyEls.flatMap((e, i) => (e.classList.contains("pressed") ? [i] : []));
  pg.pills = () => pg.board.querySelectorAll(".combo-pill").length;
  return pg;
}
const keyOf = tap => (data.layers[data.base] || []).findIndex(k => k.tap === tap);

{
  // Typing sent in, on the capitals board: one key per letter, and each goes out.
  const pg = page();
  const f = typist();
  f.t.send = m => pg.hud.receive(m);
  const each = [];
  for (const ch of "hello") {
    f.type(ch);
    each.push(pg.lit().join());
    pg.clock.advance(600);
  }
  check(same(each, ["H", "E", "L", "L", "O"].map(t => String(keyOf(t)))), `"hello" lights one key per letter (${each.join(" | ")})`);
  pg.clock.advance(6000);
  f.t.combos = true;
  f.kd("(", { shiftKey: true }); f.input(f.value + "(");
  check(pg.lit().length === 2 && pg.pills() === 1, `"(" with combos on lights its chord and draws its pill (lit ${pg.lit()})`);
}

const demo = capture("docs/demo-type.json");
if (!demo) {
  console.log("demo_test: the page part skipped, host/play.py needs python3 (set PYTHON=/path/to/python3)");
} else {
  const pg = page();
  const p = new Player(m => pg.hud.receive(m), clockOf(pg.clock));
  p.play(demo);
  pg.clock.advance(demo.duration_ms + 3000);
  check(pg.lit().length === 0 && pg.hud.state.held.size === 0, "the demo, played to its end, leaves nothing lit and nothing held");
  check(!p.playing, "and is over");

  // Stopped in the middle, then typed on: the demo's positions are fresh, and a key typed in
  // still lights (it is synthetic, as no firmware position stands behind it).
  const pg2 = page();
  const p2 = new Player(m => pg2.hud.receive(m), clockOf(pg2.clock));
  p2.play(demo);
  pg2.clock.advance(1200);
  p2.stop();
  pg2.clock.advance(100);
  const f = typist();
  f.t.send = m => pg2.hud.receive(m);
  f.type("h");
  check(pg2.lit().includes(keyOf("H")), `a key typed 100 ms after a played press still lights (lit ${pg2.lit()})`);
}

for (const f of fail) console.log("FAIL " + f);
console.log(`${checked} demo checks, ${fail.length} failures`);
process.exit(fail.length ? 1 : 0);
