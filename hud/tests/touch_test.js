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
  touch: { enabled: 1, key_mm: 18, hold_ms: 280, flavor: "balanced", latch: 1, lock_ms: 350, sticky_ms: 0,
           repeat_delay_ms: 450, repeat_ms: 60, combos: "chips", heat: 1 },
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
  check(p.document.getElementById("keys").children.length === 0, "nor in the typed-keys strip");
  check(p.document.documentElement.classList.contains("touch"), "which the board typing puts away (html.touch #keys)");
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

(function aTouchKeyboardsKeysAreTheirRealSize() {
  // 5 CSS pixels a millimetre and keys 18 mm apart: 1u, 100 in this layout, is 90 px, whatever the room.
  const html = (p) => p.document.documentElement;
  const sub = (p) => p.document.getElementById("layerSub").textContent;
  const p = page();
  const posted = [];
  p.window.webkit = { messageHandlers: { zmkhud: { postMessage: (s) => posted.push(JSON.parse(s)) } } };
  p.board.style.width = "1578px";
  p.hud.receive({ kind: "dock", on: true, max_height: 600, px_per_mm: 5, width: 1600 });
  const st = p.hud.state, left = (q, i) => parseFloat(q.hud.state.keyEls[i].style.left);
  check(Math.abs(st.scale - 0.9) < 1e-9, `keys 18 mm apart at 5 px a mm, never stretched to the room: scale ${st.scale}`);
  check(Math.abs(left(p, 1) - left(p, 0) - 90) < 1e-6, `90 px from key to key: ${left(p, 1) - left(p, 0)}`);
  check(html(p).classList.contains("touch") && !posted.some((m) => m.kind === "dock"), "it fits, and types");
  const mx = JSON.parse(JSON.stringify(MSG));
  mx.touch.key_mm = 19.05;
  p.hud.load(mx);
  check(Math.abs(p.hud.state.scale - 19.05 * 5 / 100) < 1e-9, `MX's 19.05 mm: scale ${p.hud.state.scale}`);

  // A screen too narrow for it: a HUD only, fitted, typing nothing, and the panel told.
  const narrow = page();
  const said = [];
  narrow.window.webkit = { messageHandlers: { zmkhud: { postMessage: (s) => said.push(JSON.parse(s)) } } };
  narrow.board.style.width = "278px";
  narrow.hud.receive({ kind: "dock", on: true, max_height: 600, px_per_mm: 5, width: 300 });
  check(!html(narrow).classList.contains("touch"), "too wide for the screen: no touch keyboard");
  check(Math.abs(narrow.hud.state.scale - 278 / 400) < 1e-9, `the HUD, fitted to the screen: scale ${narrow.hud.state.scale}`);
  narrow.sent.length = 0;          // what was let go as the keyboard stopped
  narrow.tap(0);
  check(narrow.reports().length === 0, `a tap types nothing: ${JSON.stringify(narrow.reports())}`);
  const refused = said.filter((m) => m.kind === "dock");
  check(refused.length === 1 && eq(refused[0], { kind: "dock", fits: false, key_mm: 18, need_mm: [72, 26], room_mm: [56, 120] }),
        `the panel is told why, once: ${JSON.stringify(refused)}`);
  check(/a HUD only/.test(sub(narrow)) && /72 × 26 mm/.test(sub(narrow)), `and the banner says it: ${sub(narrow)}`);
  narrow.hud.receive({ kind: "dock", on: false });
  check(html(narrow).classList.contains("touch") && !/HUD only/.test(sub(narrow)), "floating again, nothing is refused");

  // Too tall for the dock's share of the screen: the same.
  const short = page();
  short.board.style.width = "1578px";
  short.hud.receive({ kind: "dock", on: true, max_height: 120, px_per_mm: 5, width: 1600 });
  check(!html(short).classList.contains("touch"), "taller than the dock may be: no touch keyboard");
  // A keymap of a board that does fit, docked as it is: a keyboard again.
  const small = JSON.parse(JSON.stringify(MSG));
  small.layout = { width: 400, height: 50, keys: [0, 1, 2, 3].map((i) => ({ x: 50 + 100 * i, y: 25, w: 100, h: 50, r: 0 })) };
  short.hud.load(small);
  check(html(short).classList.contains("touch") && Math.abs(short.hud.state.scale - 0.9) < 1e-9,
        `a board that fits docks at its real size: scale ${short.hud.state.scale}`);
})();

