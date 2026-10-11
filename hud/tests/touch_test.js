#!/usr/bin/env node
/* zmk-layer-hud — the touch keyboard on the page (hud/touch.js), on the tests' clock.
 *
 *   node hud/tests/touch_test.js [--verbose]
 *
 * Fingers on the board's keys: what goes to the sink and when, what the board draws while they are
 * down and after, what they count (nothing), and the board docked along the bottom of a screen.
 * A four-key keymap written here, its actions with it, so the test needs nothing but node:
 *
 *   base:  a   b   ␣/nav (a layer-tap)   ⇧ (a plain Shift)
 *   nav:   1   ▽   (held)                ⇧
 */
"use strict";

const { loadPage } = require("./dom.js");

const VERBOSE = process.argv.includes("--verbose");
const fail = [];
let checks = 0;
function check(ok, what) { checks++; if (!ok) fail.push(what); else if (VERBOSE) console.log("ok   " + what); }
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

const K = (tap, extra) => Object.assign({ tap, hold: "", shifted: "", type: "key" }, extra || {});
const kp = (id) => ({ op: "kp", key: { page: 7, id, mods: 0 } });
const MSG = {
  kind: "keymap", schema: 1, source: "touch-test", title: "", stats: {}, fingers: null,
  combo_term: 50, combo_idle: 0, altgr: false, glyphs: {},
  positions: { 0: 0, 1: 1, 2: 2, 3: 3 },
  layout: { width: 400, height: 100, keys: [0, 1, 2, 3].map((i) => ({ x: 50 + 100 * i, y: 50, w: 100, h: 100, r: 0 })) },
  layers: { base: [K("a"), K("b"), K("␣", { hold: "nav" }), K("⇧")],
            nav: [K("1"), K("", { type: "trans" }), K("", { type: "held" }), K("⇧")] },
  layer_order: ["base", "nav"], combos: [], activators: [{ layer: "nav", idx: 2, kind: "hold" }],
  zmk_layers: { 0: { id: 0, name: "BASE", drawer: "base", label: "Base", cls: "off" },
                1: { id: 1, name: "NAV", drawer: "nav", label: "Nav", cls: "momentary" } },
  base: "base", extras: { base: "base", sticky: [], search: ["nav"] },
  actions: {
    v: 1, source: "keymap", positions: 4, default: 0,
    table: [kp(0x04), kp(0x05), { op: "ht", hold: { op: "mo", layer: 1, locking: false }, tap: kp(0x2C), flavor: "tap-preferred",
            term: 200, quick_tap: -1, prior_idle: -1, retro_tap: false, trigger_positions: null, trigger_on_release: false },
            kp(0xE1), kp(0x1E), { op: "trans" }],
    layers: { 0: [0, 1, 2, 3], 1: [4, 5, 5, 3] }, combos: [], conditional: [],
  },
  touch: { enabled: 1, floating: 0, hold_ms: 280, flavor: "balanced", latch: 1, lock_ms: 350, sticky_ms: 0,
           repeat_delay_ms: 450, repeat_ms: 60, combos: "chips", heat: 1, strip: 1 },
};

function page(opts) {
  const p = loadPage(opts);
  p.hud.load(JSON.parse(JSON.stringify(MSG)));
  p.hud.setLayers([]);
  p.sent = [];
  p.hud.setTouch({ output: "hid", repeat: "os", send: (m) => p.sent.push(Object.assign({ at: p.clock.now }, m)) });
  let id = 0;
  p.key = (idx) => p.hud.state.keyEls[idx];
  p.down = (idx, pointerId) => p.key(idx).fire("pointerdown", { pointerId: pointerId || ++id });
  p.up = (idx, pointerId, type) => p.key(idx).fire(type || "pointerup", { pointerId });
  p.tap = (idx, ms) => { const pid = ++id; p.down(idx, pid); p.clock.advance(ms || 40); p.up(idx, pid); };
  p.reports = () => p.sent.filter((m) => m.kind === "hid").map((m) => [m.mods, m.keys]);
  return p;
}

(function aTapSendsItsKeyDownAndUp() {
  const p = page();
  const pid = 7;
  const ev = p.down(0, pid);
  check(ev.defaultPrevented, "a touch on a key is not a scroll or a selection");
  check(p.key(0).classList.contains("pressed"), "the key under a finger lights");
  p.clock.advance(40);
  p.up(0, pid);
  check(eq(p.reports(), [[0, [4]], [0, []]]), `a tap sends its key down, then nothing held: ${JSON.stringify(p.reports())}`);
  check(!p.key(0).classList.contains("pressed"), "and the key goes dark when the finger lifts");
  check(p.sent.every((m, i) => i === 0 || m.seq > p.sent[i - 1].seq), "every report numbered in order");
})();

(function aKeyHeldIsSaidAgain() {
  const p = page();
  p.down(1, 1);
  p.clock.advance(1100);
  const beats = p.sent.filter((m) => eq(m.keys, [5])).length;
  check(beats === 3, `a key held is said again every 500 ms: ${beats} times in 1.1 s`);
  p.up(1, 1);
  const after = p.sent.length;
  p.clock.advance(2000);
  check(p.sent.length === after, "and not once it is let go");
  check(p.clock.pending() === 0, "nothing left scheduled");
})();

