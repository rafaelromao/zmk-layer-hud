#!/usr/bin/env node
/* zmk-layer-hud — the touch keyboard's virtual firmware (hud/engine.js).
 *
 *   node hud/tests/engine_test.js [--verbose] [--only NAME]
 *
 * Two halves. As firmware (`touch: null`) the engine runs ZMK's own behaviour tests: each vector in
 * fixtures/zmk_vectors.json is a keymap compiled the way the HUD compiles a keyboard's, a script
 * of presses and releases with ZMK's timings, and the keycode events ZMK's firmware raised in
 * answer (host/zmk_vectors_gen.py writes them from a ZMK checkout). As a touch keyboard it is held
 * to docs/touch.md: a long press holds and balances, a hold let go alone latches and a second tap
 * locks it, two fingers hold for real, a held key repeats where the host does not.
 */
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const ZmkEngine = require("../engine.js");

const VERBOSE = process.argv.includes("--verbose");
const ONLY = process.argv.includes("--only") ? process.argv[process.argv.indexOf("--only") + 1] : null;
const fail = [];
let checks = 0;
function check(ok, what) { checks++; if (!ok) fail.push(what); }
function eq(a, b) { return JSON.stringify(a) === JSON.stringify(b); }

// ---------- firmware: ZMK's own tests ----------

function runVector(v) {
  const eng = ZmkEngine.create(v.actions, { trace: true });
  const got = [];
  const take = (events) => { for (const e of events) if (e.kind === "keycode") got.push(e); };
  for (const ev of v.events) take(ev.down ? eng.down({ pos: ev.pos }, ev.t) : eng.up({ pos: ev.pos }, ev.t));
  // The simulator exits when the script's last wait is over; what is due after that never ran.
  take(eng.tick(v.end));
  return got.map((e) => [e.state, e.page, e.id, e.implicit, e.explicit]);
}

function same(got, want) {
  if (got.length !== want.length) return false;
  return want.every((w, i) => {
    const g = got[i];
    return g[0] === w[0] && (w[1] === null || g[1] === w[1]) && g[2] === w[2] && g[3] === w[3] && g[4] === w[4];
  });
}

function fmt(lines) {
  return lines.map((l) => `${l[0] ? "pressed " : "released"} page ${l[1]} 0x${l[2].toString(16)} imp 0x${l[3].toString(16)} exp 0x${l[4].toString(16)}`).join("\n      ");
}

const vectors = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", "zmk_vectors.json"), "utf8")).vectors;
let vectorsRun = 0;
for (const v of vectors) {
  if (ONLY && !v.name.includes(ONLY)) continue;
  vectorsRun++;
  const got = runVector(v);
  const ok = same(got, v.expect);
  check(ok, `ZMK ${v.name}:\n    want ${fmt(v.expect)}\n    got  ${fmt(got)}`);
  if (VERBOSE && ok) console.log(`ok ${v.name}`);
}

// ---------- the touch keyboard ----------

const K = (id, mods) => ({ page: 7, id, mods: mods || 0 });
const A = 0x04, B = 0x05, N = 0x11, SPACE = 0x2C, LSHIFT = 0xE1, LCTRL = 0xE0;
const kp = (id, mods) => ({ op: "kp", key: K(id, mods) });
const mo = (layer) => ({ op: "mo", layer, locking: false });
const ht = (hold, tap, flavor) => ({ op: "ht", hold, tap, flavor: flavor || "hold-preferred", term: 200, quick_tap: -1,
                                      prior_idle: -1, retro_tap: false, trigger_positions: null, trigger_on_release: false });
const TOUCH = { hold_ms: 280, flavor: "balanced", latch: 1, lock_ms: 350, sticky_ms: 0, repeat_delay_ms: 450, repeat_ms: 60 };

/* A keymap of two layers over five positions:
 *   0: a        1: b        2: &mt LSHIFT N     3: &lt 1 SPACE     4: &kp LSHIFT
 *   layer 1:  0: 1 (N1)  1: trans  2: trans  3: trans  4: &mo 1 is elsewhere: 4 is &sk LCTRL */
function keymap() {
  const table = [kp(A), kp(B), ht(kp(LSHIFT), kp(N)), ht(mo(1), kp(SPACE), "tap-preferred"), kp(LSHIFT),
                 kp(0x1E), { op: "trans" }, { op: "sk", key: K(LCTRL), release_after: 1000, quick_release: false, lazy: false, ignore_mods: true },
                 mo(1)];
  return { v: 1, default: 0, table, layers: { 0: [0, 1, 2, 3, 4, 8], 1: [5, 6, 6, 6, 7, 6] }, combos: [], conditional: [] };
}

