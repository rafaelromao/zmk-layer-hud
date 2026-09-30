#!/usr/bin/env node
/* zmk-layer-hud — the heatmap, on the tests' clock.
 *
 *   node hud/tests/stats_test.js [--keymap FILE] [--verbose]
 *
 * A key the keyboard presses glows and cools over hud.heatmap_ms (hud.js, "heat"). What is read
 * back is what a viewer sees: each key's --heat, the opacity of the overlay hud.css draws on it.
 * The expectations are stated from that behaviour -- warm after a press, cold once its time is
 * up, warmer for a key struck often -- and not from the constants hud.js happens to use, so a
 * retuned curve still passes and a glow that never goes out does not.
 */
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { loadPage } = require("./dom.js");

const FIXTURE = path.join(__dirname, "fixtures", "diamond.json");
const SPACED_MS = 100;    // between presses that must stay separate keystrokes: past any combo term

function main() {
  const argv = process.argv.slice(2);
  const keymap = argv.includes("--keymap") ? argv[argv.indexOf("--keymap") + 1] : FIXTURE;
  const verbose = argv.includes("--verbose") || argv.includes("-v");
  const data = JSON.parse(fs.readFileSync(keymap, "utf8"));
  const fadeMs = (data.hud && data.hud.heatmap_ms) || 3000;

  const fail = [];
  let checked = 0;
  const check = (name, ok, detail) => {
    checked++;
    if (!ok) fail.push(name + (detail === undefined ? "" : ": " + detail));
    else if (verbose) console.log("ok   " + name);
  };

  // A page with this keymap, the keyboard on its base layer. `hud` overrides the config's timings.
  const fresh = (hud, search) => {
    const page = loadPage({ search });
    page.hud.load(Object.assign({}, data, { hud: Object.assign({}, data.hud, hud) }));
    page.hud.setLayers([]);
    return page;
  };
  const heat = (page, idx) => page.hud.state.keyEls[idx].style.getPropertyValue("--heat");
  const level = (page, idx) => { const v = heat(page, idx); return v === "" ? 0 : Number(v); };
  const tap = (page, pos) => { page.hud.pressAt(pos); page.clock.advance(SPACED_MS); page.hud.releaseAt(pos); };
  const warmKeys = page => page.hud.state.keyEls.map((e, i) => (heat(page, i) !== "" ? i : -1)).filter(i => i >= 0);

  // Three keys the keyboard can report, on the base layer: ZMK position and drawer index.
  const map = data.positions || {};
  const known = Object.keys(map).length
    ? Object.entries(map).map(([pos, idx]) => ({ pos: Number(pos), idx }))
    : data.layout.keys.map((k, idx) => ({ pos: idx, idx }));
  const onBase = known.filter(k => { const b = data.layers[data.base][k.idx]; return b && b.type !== "trans" && b.tap; });
  if (onBase.length < 3) { console.error(`stats_test: ${keymap} has fewer than three base keys with a position`); process.exit(2); }
  const [A, B, C] = onBase;

  // ---------- a press, and its fade ----------
  {
    const p = fresh();
    p.hud.pressAt(A.pos);
    const first = level(p, A.idx);
    check("a press warms its key", first > 0, heat(p, A.idx));
    check("and no other", warmKeys(p).length === 1, JSON.stringify(warmKeys(p)));
    check("at an opacity the legends survive", first <= 0.6, first);
    p.clock.advance(SPACED_MS);
    p.hud.releaseAt(A.pos);
    p.clock.advance(fadeMs / 2);
    const half = level(p, A.idx);
    check("half way through it is still warm, and cooler", half > 0 && half < first, `${first} -> ${half}`);
    p.clock.advance(fadeMs / 2 + 200);
    check("a key pressed once is cold after heatmap_ms", heat(p, A.idx) === "", heat(p, A.idx));
    check("and forgotten", p.hud.state.heat.size === 0, p.hud.state.heat.size);
    check("with nothing left scheduled", p.clock.pending() === 0, p.clock.pending());
  }

  // ---------- struck often ----------
  {
    const p = fresh();
    tap(p, B.pos);
    const once = level(p, B.idx);
    for (let i = 0; i < 11; i++) tap(p, A.pos);
    const often = level(p, A.idx);
    check("a key struck often is warmer than one struck once", often > once, `${often} vs ${once}`);
    check("and never past the cap", often <= 0.6, often);
    p.clock.advance(fadeMs);
    check("it stays warm after one struck once has cooled", heat(p, A.idx) !== "" && heat(p, B.idx) === "",
          `A ${heat(p, A.idx) || "cold"}, B ${heat(p, B.idx) || "cold"}`);
    p.clock.advance(fadeMs * 3);
    check("and cools in the end", warmKeys(p).length === 0 && p.clock.pending() === 0,
          `${warmKeys(p).length} warm, ${p.clock.pending()} pending`);
  }

  // ---------- recency ----------
  {
    const p = fresh();
    tap(p, A.pos);
    p.clock.advance(fadeMs / 3);
    tap(p, B.pos);
    check("a key pressed recently is warmer than one pressed before it",
          level(p, B.idx) > level(p, A.idx) && level(p, A.idx) > 0, `${level(p, B.idx)} vs ${level(p, A.idx)}`);
  }

  // ---------- one timer ----------
  {
    const p = fresh();
    for (const k of [A, B, C]) tap(p, k.pos);
    p.clock.advance(SPACED_MS * 2);               // the releases' own timers have run out
    check("one timer drives every warm key", p.clock.pending() === 1, `${p.clock.pending()} pending, ${warmKeys(p).length} warm`);
    p.clock.advance(fadeMs + 500);
    check("and it stops once they are cold", p.clock.pending() === 0 && p.hud.state.heatTimer === null, p.clock.pending());
  }

  // ---------- the board is rebuilt ----------
  {
    const p = fresh();
    tap(p, A.pos);
    const before = p.hud.state.keyEls[A.idx];
    p.hud.load(data);                             // what a keymap reload and every reconnect do
    check("the board was rebuilt", p.hud.state.keyEls[A.idx] !== before);
    check("and the glow survived it", heat(p, A.idx) !== "", "cold after the reload");
  }

  // ---------- turned off ----------
  {
    const p = fresh({ heatmap_ms: 0 });
    tap(p, A.pos);
    check("heatmap_ms: 0 warms nothing", warmKeys(p).length === 0 && p.hud.state.heat.size === 0, JSON.stringify(warmKeys(p)));
    p.clock.advance(1000);
    check("and schedules nothing", p.clock.pending() === 0 && p.hud.state.heatTimer === null, p.clock.pending());
  }

  // ---------- keys lit from reports alone ----------
  {
    // A letter on the base layer that only one base key types.
    const base = data.layers[data.base];
    const letter = base.map(k => k.tap).find(t => typeof t === "string" && /^\p{Ll}$/u.test(t) &&
                                                  base.filter(k => k.tap === t).length === 1);
    const idx = base.findIndex(k => k.tap === letter);
    const typed = () => ({ type: "keyDown", chars: letter, name: letter, code: 0, flags: {}, repeat: false });
    if (letter) {
      const p = fresh();
      p.hud.key(typed());
      check(`a key lit from a report warms while no position has come (${letter})`, heat(p, idx) !== "", "cold");

      // Once positions come, a report that beats its own position lights a guess the position
      // takes down; the glow would outlive it by seconds, so a guess does not warm anything.
      const q = fresh();
      const other = [A, B, C].find(k => k.idx !== idx);
      tap(q, other.pos);
      q.clock.advance(Math.max(fadeMs, (data.hud && data.hud.positions_fresh_ms) || 3000) + 200);
      q.hud.key(typed());
      const lit = q.hud.state.keyEls[idx].classList.contains("pressed");
      check("a guess lit once positions have come warms nothing", lit && warmKeys(q).length === 0,
            `${lit ? "lit" : "not lit"}, warm ${JSON.stringify(warmKeys(q))}`);
    }
  }

  // ---------- a GIF still ----------
  {
    const p = fresh({}, "?keymap=keymap.json&demo=0");
    check("a still marks the page", p.document.body.classList.contains("demo"));
    p.hud.pressAt(A.pos);
    check("and warms nothing, whatever it presses", warmKeys(p).length === 0 && p.hud.state.heat.size === 0, JSON.stringify(warmKeys(p)));
  }

  for (const f of fail) console.log("FAIL " + f);
  console.log(`${checked} heat checks, ${fail.length} failures`);
  process.exit(fail.length ? 1 : 0);
}

main();