(function cancelReleases() {
  const p = page();
  p.down(0, 3);
  p.up(0, 3, "pointercancel");
  check(eq(p.reports().pop(), [0, []]), "a cancelled touch lets its key go");
})();

(function aTouchInTheGapGoesToTheNearestKey() {
  const p = page();
  const board = p.board;
  // Key 0 spans 3..139.5 px at this board's scale; the gap runs on to key 1 at 145.5.
  board.fire("pointerdown", { pointerId: 9, clientX: 141, clientY: 70 });
  board.fire("pointerup", { pointerId: 9 });
  check(eq(p.reports()[0], [0, [4]]), `a touch a pixel into the gap types the key beside it: ${JSON.stringify(p.reports())}`);
  board.fire("pointerdown", { pointerId: 10, clientX: 300, clientY: 400 });
  check(p.reports().length === 2, "a touch far from every key types nothing");
})();

(function twoFingers() {
  const p = page();
  p.down(2, 1);                       // the layer-tap, held
  p.clock.advance(300);               // past touch.hold_ms: nav
  check(p.hud.state.touch.ids[0] === 1, "a layer-tap held past hold_ms brings its layer up");
  check(/Nav/i.test(p.document.getElementById("layerName").textContent), "and the banner says so");
  p.down(0, 2);                       // a second finger, on nav's 1
  p.up(0, 2);
  p.up(2, 1);
  check(eq(p.reports().filter((r) => r[1].length)[0], [0, [0x1E]]), `the second finger types on the held layer: ${JSON.stringify(p.reports())}`);
  check(p.hud.state.touch.ids[0] === 0, "a hold used by another finger lets its layer go");
})();

(function aHoldLetGoAloneLatches() {
  const p = page();
  p.down(2, 1); p.clock.advance(300); p.up(2, 1);
  check(p.hud.state.touch.ids[0] === 1 && p.key(2).classList.contains("latched"), "held alone, the layer stays and its key is ringed");
  check(/one shot/.test(p.document.getElementById("layerSub").textContent), "the banner says one shot");
  p.tap(0);
  check(eq(p.reports().filter((r) => r[1].length), [[0, [0x1E]]]) && p.hud.state.touch.ids[0] === 0,
        "the next key types from it, and it goes");
})();

(function aShiftTappedTwiceLocksAndCapitalises() {
  const p = page();
  p.tap(3); p.clock.advance(100); p.tap(3);
  check(p.key(3).classList.contains("locked"), "Shift tapped twice is locked");
  check(p.key(0).querySelector(".tap").textContent === "A", "and the board draws the capitals");
  p.clock.advance(400); p.tap(0); p.clock.advance(100); p.tap(1);
  check(eq(p.reports().filter((r) => r[1].length), [[2, [4]], [2, [5]]]), `locked, every key is shifted: ${JSON.stringify(p.reports())}`);
})();

(function touchTypingCountsNowhere() {
  const p = page();
  for (let i = 0; i < 5; i++) { p.tap(0); p.clock.advance(150); }
  const local = p.hud.stats.local(), unsent = p.hud.stats.unsent();
  check(!Object.keys(local.presses).length && !Object.keys(unsent.presses).length, "touch typing is counted nowhere");
  check(p.hud.stats.wpm() === 0, "nor in the speed");
  check(p.keys && p.document.getElementById("keys").children.length > 0, "but the strip shows it");
  check(p.key(0).style.getPropertyValue("--heat") !== "", "and the key warms");
})();

(function aSecretShowsNothingButTypes() {
  const p = page();
  p.hud.receive({ kind: "secure", on: true });
  p.tap(0);
  check(p.document.getElementById("keys").children.length === 0, "while a secret is typed, the strip shows nothing");
  check(eq(p.reports()[0], [0, [4]]), "and the key still types");
})();

(function theKeyboardsLayersAreAdopted() {
  const p = page();
  p.hud.setLayers([1]);
  check(p.hud.state.touch.ids[0] === 1 && p.key(0).querySelector(".tap").textContent === "1",
        "the keyboard's own layer is the one the touch keyboard types on");
})();

(function thePanelGetsItAndKnowsTheBoardIsKeys() {
  const p = loadPage();
  const posted = [];
  p.window.webkit = { messageHandlers: { zmkhud: { postMessage: (s) => posted.push(JSON.parse(s)) } } };
  p.hud.load(JSON.parse(JSON.stringify(MSG)));
  p.hud.setLayers([]);
  p.hud.receive({ kind: "touch", on: true, sink: { output: "hid", repeat: "os" } });
  const noDrag = posted.filter((m) => m.kind === "nodrag").pop();
  const board = p.board.getBoundingClientRect();
  check(noDrag && noDrag.rects.some((r) => r[0] === Math.round(board.left) && r[2] === Math.round(board.width)),
        `the board is a no-drag area while it types: ${JSON.stringify(noDrag)}`);
  p.hud.state.keyEls[0].fire("pointerdown", { pointerId: 1 });
  check(posted.some((m) => m.kind === "hid" && eq(m.keys, [4])), "typing goes through the panel's bridge");
  p.hud.receive({ kind: "typing", state: "denied", fix: "zmk-layer-hud typing setup" });
  check(/typing setup/.test(p.document.getElementById("layerSub").textContent), "the host's fix is on the banner");
  p.document.hidden = true;
  p.document.fire("visibilitychange");
  check(eq(posted.filter((m) => m.kind === "hid").pop().keys, []), "a hidden page lets its keys go");
})();