(function attachDetachAndMinimize() {
  const html = (p) => p.document.documentElement;
  const btn = (p, id) => p.document.getElementById(id);
  // In a panel: its banner asks it to dock the HUD as a touch keyboard, or to float it again.
  const p = page();
  const posted = [];
  p.window.webkit = { messageHandlers: { zmkhud: { postMessage: (s) => posted.push(JSON.parse(s)) } } };
  p.hud.relayout();
  check(html(p).classList.contains("attachable"), "a panel's page offers attach");
  btn(p, "attach").click();
  check(eq(posted.filter((m) => m.kind === "attach"), [{ kind: "attach", on: true }]), "floating, it asks to be docked");
  p.hud.receive({ kind: "dock", on: true, max_height: 600, px_per_mm: 5, width: 1600 });
  btn(p, "attach").click();
  check(eq(posted.filter((m) => m.kind === "attach").pop(), { kind: "attach", on: false }), "docked, to float again");
  check(btn(p, "attach").getAttribute("aria-pressed") === "true", "and says it is attached");
  // Minimized: still docked and still the keyboard, down to its banner; the panel is told the new height.
  posted.length = 0;
  btn(p, "mini").click();
  check(html(p).classList.contains("mini") && html(p).classList.contains("dock"), "minimize keeps it docked, a bar");
  check(posted.some((m) => m.kind === "size"), "and the panel hears how tall it is now");
  p.sent.length = 0;
  p.tap(0);
  check(p.reports().length === 0, `a bar types nothing: ${JSON.stringify(p.reports())}`);
  btn(p, "mini").click();
  check(!html(p).classList.contains("mini"), "maximize brings the keys back");
  p.tap(0);
  check(eq(p.reports(), [[0, [4]], [0, []]]), `and they type: ${JSON.stringify(p.reports())}`);
  btn(p, "mini").click();
  p.hud.receive({ kind: "dock", on: false });
  check(!html(p).classList.contains("mini"), "floating, nothing is minimized");
  // A page with nothing to dock it offers no attach (the landing page's demo, a browser on the socket).
  const bare = page();
  bare.hud.relayout();
  check(!html(bare).classList.contains("attachable"), "no panel, no attach");
})();

(function theStatsShownOrHiddenTheBoardIsDrawnToItsWidth() {
  // A browser's window stays as it is when the column comes or goes, so the board, narrower or wider
  // beside it, is drawn again at its own width: never under the column.
  const p = page();
  const right = () => Math.max(...p.hud.state.keyEls.map((e) => parseFloat(e.style.left) + parseFloat(e.style.width)));
  p.board.style.width = "800px";
  p.hud.setPref("side", "hidden");
  p.clock.advance(200);
  check(right() > 700, `the column hidden, the board as wide as it is: ${right()}`);
  p.board.style.width = "400px";
  p.hud.setPref("side", "shown");
  p.clock.advance(200);
  check(right() <= 400, `the column shown, no key reaches under it: ${right()}`);
})();

(function aTouchKeyboardWaitsForNoKeyboard() {
  const p = page();
  check(!/waiting for the keyboard/.test(p.document.getElementById("feed").textContent),
        `no keyboard plugged in is no matter to a touch keyboard: ${p.document.getElementById("feed").textContent}`);
  const hud = loadPage();
  hud.hud.load(JSON.parse(JSON.stringify(MSG)));
  check(/waiting for the keyboard/.test(hud.document.getElementById("feed").textContent), "the HUD still waits for one");
})();

(function aKeymapThatSaysNothingOfItsKeysIsAHud() {
  // As the site's boards come (site/build.py leaves out what keys do): drawn, and saying it cannot type.
  const bare = JSON.parse(JSON.stringify(MSG));
  delete bare.actions;
  delete bare.touch;
  const p = loadPage();
  p.hud.load(bare);
  p.hud.setTouch({ output: "hid", repeat: "os", send: () => {} });
  check(!p.document.documentElement.classList.contains("touch"), "no actions: not a keyboard");
  check(/a HUD only · this keymap says nothing of what its keys do/.test(p.document.getElementById("layerSub").textContent),
        `and the banner says why: ${p.document.getElementById("layerSub").textContent}`);
  p.hud.load(JSON.parse(JSON.stringify(MSG)));
  check(p.document.documentElement.classList.contains("touch") && !/HUD only/.test(p.document.getElementById("layerSub").textContent),
        "a keymap that says types");
})();