function session(opts) {
  const eng = ZmkEngine.create(keymap(), Object.assign({ touch: TOUCH }, opts || {}));
  const reports = [];
  let t = 0;
  const take = (events) => { for (const e of events) if (e.kind === "report") reports.push({ mods: e.mods, keys: e.keys }); return events; };
  return {
    eng, reports,
    down(pos, at) { t = at; return take(eng.down({ pos }, t)); },
    up(pos, at) { t = at; return take(eng.up({ pos }, t)); },
    tick(at) { t = at; return take(eng.tick(t)); },
    typed() { return reports.filter((r) => r.keys.length).map((r) => [r.mods, r.keys.slice()]); },
    // What the host types: each key as it goes down, with the modifiers then.
    presses() {
      const out = [];
      let before = [];
      for (const r of reports) {
        for (const k of r.keys) if (!before.includes(k)) out.push([r.mods, k]);
        before = r.keys;
      }
      return out;
    },
    last() { return reports[reports.length - 1] || { mods: 0, keys: [] }; },
  };
}

(function aTapTypes() {
  const s = session();
  s.down(0, 0); s.up(0, 50);
  check(eq(s.typed(), [[0, [A]]]) && eq(s.last(), { mods: 0, keys: [] }), "a tap on &kp A types a and lets go");
})();

(function aLongPressHoldsAndBalances() {
  const s = session();
  s.down(2, 0); s.tick(250);
  check(eq(s.reports, []), "a hold-tap is undecided before touch.hold_ms (280), not the keymap's 200");
  s.down(0, 300);   // a second finger types a while the first holds
  s.up(0, 330); s.up(2, 400);
  check(eq(s.typed(), [[2, [A]]]), `a held mod-tap with a second finger types A: ${JSON.stringify(s.typed())}`);
  check(s.eng.state().latches.length === 0, "a hold used by another finger is not latched");
})();

(function aRollStaysLetters() {
  const s = session();
  s.down(2, 0); s.down(0, 60); s.up(2, 90); s.up(0, 120);   // balanced: the mod-tap lifts first
  check(eq(s.presses(), [[0, N], [0, A]]), `two thumbs rolling over a mod-tap type n then a: ${JSON.stringify(s.presses())}`);
})();

(function aHoldLetGoAloneLatches() {
  const s = session();
  s.down(2, 0); s.tick(300); s.up(2, 400);
  check(s.last().mods === 2, "a mod-tap held alone keeps Shift after it lifts");
  check(eq(s.eng.state().latches, [{ pos: 2, kind: "mod", state: "oneshot" }]), "and says it is one-shot");
  s.down(1, 1000); s.up(1, 1050);
  check(eq(s.typed(), [[2, [B]]]), `the next key is shifted: ${JSON.stringify(s.typed())}`);
  check(eq(s.last(), { mods: 0, keys: [] }) && !s.eng.state().latches.length, "and the one-shot goes with it");
})();

(function aSecondTapLocks() {
  const s = session();
  s.down(4, 0); s.up(4, 40);          // &kp LSHIFT tapped: one-shot
  s.down(4, 200); s.up(4, 240);       // again within lock_ms: locked
  check(eq(s.eng.state().latches, [{ pos: 4, kind: "mod", state: "locked" }]), "a second tap within lock_ms locks");
  s.down(0, 500); s.up(0, 540); s.down(1, 600); s.up(1, 640);
  check(eq(s.typed(), [[2, [A]], [2, [B]]]), `locked Shift shifts every key: ${JSON.stringify(s.typed())}`);
  s.down(4, 900); s.up(4, 940);
  check(s.last().mods === 0 && !s.eng.state().latches.length, "a third tap lets it go");
})();

(function aMomentaryLayerTappedIsOneShot() {
  const s = session();
  s.down(5, 0); s.up(5, 40);          // &mo 1 tapped
  check(eq(s.eng.state().layers, [1, 0]), "a tapped &mo leaves its layer on");
  s.down(0, 300); s.up(0, 340);       // layer 1's key 0 is N1
  check(eq(s.typed(), [[0, [0x1E]]]), `the next key types from that layer: ${JSON.stringify(s.typed())}`);
  check(eq(s.eng.state().layers, [0]), "and the layer goes after it");
})();