(function withoutASinkTheBoardIsTheHUD() {
  const p = loadPage();
  p.hud.load(JSON.parse(JSON.stringify(MSG)));
  const ev = p.hud.state.keyEls[0].fire("pointerdown", { pointerId: 1 });
  check(!ev.defaultPrevented && !p.document.documentElement.classList.contains("touch"), "no sink, no typing");
})();

(function docked() {
  const wide = loadPage({ innerHeight: 900 });
  const posted = [];
  wide.window.webkit = { messageHandlers: { zmkhud: { postMessage: (s) => posted.push(JSON.parse(s)) } } };
  wide.hud.load(JSON.parse(JSON.stringify(MSG)));
  wide.board.style.width = "1600px";
  wide.hud.receive({ kind: "dock", on: true, max_height: 240 });
  const st = wide.hud.state;
  check(wide.document.documentElement.classList.contains("dock"), "docked, the page says so");
  check(Math.abs(st.scale - (240 - 40) / 100) < 1e-9, `a wide board is as tall as the host allows: scale ${st.scale}`);
  check(Math.abs(st.offsetX - (1600 - 400 * st.scale) / 2) < 1e-9, "and centred");
  check(st.gap > 6 && st.gap <= 8, `with gaps that grow with the keys: ${st.gap}`);
  const size = posted.filter((m) => m.kind === "size").pop();
  check(size && size.width === null, "the host decides the width");
  const tall = loadPage();
  tall.hud.load(JSON.parse(JSON.stringify(MSG)));
  tall.board.style.width = "300px";
  tall.hud.setDock({ on: true, max_height: 600 });
  check(Math.abs(tall.hud.state.scale - 300 / 400) < 1e-9 && tall.hud.state.offsetX === 0, "a narrow screen: as wide as it is");
  tall.hud.setDock({ on: false });
  check(!tall.document.documentElement.classList.contains("dock") && tall.hud.state.offsetX === 0, "undocked, as before");
})();

(function aSplitBoardsHalvesGoToTheSides() {
  // Two keys a side, a gap of two keys between the halves.
  const split = JSON.parse(JSON.stringify(MSG));
  split.layout = { width: 600, height: 100, keys: [50, 150, 450, 550].map((x) => ({ x, y: 50, w: 100, h: 100, r: 0 })) };
  const p = loadPage();
  p.hud.load(split);
  p.board.style.width = "1600px";
  p.hud.setDock({ on: true, max_height: 240 });
  const st = p.hud.state, left = (i) => parseFloat(st.keyEls[i].style.left), w = parseFloat(st.keyEls[0].style.width);
  check(Math.abs(left(0) - (st.gap + st.gap / 2)) < 1e-6, `the left half against the left side: ${left(0)}`);
  check(Math.abs(left(3) + w - (1600 - st.gap - st.gap / 2)) < 1e-6, `the right half against the right side: ${left(3) + w}`);
  check(Math.abs(left(1) - left(0) - 100 * st.scale) < 1e-6, "each half keeps its own spacing");
  check(Math.abs(st.scale - 2) < 1e-9, `as tall as the host allows: scale ${st.scale}`);
  p.sent = [];
  p.hud.setTouch({ output: "hid", repeat: "os", send: (m) => p.sent.push(m) });
  const c = st.centers[3];
  p.board.fire("pointerdown", { pointerId: 1, clientX: c.x + 100 * st.scale / 2 + 1, clientY: c.y });
  check(p.sent.some((m) => m.kind === "hid" && m.mods === 2), "a touch at the right half's edge is its key (Shift)");
  p.hud.setDock({ on: true, max_height: 240, split: false });
  check(Math.abs(left(1) + w + st.gap - left(2) - 2 * 100 * st.scale) < 1e-6 || st.offsetX > 0, "split: false keeps the board in one piece, centred");
  const narrow = loadPage();
  narrow.hud.load(split);
  narrow.board.style.width = "600px";
  narrow.hud.setDock({ on: true, max_height: 600 });
  const nl = (i) => parseFloat(narrow.hud.state.keyEls[i].style.left);
  check(nl(2) - nl(1) >= 300 * narrow.hud.state.scale - 1e-6, "on a narrow screen the halves keep the gap the layout draws");
})();

for (const f of fail) console.log("FAIL " + f);
console.log(`the touch keyboard on the page: ${checks} checks, ${fail.length} failures`);
process.exit(fail.length ? 1 : 0);