(function inABrowserTouchAttachesAlongTheBottom() {
  // Development: ?touch from a local server is the touch keyboard docked along the window's bottom,
  // its keys real size at &mm= pixels a millimetre, typing into the console.
  const p = loadPage({ search: "?touch&mm=5", protocol: "http:", innerWidth: 1600, innerHeight: 1000 });
  const typed = [];
  p.window.console = Object.assign({}, console, { log: (...a) => typed.push(a.join(" ")) });
  p.hud.load(JSON.parse(JSON.stringify(MSG)));
  const html = p.document.documentElement;
  check(html.classList.contains("dock") && html.classList.contains("bottom") && html.classList.contains("touch"),
        `docked along the bottom, typing: ${html.className}`);
  check(Math.abs(p.hud.state.scale - 0.9) < 1e-9, `real size at 5 px a mm: ${p.hud.state.scale}`);
  check(html.classList.contains("attachable"), "with attach and detach");
  p.hud.state.keyEls[0].fire("pointerdown", { pointerId: 1, button: 0 });
  p.hud.state.keyEls[0].fire("pointerup", { pointerId: 1 });
  check(typed.some((t) => /"keys":\[4\]/.test(t)), `a click types, into the console: ${JSON.stringify(typed)}`);
  p.document.getElementById("attach").click();
  check(!html.classList.contains("dock") && !html.classList.contains("bottom") && !html.classList.contains("touch"),
        "detached: the floating HUD, typing nothing");
  p.document.getElementById("attach").click();
  check(html.classList.contains("dock") && html.classList.contains("touch"), "and attached again");
})();

// The feedback: a stand-in AudioContext that says which click each source played, and how loud.
function audioPage(touch, sinkExtra, floating) {
  const played = [], buzzed = [];
  class Audio {
    constructor() { this.sampleRate = 8000; this.state = "suspended"; this.destination = {}; }
    resume() { this.state = "running"; }
    createBuffer(ch, n) { const d = new Float32Array(n); return { length: n, getChannelData: () => d }; }
    createGain() { return { gain: { value: 1 }, connect() {} }; }
    createBufferSource() {
      const src = { buffer: null, connect: (g) => { src.gain = g; }, start: () => played.push(src) };
      return src;
    }
  }
  const m = JSON.parse(JSON.stringify(MSG));
  m.layers.base[1] = K("⌫");
  m.actions.table.push(kp(0x2A));
  m.actions.layers[0][1] = m.actions.table.length - 1;
  Object.assign(m.touch, { sound: 30, haptics: 1 }, touch || {});     // host/keymap.py's defaults, then the test's
  const p = loadPage({ AudioContext: Audio, navigator: { vibrate: (ms) => buzzed.push(ms) } });
  p.hud.load(m);
  p.hud.setLayers([]);
  p.hud.setTouch(Object.assign({ output: "hid", repeat: "os", send: () => {} }, sinkExtra || {}));
  // Docked unless asked: a touch screen's keyboard, where the feedback is on until switched off.
  if (!floating) p.hud.setDock({ on: true, max_height: 300 });
  p.tap = (idx) => { p.hud.state.keyEls[idx].fire("pointerdown", { pointerId: idx + 1 }); p.clock.advance(30);
                     p.board.fire("pointerup", { pointerId: idx + 1 }); };
  return Object.assign(p, { played, buzzed });
}

(function aTapClicksAndBuzzesTheMomentAFingerLands() {
  const p = audioPage();
  p.hud.state.keyEls[0].fire("pointerdown", { pointerId: 1 });
  check(p.played.length === 1 && p.buzzed.length === 1, "on the press, not the release: a click and a buzz");
  p.board.fire("pointerup", { pointerId: 1 });
  check(p.played.length === 1, "and nothing more when the finger lifts");
  p.tap(1);
  const [a, del] = p.played;
  check(a.buffer !== del.buffer, "Backspace has a click of its own");
  check(Math.abs(a.gain.gain.value - 0.3 * 0.6) < 1e-9, `as loud as touch.sound says: ${a.gain.gain.value}`);
  check(eq(p.buzzed, [8, 12]), `a short buzz, a little longer for the special keys: ${JSON.stringify(p.buzzed)}`);
  const quiet = audioPage({ sound: 0, haptics: 0 });
  quiet.tap(0);
  check(quiet.played.length === 0 && quiet.buzzed.length === 0, "touch.sound 0 and touch.haptics 0: neither");
  const said = [];
  const native = audioPage({}, { feedback: (kind) => said.push(kind) });
  native.tap(0);
  native.tap(1);
  check(eq(said, ["standard", "delete"]) && native.played.length === 0 && native.buzzed.length === 0,
        `a sink with feedback of its own is asked instead: ${JSON.stringify(said)}`);
})();