(function aLayerTapHeldAloneLatchesItsLayer() {
  const s = session();
  s.down(3, 0); s.tick(300); s.up(3, 500);
  check(eq(s.eng.state().layers, [1, 0]), "&lt held alone: its layer stays on");
  s.down(0, 900); s.up(0, 950);
  check(eq(s.typed(), [[0, [0x1E]]]) && eq(s.eng.state().layers, [0]), "one key from it, then it goes");
})();

(function aStickyKeyDoubleTapLocks() {
  const s = session();
  s.down(5, 0); s.up(5, 30);                         // to layer 1, one-shot
  s.down(4, 100); s.up(4, 130);                      // &sk LCTRL on layer 1: one-shot Ctrl
  s.down(4, 250); s.up(4, 280);                      // again: locked (layer 1 still on: the &mo latch was not used)
  const latched = s.eng.state().latches.find((l) => l.pos === 4);
  check(latched && latched.state === "locked", `a sticky key tapped twice locks: ${JSON.stringify(s.eng.state().latches)}`);
})();

(function aHeldKeyRepeatsWhereTheHostDoesNot() {
  const s = session({ sink: { repeat: "synthetic", pages: [7, 12] } });
  s.down(0, 0); s.tick(450 + 60 * 3 + 1);
  const presses = s.presses().length;
  check(presses === 1 + 4, `a held key repeats after 450 ms every 60 ms: ${presses} presses`);
  s.up(0, 700);
  const lifted = s.presses().length;
  s.tick(2000);
  check(s.presses().length === lifted, "and stops when it lifts");
  const os = session();
  os.down(0, 0); os.tick(2000);
  check(os.reports.length === 1, "an `os` sink just sees the key stay down");
})();

(function releaseAllLetsEverythingGo() {
  const s = session();
  s.down(2, 0); s.down(0, 10);
  const events = s.eng.releaseAll(20);
  check(eq(s.eng.state().down, []) && s.eng.state().idle, "releaseAll leaves nothing down");
  check(!events.some((e) => e.kind === "report" && e.keys.length), "and an undecided hold-tap types nothing");
})();

(function adoptTakesTheBoardsLayersOnlyWhenIdle() {
  const s = session();
  s.eng.adopt([1], 0);
  check(eq(s.eng.state().layers, [1, 0]), "idle, the engine takes the keyboard's layers");
  s.down(0, 10);
  s.eng.adopt([], 20);
  check(eq(s.eng.state().layers, [1, 0]), "not while a finger is down");
})();

(function deadlinesAreForTicks() {
  const eng = ZmkEngine.create(keymap(), { touch: TOUCH });
  check(eng.nextDeadline() === null, "nothing pending: no deadline");
  eng.down({ pos: 2 }, 1000);
  check(eng.nextDeadline() === 1280, `a hold-tap's deadline is its hold_ms: ${eng.nextDeadline()}`);
})();

(function unsupportedSays() {
  const eng = ZmkEngine.create({ v: 1, default: 0, table: [{ op: "unsupported", why: "zmk,behavior-leader-key", raw: "&leader" }],
                                 layers: { 0: [0] }, combos: [], conditional: [] }, { touch: TOUCH });
  const events = eng.down({ pos: 0 }, 0);
  check(events.some((e) => e.kind === "unsupported" && e.why === "zmk,behavior-leader-key"), "an unsupported key says why");
})();

(function aChipFiresItsCombo() {
  const m = keymap();
  m.combos = [{ positions: [0, 1], layers: [0], action: 1, term: 30, idle: 0, slow_release: false, drawn: 0 }];
  const eng = ZmkEngine.create(m, { touch: TOUCH });
  const e1 = eng.down({ combo: 0 }, 0).concat(eng.up({ combo: 0 }, 40));
  check(e1.some((e) => e.kind === "report" && eq(e.keys, [B])), "a chip types its combo's action");
  eng.adopt([1], 50);
  const e2 = eng.down({ combo: 0 }, 60);
  check(!e2.some((e) => e.kind === "report"), "not on a layer the combo is not on");
})();

for (const f of fail) console.log("FAIL " + f);
console.log(`${vectorsRun} of ZMK's behaviour tests run as firmware, ${checks} checks, ${fail.length} failures`);
process.exit(fail.length ? 1 : 0);
