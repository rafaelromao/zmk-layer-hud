#!/usr/bin/env node
/* zmk-layer-hud — the Android keyboard's page (apps/android/app/src/main/assets/ime-boot.js), on the
 * HUD's page in the tests' DOM: what a tap posts to the keyboard (HudIme.kt reads it with
 * sink/Ops.kt's PageMessage), the size it says, the next-keyboard key, and the board docked.
 *
 *   node apps/tests/ime_boot_test.js [--verbose]
 */
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { loadPage } = require("../../hud/tests/dom.js");

const VERBOSE = process.argv.includes("--verbose");
const fail = [];
let checks = 0;
function check(ok, what) { checks++; if (!ok) fail.push(what); else if (VERBOSE) console.log("ok   " + what); }
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

const BOOT = fs.readFileSync(path.join(__dirname, "..", "android", "app", "src", "main", "assets", "ime-boot.js"), "utf8");
const K = (tap) => ({ tap, hold: "", shifted: "", type: "key" });
const kp = (id) => ({ op: "kp", key: { page: 7, id, mods: 0 } });
const MSG = {
  kind: "keymap", schema: 1, source: "ime-test", title: "", stats: {}, fingers: null,
  combo_term: 50, combo_idle: 0, altgr: false, glyphs: {}, positions: { 0: 0, 1: 1 },
  layout: { width: 200, height: 100, keys: [0, 1].map((i) => ({ x: 50 + 100 * i, y: 50, w: 100, h: 100, r: 0 })) },
  layers: { base: [K("a"), K("⌫")] }, layer_order: ["base"], combos: [], activators: [],
  zmk_layers: { 0: { id: 0, name: "BASE", drawer: "base", label: "Base", cls: "off" } },
  base: "base", extras: { base: "base", sticky: [], search: [] },
  actions: { v: 1, source: "keymap", positions: 2, default: 0, table: [kp(0x04), kp(0x2A)],
             layers: { 0: [0, 1] }, combos: [], conditional: [] },
  touch: { enabled: 1, floating: 0, hold_ms: 280, flavor: "balanced", latch: 1, lock_ms: 350, sticky_ms: 0,
           repeat_delay_ms: 450, repeat_ms: 60, combos: "chips", heat: 1, strip: 1 },
};

// The banner's next-keyboard key, which ime-boot.js adds (the test DOM finds only the page's own ids).
const nextKeys = (p) => p.document.getElementById("layer").children.filter((c) => c.id === "nextKeyboard");

function keyboard(conf) {
  const p = loadPage({ search: "?embed", innerHeight: 1000 });
  p.posted = [];
  p.window.zmkhudIme = { postMessage: (text) => p.posted.push(JSON.parse(text)) };
  p.window.ZMKHUD_IME = conf;
  vm.runInContext(BOOT, p.window);
  return p;
}

(function aTapIsTextForTheField() {
  const p = keyboard({ maxHeight: 300, canSwitch: true });
  const html = p.document.documentElement;
  check(html.classList.contains("dock"), "the board is docked");
  check(eq(p.hud.state.dock, { maxHeight: 300, split: true }), `as tall as the keyboard's share allows: ${JSON.stringify(p.hud.state.dock)}`);
  p.hud.receive(MSG);
  check(html.classList.contains("touch"), "and types once the keymap is there");
  p.posted.length = 0;
  for (const idx of [0, 1]) {
    p.hud.state.keyEls[idx].fire("pointerdown", { pointerId: idx + 1 });
    p.clock.advance(30);
    p.hud.state.keyEls[idx].fire("pointerup", { pointerId: idx + 1 });
  }
  const ops = p.posted.filter((m) => m.kind === "ops").flatMap((m) => m.ops);
  check(eq(ops, [{ kind: "text", text: "a", usage: 4, mods: 0 }, { kind: "key", key: "backspace", usage: 42, mods: 0 }]),
        `a letter is text and backspace a key, each with its usage: ${JSON.stringify(ops)}`);
  check(p.posted.filter((m) => m.kind === "ops").every((m) => m.v === 1 && Number.isInteger(m.seq)), "every message versioned and numbered");
  check(!p.posted.some((m) => m.kind === "hid"), "and no report snapshots: the keyboard types text");
  const said = p.posted.filter((m) => m.kind === "feedback").map((m) => m.key);
  check(eq(said, ["standard", "delete"]), `each press asks the keyboard for its buzz and click: ${JSON.stringify(said)}`);
  check(p.posted.findIndex((m) => m.kind === "feedback") < p.posted.findIndex((m) => m.kind === "ops"),
        "before the text it types: on the press");
})();

(function itSaysHowTallItCameOut() {
  const p = keyboard({ maxHeight: 300, canSwitch: false });
  check(p.posted.some((m) => m.kind === "size" && Number.isInteger(m.height)), "the board's height goes to the keyboard");
  check(nextKeys(p).length === 0, "no next-keyboard key where Android says not to offer one");
  p.clock.advance(1);
  check(/no keymap yet/.test(p.document.getElementById("layerName").textContent), "without a keymap the banner says where one comes from");
})();

(function theNextKeyboardKey() {
  const p = keyboard({ maxHeight: 300, canSwitch: true });
  const [found] = nextKeys(p);
  check(!!found, "the next-keyboard key is in the banner");
  if (found) found.click();
  check(p.posted.some((m) => m.kind === "switch"), "and asks the keyboard for the next one");
  vm.runInContext(BOOT, p.window);
  check(nextKeys(p).length === 1, "booted twice, it is still one key");
})();

console.log(`the Android keyboard's page: ${checks} checks, ${fail.length} failures`);
if (fail.length) {
  for (const f of fail) console.log("FAIL " + f);
  process.exit(1);
}