(function theFeedbackIsOnForATouchScreenOffForTheHudAndSwitched() {
  const sw = (p) => p.document.getElementById("stats").querySelector(".switch");
  const tile = (p) => p.document.getElementById("stats").querySelector(".feedback");
  const floating = audioPage({}, null, true);
  floating.tap(0);
  check(floating.played.length === 0 && floating.buzzed.length === 0, "undocked (a page typing for development: &touch) it is quiet by default");
  check(!tile(floating).classList.contains("off") && sw(floating).getAttribute("aria-checked") === "false",
        "and its side panel has the switch, off");
  sw(floating).click();
  floating.tap(0);
  check(floating.played.length === 1 && sw(floating).getAttribute("aria-checked") === "true", "switched on, it clicks");

  const docked = audioPage();
  check(sw(docked).getAttribute("aria-checked") === "true", "docked, the switch starts on");
  sw(docked).click();
  docked.tap(0);
  check(docked.played.length === 0 && docked.buzzed.length === 0, "switched off, a touch is silent");
  check(docked.hud.state.prefs.feedback === "off", "and the choice is kept, as the theme's is");
  docked.hud.setDock({ on: false });
  docked.tap(0);
  check(docked.played.length === 0, "a choice made holds in either mode");

  const native = audioPage({}, { feedback: () => {} });
  native.hud.setPref("feedback", "off");
  let asked = 0;
  native.hud.setTouch({ output: "hid", repeat: "os", send: () => {}, feedback: () => asked++ });
  native.tap(0);
  check(asked === 0, "switched off, a shell's own feedback is not asked either");
  native.hud.setTouch(null);
  check(tile(native).classList.contains("off"), "and a board that does not type has no switch");
})();

(function theStatsSitBetweenTheHalvesWhenTheyFit() {
  const split = JSON.parse(JSON.stringify(MSG));
  split.layout = { width: 600, height: 100, keys: [50, 150, 450, 550].map((x) => ({ x, y: 50, w: 100, h: 100, r: 0 })) };
  const root = (p) => p.document.documentElement;
  const p = loadPage();
  p.hud.load(split);
  p.board.style.width = "1600px";
  p.hud.setDock({ on: true, max_height: 240 });
  const st = p.hud.state, stats = p.document.getElementById("stats");
  check(root(p).classList.contains("stats-middle"), "a wide screen's split board has the stats between its halves");
  const leftHalfEnd = parseFloat(st.keyEls[1].style.left) + parseFloat(st.keyEls[1].style.width);
  const rightHalfStart = parseFloat(st.keyEls[2].style.left);
  const sl = parseFloat(stats.style.left);
  check(sl > leftHalfEnd && sl + 188 < rightHalfStart, `between them: ${leftHalfEnd.toFixed(0)} < ${sl.toFixed(0)}..${(sl + 188).toFixed(0)} < ${rightHalfStart.toFixed(0)}`);
  check(Math.abs((sl + 94) - (leftHalfEnd + rightHalfStart) / 2) < st.gap, "in the middle");
  check(stats.style.maxHeight === Math.ceil(100 * st.scale) + "px", `no taller than the board: ${stats.style.maxHeight}`);
  // A column taller than the board: the strip grows to hold it, within the room the host allows.
  const tall = loadPage();
  tall.hud.load(split);
  tall.board.style.width = "1600px";
  const tallStats = tall.document.getElementById("stats");
  tallStats.scrollHeight = 330;
  tall.hud.setDock({ on: true, max_height: 600 });
  check(tall.board.style.height === "330px" && tallStats.style.maxHeight === "330px",
        `the column whole, the strip as tall as it: ${tall.board.style.height}, ${tallStats.style.maxHeight}`);
  tallStats.scrollHeight = 900;
  tall.hud.setDock({ on: true, max_height: 600 });
  check(tall.board.style.height === "560px", `and no taller than the host allows: ${tall.board.style.height}`);
  p.hud.setDock({ on: false });
  check(!root(p).classList.contains("stats-middle") && stats.style.left === "", "floating again, the column goes back to the side");

  const narrow = loadPage();
  narrow.hud.load(split);
  narrow.board.style.width = "600px";            // the halves ~195 px apart: less than the column and its margins
  narrow.hud.setDock({ on: true, max_height: 240 });
  check(!narrow.document.documentElement.classList.contains("stats-middle"), "a gap narrower than the column keeps it away");

  const tablet = loadPage({ search: "?embed" });
  tablet.hud.load(split);
  tablet.board.style.width = "1600px";
  tablet.hud.setDock({ on: true, max_height: 240 });
  check(!tablet.document.documentElement.classList.contains("stats-middle"), "and a tablet's keyboard page has none");
})();

// The same board with a combo on its first two keys -- a and b together are Escape, on the base layer
// alone -- for the chips: a copy, since a combo would make the tests above that roll a and b chord.
function chipPage(combos) {
  const m = JSON.parse(JSON.stringify(MSG));
  m.combos = [{ positions: [0, 1], key: K("⎋"), layers: ["base"] }];
  m.actions.table.push(kp(0x29));
  m.actions.combos = [{ positions: [0, 1], layers: [0], action: m.actions.table.length - 1, term: 50, idle: 0,
                        slow_release: false, drawn: 0 }];
  m.touch.combos = combos || "chips";
  const p = loadPage();
  p.hud.load(m);
  p.hud.setLayers([]);
  p.sent = [];
  p.hud.setTouch({ output: "hid", repeat: "os", send: (msg) => p.sent.push(msg) });
  p.chips = () => p.board.querySelectorAll(".combo-chip");
  p.reports = () => p.sent.filter((msg) => msg.kind === "hid").map((msg) => [msg.mods, msg.keys]);
  return p;
}

(function aComboIsAChipBetweenItsKeys() {
  const p = chipPage();
  const chips = p.chips();
  check(chips.length === 1, `one chip for the one combo on this layer: ${chips.length}`);
  const c = chips[0];
  const left = parseFloat(c.style.left), w = parseFloat(c.style.width);
  const st = p.hud.state;
  check(Math.abs(left + w / 2 - (st.centers[0].x + st.centers[1].x) / 2) < 1e-6, "between its keys");
  check(w >= 18 && w <= 34, `as big as a little of a key: ${w}`);
  check(c.textContent === "⎋", `with the combo's legend: ${c.textContent}`);
  const ev = c.fire("pointerdown", { pointerId: 5 });
  check(ev.defaultPrevented, "a touch on it is the board's");
  const lit = p.chips()[0];
  check(lit.classList.contains("pressed") && st.keyEls[0].classList.contains("pressed") && st.keyEls[1].classList.contains("pressed"),
        "held, the chip and both its keys light");
  p.clock.advance(40);
  p.board.fire("pointerup", { pointerId: 5 });
  check(eq(p.reports(), [[0, [0x29]], [0, []]]), `a tap on it is the combo: ${JSON.stringify(p.reports())}`);
  check(!st.keyEls[0].classList.contains("pressed") && !p.chips()[0].classList.contains("pressed"), "let go, all dark again");
})();

(function noChipsOffItsLayerOrWhenTheConfigSaysNot() {
  const p = chipPage();
  p.hud.state.keyEls[2].fire("pointerdown", { pointerId: 1 });
  p.clock.advance(300);
  check(p.hud.state.touch.ids[0] === 1 && p.chips().length === 0, "on nav, which the combo is not on, there is no chip");
  p.board.fire("pointerup", { pointerId: 1 });
  // A hold let go alone is a one-shot: the next key is nav's, and then the base is back.
  check(p.chips().length === 0, "nav latched for one key, still none");
  p.hud.state.keyEls[0].fire("pointerdown", { pointerId: 2 });
  p.clock.advance(30);
  p.board.fire("pointerup", { pointerId: 2 });
  check(p.chips().length === 1, "back on the base, it is there again");
  check(chipPage("chords").chips().length === 0, "touch.combos: chords, the default, draws none");
  const q = chipPage();
  q.hud.setTouch(null);
  check(q.chips().length === 0, "and a board that does not type has none");
})();

for (const f of fail) console.log("FAIL " + f);
console.log(`the touch keyboard on the page: ${checks} checks, ${fail.length} failures`);
process.exit(fail.length ? 1 : 0);
