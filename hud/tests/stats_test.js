#!/usr/bin/env node
/* zmk-layer-hud — the heatmap, the counts and the stats bar, on the tests' clock.
 *
 *   node hud/tests/stats_test.js [--keymap FILE] [--verbose]
 *
 * A key the keyboard presses glows and cools over hud.heatmap_ms (hud.js, "heat"); every
 * keystroke the board draws is counted once nothing can take it back (the "ledger"); typing is
 * timed into words per minute; the bar above the panel shows it all. What is read back is what a
 * viewer sees -- each key's --heat and session level, the bar's chips -- and the counts through
 * hud.stats. The expectations are stated from the behaviour, not from the constants hud.js uses:
 * a retuned curve still passes, a glow that never goes out or a keystroke counted twice does not.
 */
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { loadPage } = require("./dom.js");

const FIXTURE = path.join(__dirname, "fixtures", "diamond.json");
const SPACED_MS = 100;    // between presses that must stay separate keystrokes: past any combo term
const CHORD_MS = 5;       // between the keys of a chord
// A stat box's text as a viewer reads it: each row's label and value, rows apart.
const rowsText = c => c.children.filter(row => row.className === "row")
  .map(row => row.children.map(x => x.textContent).join(" ")).join(" / ");

function main() {
  const argv = process.argv.slice(2);
  const keymap = argv.includes("--keymap") ? argv[argv.indexOf("--keymap") + 1] : FIXTURE;
  const verbose = argv.includes("--verbose") || argv.includes("-v");
  const data = JSON.parse(fs.readFileSync(keymap, "utf8"));
  const hudT = (name, fallback) => (data.hud && data.hud[name] != null ? data.hud[name] : fallback);
  const fadeMs = hudT("heatmap_ms", 3000);
  const term = (data.combo_term || 50) + hudT("combo_slack_ms", 20);
  const settle = Math.max(term, hudT("sequence_ms", 200)) + 10;    // a keystroke is counted by then
  const W = hudT("wpm_window_ms", 10000), idle = hudT("wpm_idle_ms", 3000);

  const fail = [], notes = [];
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
  const tap = (page, pos, sent) => { page.hud.pressAt(pos, sent); page.clock.advance(SPACED_MS); page.hud.releaseAt(pos); };
  const warmKeys = page => page.hud.state.keyEls.map((e, i) => (heat(page, i) !== "" ? i : -1)).filter(i => i >= 0);
  const sessionLevel = (page, idx) => {
    const e = page.hud.state.keyEls[idx];
    for (let i = 1; i <= 6; i++) if (e.classList.contains("hs" + i)) return i;
    return 0;
  };

  // ---------- reading the keymap ----------
  const base = data.base;
  const posMap = data.positions || {};
  const posOf = new Map(Object.keys(posMap).length ? Object.entries(posMap).map(([p, i]) => [i, Number(p)])
                                                    : data.layout.keys.map((k, i) => [i, i]));
  const binding = (idx, layers) => {
    for (const name of layers) {
      const k = data.layers[name] && data.layers[name][idx];
      if (k && k.type !== "trans") return name;
    }
    return layers[0];
  };
  const idOf = layer => {
    const e = Object.entries(data.zmk_layers || {}).find(([, z]) => z && z.drawer === layer);
    return e ? Number(e[0]) : null;
  };
  const key = idxs => idxs.map(i => posOf.get(i)).sort((a, b) => a - b).join(",");
  const count = (tally, layer, pos) => ((tally.presses[layer] || {})[pos] || 0);
  const total = tally => Object.values(tally.presses).reduce((n, m) => n + Object.values(m).reduce((a, b) => a + b, 0), 0);
  const combosOf = tally => Object.values(tally.combos).reduce((n, m) => n + Object.values(m).reduce((a, b) => a + b, 0), 0);
  const onBaseCombo = set => data.combos.some(c => c.layers.includes(base) && c.positions.length === set.length &&
                                                   c.positions.every(p => set.includes(p)));

  // Three keys the keyboard can report, on the base layer.
  const onBase = [...posOf.keys()].filter(i => { const b = data.layers[base][i]; return b && b.type !== "trans" && b.tap; })
    .map(idx => ({ idx, pos: posOf.get(idx) }));
  if (onBase.length < 3) { console.error(`stats_test: ${keymap} has fewer than three base keys with a position`); process.exit(2); }
  const [A, B, C] = onBase;
  // A letter on the base layer that only one base key types, and that key.
  const baseKeys = data.layers[base];
  const letter = baseKeys.map(k => k.tap).find(t => typeof t === "string" && /^\p{Ll}$/u.test(t) &&
                                                    baseKeys.filter(k => k.tap === t).length === 1);
  const L = letter ? { idx: baseKeys.findIndex(k => k.tap === letter) } : null;
  if (L) L.pos = posOf.get(L.idx);
  const typedLetter = extra => Object.assign({ type: "keyDown", chars: letter, name: letter, code: 0, flags: {}, repeat: false }, extra || {});

  // ---------- heat: a press, and its fade ----------
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
  }

  // ---------- heat: struck often ----------
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
    check("and cools in the end", warmKeys(p).length === 0, `${warmKeys(p).length} warm`);
  }

  // ---------- heat: recency, the board rebuilt, turned off ----------
  {
    const p = fresh();
    tap(p, A.pos);
    p.clock.advance(fadeMs / 3);
    tap(p, B.pos);
    check("a key pressed recently is warmer than one pressed before it",
          level(p, B.idx) > level(p, A.idx) && level(p, A.idx) > 0, `${level(p, B.idx)} vs ${level(p, A.idx)}`);
    const before = p.hud.state.keyEls[A.idx];
    p.hud.load(data);                             // what a keymap reload and every reconnect do
    check("the board was rebuilt", p.hud.state.keyEls[A.idx] !== before);
    check("and the glow survived it", heat(p, A.idx) !== "", "cold after the reload");

    const q = fresh({ heatmap_ms: 0 });
    tap(q, A.pos);
    check("heatmap_ms: 0 warms nothing", warmKeys(q).length === 0 && q.hud.state.heat.size === 0, JSON.stringify(warmKeys(q)));
  }

  // ---------- heat: keys lit from reports alone ----------
  if (L) {
    // Certain only once its position can no longer come: then it glows, and not before.
    const p = fresh();
    p.hud.key(typedLetter());
    check(`a key lit from a report alone does not glow while it may be taken back (${letter})`, heat(p, L.idx) === "");
    p.clock.advance(settle);
    check("and glows once nothing can", heat(p, L.idx) !== "", "cold");

    // A report that beats its own position lights a guess the position takes down -- sometimes
    // another key than the one the position names. The guess must never glow.
    const q = fresh();
    const other = [A, B, C].find(k => k.idx !== L.idx);
    q.hud.key(typedLetter());
    q.clock.advance(CHORD_MS);
    tap(q, other.pos);
    q.clock.advance(settle);
    check("a guess its position took back never glows", heat(q, L.idx) === "" && heat(q, other.idx) !== "",
          `guess ${heat(q, L.idx) || "cold"}, position ${heat(q, other.idx) || "cold"}`);
  }

  // ---------- heat: a GIF still ----------
  {
    const p = fresh({}, "?keymap=keymap.json&demo=0");
    check("a still marks the page", p.document.body.classList.contains("demo"));
    p.hud.pressAt(A.pos);
    p.clock.advance(settle);
    check("and warms nothing, and counts nothing, whatever it presses",
          warmKeys(p).length === 0 && total(p.hud.stats.local()) === 0 && p.hud.stats.pending() === 0,
          `${warmKeys(p).length} warm, ${total(p.hud.stats.local())} counted`);
  }

  // ---------- the ledger: a press ----------
  {
    const p = fresh();
    p.hud.pressAt(A.pos);
    p.clock.advance(term - 1);
    check("a press is not counted while a combo may still claim it", total(p.hud.stats.local()) === 0 && p.hud.stats.pending() > 0);
    p.clock.advance(2);
    const loc = p.hud.stats.local(), own = p.hud.stats.unsent();
    check("and is counted once after, on its layer at its position", count(loc, base, A.pos) === 1 && total(loc) === 1,
          JSON.stringify(loc.presses));
    check("as the keyboard's own", count(own, base, A.pos) === 1);
    p.hud.releaseAt(A.pos);
    p.clock.advance(1000);
    check("and never again", total(p.hud.stats.local()) === 1);

    const q = fresh();
    tap(q, A.pos, true);
    q.clock.advance(settle);
    check("a press sent in counts on the page, never in a session",
          total(q.hud.stats.local()) === 1 && total(q.hud.stats.unsent()) === 0);

    q.hud.pressAt(9999);
    q.clock.advance(settle);
    check("a position the keymap does not place counts nothing", total(q.hud.stats.local()) === 1 && q.hud.stats.pending() === 0);

    const r = fresh();
    r.hud.pressAt(B.pos);
    r.hud.load(data);
    check("a keymap reload counts what was waiting, with the keymap it was drawn on",
          count(r.hud.stats.local(), base, B.pos) === 1 && r.hud.stats.pending() === 0);
  }

  // ---------- the ledger: combos ----------
  {
    const two = data.combos.find(c => c.layers.includes(base) && c.positions.length === 2 && c.positions.every(i => posOf.has(i)));
    if (two) {
      const p = fresh();
      p.hud.pressAt(posOf.get(two.positions[0]));
      p.clock.advance(CHORD_MS);
      p.hud.pressAt(posOf.get(two.positions[1]));
      p.clock.advance(settle);
      const loc = p.hud.stats.local();
      check(`a chord counts one combo (${two.key.tap})`, ((loc.combos[base] || {})[key(two.positions)] || 0) === 1 && combosOf(loc) === 1,
            JSON.stringify(loc.combos));
      check("and a press for each of its keys", total(loc) === 2);
    } else notes.push("no two-key combo on the base layer: combo counting not checked");

    // A third key inside the term makes a bigger combo: the smaller one's pill comes down, and so
    // must its count.
    let big = null, small = null;
    for (const c of data.combos) {
      if (!c.layers.includes(base) || c.positions.length !== 3 || !c.positions.every(i => posOf.has(i))) continue;
      const [a, b, x] = c.positions;
      for (const [p1, p2, p3] of [[a, b, x], [a, x, b], [b, x, a]]) {
        if (onBaseCombo([p1, p2])) { big = c; small = [p1, p2, p3]; break; }
      }
      if (big) break;
    }
    if (big) {
      const p = fresh();
      for (const [i, idx] of small.entries()) { if (i) p.clock.advance(CHORD_MS); p.hud.pressAt(posOf.get(idx)); }
      p.clock.advance(settle);
      const loc = p.hud.stats.local();
      check(`a combo superseded by a larger one counts only the larger (${big.key.tap})`,
            ((loc.combos[base] || {})[key(big.positions)] || 0) === 1 && combosOf(loc) === 1, JSON.stringify(loc.combos));
    } else notes.push("no three-key combo containing a two-key one on the base layer: supersession not checked");

    // ZMK's own rules for when a chord is not a combo, and its keys are typed one by one.
    if (two) {
      const [p1, p2] = two.positions.map(i => posOf.get(i));
      const combos = page => combosOf(page.hud.stats.local());
      const pills = page => page.board.querySelectorAll(".combo-pill").length;
      // A fast roll: the first key is up before the second goes down, inside the combo term.
      const roll = fresh();
      roll.hud.pressAt(p1); roll.clock.advance(CHORD_MS); roll.hud.releaseAt(p1); roll.clock.advance(CHORD_MS);
      roll.hud.pressAt(p2);
      roll.clock.advance(settle);
      check("a key let go before the chord is complete makes it two keystrokes, not a combo",
            combos(roll) === 0 && pills(roll) === 0 && total(roll.hud.stats.local()) === 2,
            `${combos(roll)} combos, ${pills(roll)} pills`);
      // require-prior-idle-ms: a chord struck too soon after another key is typed as its keys.
      const idleData = Object.assign({}, data, { combo_idle: 150 });
      const other = [A, B, C].find(k => !two.positions.includes(k.idx));
      const soon = loadPage(); soon.hud.load(idleData); soon.hud.setLayers([]);
      tap(soon, other.pos); soon.clock.advance(20);
      soon.hud.pressAt(p1); soon.clock.advance(CHORD_MS); soon.hud.pressAt(p2);
      soon.clock.advance(settle);
      check("a chord struck sooner than the keymap's idle after a key is its keys", combos(soon) === 0 && pills(soon) === 0,
            `${combos(soon)} combos`);
      const later = loadPage(); later.hud.load(idleData); later.hud.setLayers([]);
      tap(later, other.pos); later.clock.advance(300);
      later.hud.pressAt(p1); later.clock.advance(CHORD_MS); later.hud.pressAt(p2);
      later.clock.advance(settle);
      check("and one struck after it is the combo", combos(later) === 1, `${combos(later)} combos`);
    }
  }

  // ---------- the ledger: reports ----------
  if (L) {
    const other = [A, B, C].find(k => k.idx !== L.idx);
    for (const late of [CHORD_MS, 60]) {
      const p = fresh();
      p.hud.key(typedLetter());
      p.clock.advance(late);
      p.hud.pressAt(L.pos);
      p.clock.advance(settle);
      const loc = p.hud.stats.local();
      check(`a report ${late} ms before its position counts one keystroke`, total(loc) === 1 && count(loc, base, L.pos) === 1,
            JSON.stringify(loc.presses));
    }
    const p = fresh();
    p.hud.key(typedLetter());
    p.clock.advance(term + 1);
    check("a report alone is not counted while its position may still come", total(p.hud.stats.local()) === 0);
    p.clock.advance(settle);
    check("and is counted once it cannot", count(p.hud.stats.local(), base, L.pos) === 1 && total(p.hud.stats.local()) === 1);
    p.hud.key(typedLetter({ combos: false }));
    p.clock.advance(settle);
    check("a report sent in counts on the page, never in a session",
          total(p.hud.stats.local()) === 2 && total(p.hud.stats.unsent()) === 1);
    void other;
  }

  // ---------- the ledger: layers ----------
  {
    // The thumb that holds a layer, and a key that layer binds, which forms no combo with it.
    const held = (data.activators || []).map(a => ({ a, id: idOf(a.layer) }))
      .find(({ a, id }) => a.kind === "hold" && id !== null && a.layer !== base && posOf.has(a.idx));
    const K = held && [...posOf.keys()].find(i => i !== held.a.idx && binding(i, [held.a.layer, base]) === held.a.layer &&
                                                 !onBaseCombo([held.a.idx, i]) && !onBaseCombo([i, held.a.idx]));
    if (held && K !== undefined) {
      const thumb = posOf.get(held.a.idx), kpos = posOf.get(K);
      // ZMK decides a hold when the next key goes down, and sends that key's position first.
      const p = fresh();
      p.hud.pressAt(thumb);
      p.clock.advance(20);
      p.hud.pressAt(kpos);
      p.clock.advance(3);
      p.hud.setLayers([held.id]);
      p.clock.advance(settle);
      const loc = p.hud.stats.local();
      check(`the first key on a held layer counts on it (${held.a.layer})`,
            count(loc, held.a.layer, kpos) === 1 && count(loc, base, thumb) === 1, JSON.stringify(loc.presses));

      // A key that went down before the thumb was typed on the base, whatever comes up after.
      const q = fresh();
      q.hud.pressAt(kpos);
      q.clock.advance(10);
      q.hud.pressAt(thumb);
      q.clock.advance(3);
      q.hud.setLayers([held.id]);
      q.clock.advance(settle);
      const loc2 = q.hud.stats.local();
      check("a key pressed before the layer's own key stays on the base",
            count(loc2, binding(K, [base]), kpos) === 1 && count(loc2, held.a.layer, kpos) === 0, JSON.stringify(loc2.presses));
    } else notes.push("no held layer with a key of its own: held-layer counting not checked");

    // A one-shot layer: its key is tapped, the layer serves one key and drops.
    const one = (data.activators || []).map(a => ({ a, id: idOf(a.layer) }))
      .find(({ a, id }) => a.kind === "sticky" && id !== null && posOf.has(a.idx));
    const K1 = one && [...posOf.keys()].find(i => i !== one.a.idx && binding(i, [one.a.layer, base]) === one.a.layer);
    const K2 = one && [...posOf.keys()].find(i => i !== one.a.idx && i !== K1 && binding(i, [base]) === base);
    if (one && K1 !== undefined && K2 !== undefined) {
      const p = fresh();
      tap(p, posOf.get(one.a.idx));
      p.hud.setLayers([one.id]);
      p.clock.advance(SPACED_MS);
      tap(p, posOf.get(K1));
      p.hud.setLayers([]);                          // held back for the flash; the next key applies it
      p.clock.advance(SPACED_MS);
      tap(p, posOf.get(K2));
      p.clock.advance(settle);
      const loc = p.hud.stats.local();
      check(`a one-shot layer's key counts on it, the next key on the base (${one.a.layer})`,
            count(loc, one.a.layer, posOf.get(K1)) === 1 && count(loc, base, posOf.get(K2)) === 1 &&
            count(loc, base, posOf.get(one.a.idx)) === 1, JSON.stringify(loc.presses));
    } else notes.push("no one-shot layer: its counting not checked");
  }

  // ---------- typing speed ----------
  const typeEv = (ch, extra) => Object.assign({ type: "keyDown", name: ch === " " ? "space" : ch, chars: ch, code: 0,
                                                flags: {}, repeat: false }, extra || {});
  const typeText = (p, text, gap, extra) => {
    [...text].forEach((ch, i) => { if (i) p.clock.advance(gap); p.hud.key(typeEv(ch, extra)); });
  };
  {
    const p = fresh();
    typeText(p, "x".repeat(60), 200);               // 5 characters a second: 60 wpm
    check("a character every 200 ms is 60 wpm", Math.abs(p.hud.stats.wpm() - 60) <= 1, p.hud.stats.wpm());
    const loc = p.hud.stats.local();
    check("its time is the typing in it", loc.active_ms === 59 * 200 && loc.active_net === 59, `${loc.active_ms} ms, ${loc.active_net}`);
    check("and a full window of it is a peak", Math.abs(loc.peak_wpm - 60) <= 1, loc.peak_wpm);

    const q = fresh();
    typeText(q, "abc", 50);
    check("two quick keys are not a speed: the window is at least 2 s", q.hud.stats.wpm() <= 20, q.hud.stats.wpm());
    check("and no peak comes of a window that is not full", q.hud.stats.local().peak_wpm === 0);

    const r = fresh();
    typeText(r, "abcdefghij", 200);
    const before = r.hud.stats.wpm();
    r.clock.advance(200); r.hud.key({ type: "keyDown", name: "delete", chars: "\x7f", code: 42, flags: {}, repeat: false });
    r.clock.advance(200); r.hud.key({ type: "keyDown", name: "delete", chars: "\x7f", code: 42, flags: { alt: true }, repeat: false });
    const loc2 = r.hud.stats.local();
    check("a backspace takes a character off, with a modifier a word", loc2.deleted === 6 && loc2.chars === 10, `${loc2.chars} typed, ${loc2.deleted} deleted`);
    check("and slows the speed", r.hud.stats.wpm() < before, `${before} -> ${r.hud.stats.wpm()}`);

    const s = fresh();
    for (const ev of [{ name: "c", chars: "c", flags: { cmd: true } }, { name: "x", chars: "x", flags: { ctrl: true } },
                      { name: "left", chars: "" }, { name: "escape", chars: "\x1b" }, { name: "f5", chars: "" }]) {
      s.hud.key(Object.assign({ type: "keyDown", code: 0, flags: {}, repeat: false }, ev));
    }
    check("commands, arrows and Esc are not text", s.hud.stats.local().chars === 0, s.hud.stats.local().chars);
    for (const ev of [{ name: "return", chars: "\r" }, { name: "tab", chars: "\t" }, { name: "space", chars: " " },
                      { name: "™", chars: "™", flags: { alt: true } }]) {
      s.hud.key(Object.assign({ type: "keyDown", code: 0, flags: {}, repeat: false }, ev));
    }
    check("Return, Tab, Space and a character typed with ⌥ are", s.hud.stats.local().chars === 4, s.hud.stats.local().chars);

    const t = fresh();
    typeText(t, "abcde", 200);
    t.clock.advance(idle + 2000);
    typeText(t, "fghij", 200);
    const loc3 = t.hud.stats.local();
    check("a pause starts a new burst, and is not typing time", loc3.active_ms === 8 * 200 && loc3.active_net === 8,
          `${loc3.active_ms} ms, ${loc3.active_net}`);

    const u = fresh();
    typeText(u, "x".repeat(80), 150, { combos: false });
    check("typing sent in has a speed", u.hud.stats.wpm() > 0);
    check("and counts on the page, never in a session",
          u.hud.stats.local().chars === 80 && u.hud.stats.unsent().chars === 0 && u.hud.stats.unsent().peak_wpm === 0);

    p.clock.advance(W + fadeMs + 1000);
    check("the speed goes to nothing once typing stops", p.hud.stats.wpm() === 0);
    check("with nothing left scheduled", p.clock.pending() === 0, p.clock.pending());
  }

  // ---------- the bar ----------
  {
    const p = fresh();
    const bar = p.document.getElementById("stats");
    const chip = name => bar.querySelectorAll(".stat").find(c => c.classList.contains(name));
    const text = name => { const c = chip(name); return c ? rowsText(c) : null; };
    check("the bar has its chips", ["wpm", "session", "acc", "keys", "layer", "mode"].every(chip),
          bar.children.map(c => c.className).join(" | "));
    check("with nothing typed, no speed", text("wpm") === "wpm —", text("wpm"));
    typeText(p, "x".repeat(60), 200);
    check("typing shows its speed", text("wpm") === "wpm 60", text("wpm"));
    check("its session average and peak", /^avg wpm 60 \/ top wpm 6[01]$/.test(text("session")), text("session"));
    check("and its accuracy", text("acc") === "accurate 100%", text("acc"));
    // The x typed above is a chord on some boards (the Diamond's), so keys are counted apart.
    const k = fresh();
    const ktext = name => k.document.getElementById("stats").querySelectorAll(".stat")
      .find(c => c.classList.contains(name));
    tap(k, A.pos); tap(k, B.pos); tap(k, A.pos);
    k.clock.advance(settle);
    check("keys are counted", rowsText(ktext("keys")) === "keys 3 / combos 0%", rowsText(ktext("keys")));
    check("the layer on screen has its share", rowsText(ktext("layer")).endsWith(" 100%"), rowsText(ktext("layer")));
    check("and the heatmap its mode", rowsText(ktext("mode")) === "heat live", rowsText(ktext("mode")));

    const q = fresh({ stats_bar: 0 });
    check("stats_bar: 0 hides it", q.document.getElementById("stats").classList.contains("off"));
  }

  // ---------- the session heatmap ----------
  {
    const p = fresh();
    for (let i = 0; i < 5; i++) tap(p, A.pos);
    tap(p, B.pos);
    p.clock.advance(settle);
    p.hud.setHeatmap("session");
    check("the session heatmap has no live glow", warmKeys(p).length === 0, JSON.stringify(warmKeys(p)));
    check("a key pressed more is hotter", sessionLevel(p, A.idx) > sessionLevel(p, B.idx) && sessionLevel(p, B.idx) >= 1,
          `${sessionLevel(p, A.idx)} vs ${sessionLevel(p, B.idx)}`);
    check("the most pressed is the top step", sessionLevel(p, A.idx) === 6, sessionLevel(p, A.idx));
    check("a key never pressed has none", sessionLevel(p, C.idx) === 0);
    tap(p, C.pos);
    p.clock.advance(settle);
    check("and gets one once it is", sessionLevel(p, C.idx) >= 1);
    // On a layer where a key is transparent, the base's count for it does not show through.
    let through = null;
    for (const l of data.layer_order) {
      if (l === base || idOf(l) === null) continue;
      const idx = [...posOf.keys()].find(i => data.layers[l][i] && data.layers[l][i].type === "trans" && binding(i, [base]) === base);
      if (idx !== undefined) { through = { layer: l, idx }; break; }
    }
    if (through) {
      for (let i = 0; i < 3; i++) tap(p, posOf.get(through.idx));
      p.clock.advance(settle);
      check("a key pressed on the base shows its count there", sessionLevel(p, through.idx) >= 1);
      p.hud.setLayers([idOf(through.layer)]);
      p.clock.advance(1000);
      check(`and none where it is transparent (${through.layer})`, sessionLevel(p, through.idx) === 0, sessionLevel(p, through.idx));
      p.hud.setLayers([]);
      p.clock.advance(1000);
    } else notes.push("no layer with a transparent key: session heat through transparency not checked");
    p.hud.setHeatmap("off");
    tap(p, A.pos);
    check("off shows nothing", warmKeys(p).length === 0 && sessionLevel(p, A.idx) === 0);
    p.hud.setHeatmap("live");
    tap(p, B.pos);
    check("and live glows again", heat(p, B.idx) !== "");
  }

  // ---------- strokes: the time a key takes, and same-finger bigrams ----------
  {
    // Keys that can only be tapped: no hold of their own, no layer brought up with them.
    const isPlain = k => { const b = data.layers[base][k.idx]; return !b.hold && !(data.activators || []).some(a => a.idx === k.idx); };
    const plain = onBase.filter(isPlain);
    if (plain.length >= 4) {
      const [P, Q, R, S] = plain;
      const fingersFor = assign => data.layout.keys.map((k, i) => (assign[i] !== undefined ? assign[i] : null));
      const withFingers = (assign, extra) => {
        const page = loadPage();
        page.hud.load(Object.assign({}, data, { fingers: fingersFor(assign) }, extra || {}));
        page.hud.setLayers([]);
        return page;
      };
      // P and Q on one finger, R on another, S a thumb.
      const hands = { [P.idx]: "li", [Q.idx]: "li", [R.idx]: "ri", [S.idx]: "lt" };
      const ms = (t, pos, layer) => ((t.ms[layer || base] || {})[pos] || 0);
      const timed = (t, pos, layer) => ((t.timed[layer || base] || {})[pos] || 0);
      const played = (keys, gap) => {
        const q = withFingers(hands);
        for (const k of keys) { tap(q, k.pos); q.clock.advance(gap || 50); }
        q.clock.advance(settle);
        return q.hud.stats.local();
      };

      let t = played([P, Q]);          // Q goes down 150 ms after P did
      check("a key's time is from the keystroke before it", ms(t, Q.pos) === 150 && timed(t, Q.pos) === 1,
            JSON.stringify([t.ms, t.timed]));
      check("the first of a burst has none", timed(t, P.pos) === 0);
      check("one finger on two keys in a row is a same-finger bigram", t.sfb === 1 && t.bigrams === 1, `${t.sfb}/${t.bigrams}`);
      t = played([P, R]);
      check("two fingers are a bigram, not a same-finger one", t.sfb === 0 && t.bigrams === 1, `${t.sfb}/${t.bigrams}`);
      t = played([P, P]);
      check("one key twice is not a same-finger bigram", t.sfb === 0 && t.bigrams === 1, `${t.sfb}/${t.bigrams}`);
      t = played([P, S, Q]);
      check("a thumb's keystroke between two keys makes no bigram of them", t.bigrams === 0, `${t.sfb}/${t.bigrams}`);
      check("and the key after it is timed from it", ms(t, Q.pos) === 150, ms(t, Q.pos));
      t = played([P, Q], idle + 100);
      check("a pause makes no bigram, and the key after it starts a burst", t.bigrams === 0 && timed(t, Q.pos) === 0,
            `${t.bigrams}, ${timed(t, Q.pos)}`);

      const chord = data.combos.find(c => c.layers.includes(base) && c.positions.length === 2 &&
                                          c.positions.every(i => posOf.has(i) && i !== P.idx && i !== Q.idx));
      if (chord) {
        const q = withFingers(hands);
        const [c1, c2] = chord.positions.map(i => posOf.get(i));
        tap(q, P.pos); q.clock.advance(50);
        q.hud.pressAt(c1); q.clock.advance(CHORD_MS); q.hud.pressAt(c2);
        q.clock.advance(SPACED_MS); q.hud.releaseAt(c1); q.hud.releaseAt(c2);
        q.clock.advance(50); tap(q, Q.pos);
        q.clock.advance(settle);
        const u = q.hud.stats.local();
        check("a chord between two keys makes no bigram of them", u.bigrams === 0 && u.sfb === 0 && combosOf(u) === 1,
              `${u.sfb}/${u.bigrams}, ${combosOf(u)} combos`);
      } else notes.push("no two-key combo on the base: a chord between keystrokes not checked");

      // A thumb holding a layer is no keystroke: the key struck on the layer follows the one before.
      const held = (data.activators || []).map(a => ({ a, id: idOf(a.layer) }))
        .find(({ a, id }) => a.kind === "hold" && id !== null && a.layer !== base && posOf.has(a.idx));
      const K = held && [...posOf.keys()].find(i => i !== held.a.idx && i !== P.idx && binding(i, [held.a.layer, base]) === held.a.layer &&
                                                   !onBaseCombo([held.a.idx, i]) && !onBaseCombo([i, held.a.idx]));
      if (held && K !== undefined) {
        const q = withFingers(Object.assign({}, hands, { [K]: "li", [held.a.idx]: "lt" }));
        const thumb = posOf.get(held.a.idx), kpos = posOf.get(K);
        tap(q, P.pos); q.clock.advance(50);
        q.hud.pressAt(thumb); q.clock.advance(60);
        q.hud.pressAt(kpos); q.clock.advance(3); q.hud.setLayers([held.id]);
        q.clock.advance(SPACED_MS); q.hud.releaseAt(kpos);
        q.clock.advance(30); q.hud.releaseAt(thumb); q.hud.setLayers([]);
        q.clock.advance(settle);
        const u = q.hud.stats.local();
        check(`a key held for a layer is not a keystroke (${held.a.layer})`,
              u.sfb === 1 && u.bigrams === 1 && timed(u, kpos, held.a.layer) === 1 && ms(u, kpos, held.a.layer) === 210,
              JSON.stringify([u.sfb, u.bigrams, u.ms, u.timed]));
      } else notes.push("no held layer with a key of its own: a layer hold between keystrokes not checked");

      // A home-row mod: rolled into the next key it typed its letter; held over it, it did not.
      const H = onBase.find(k => data.layers[base][k.idx].hold && !(data.activators || []).some(a => a.idx === k.idx) &&
                                 k.idx !== P.idx && k.idx !== Q.idx);
      if (H) {
        const both = Object.assign({}, hands, { [H.idx]: "lm" });
        const r1 = withFingers(both);
        tap(r1, P.pos); r1.clock.advance(50);
        r1.hud.pressAt(H.pos); r1.clock.advance(SPACED_MS); r1.hud.pressAt(Q.pos);
        r1.clock.advance(30); r1.hud.releaseAt(H.pos); r1.clock.advance(40); r1.hud.releaseAt(Q.pos);
        r1.clock.advance(settle);
        const u = r1.hud.stats.local();
        check("a key with a hold, rolled into the next, is a keystroke", u.bigrams === 2 && u.sfb === 0, `${u.sfb}/${u.bigrams}`);
        const r2 = withFingers(both);
        tap(r2, P.pos); r2.clock.advance(50);
        r2.hud.pressAt(H.pos); r2.clock.advance(SPACED_MS); r2.hud.pressAt(Q.pos);
        r2.clock.advance(60); r2.hud.releaseAt(Q.pos); r2.clock.advance(30); r2.hud.releaseAt(H.pos);
        r2.clock.advance(settle);
        const w = r2.hud.stats.local();
        check("held over it, it is not", w.bigrams === 1 && w.sfb === 1, `${w.sfb}/${w.bigrams}`);
      } else notes.push("no base key with a hold: home-row mods' taps and holds not checked");

      // Typing sent in is timed for what the page shows, never for the session.
      const s = withFingers(hands);
      tap(s, P.pos); s.clock.advance(50);
      s.hud.pressAt(Q.pos, true); s.clock.advance(SPACED_MS); s.hud.releaseAt(Q.pos); s.clock.advance(50);
      tap(s, R.pos);
      s.clock.advance(settle);
      const loc = s.hud.stats.local(), own = s.hud.stats.unsent();
      check("typing sent in is timed on the page, and not for the session", loc.bigrams === 2 && own.bigrams === 1 &&
            timed(own, Q.pos) === 0 && timed(own, R.pos) === 1, `${loc.bigrams} ${own.bigrams} ${JSON.stringify(own.timed)}`);

      // The chips: off until the config asks for them; hands and bigrams only with fingers.
      const shown = (page, name) => !page.document.getElementById("stats").querySelectorAll(".stat")
        .find(x => x.classList.contains(name)).classList.contains("off");
      const plainPage = fresh();
      check("the new chips are off unless asked for", ["time", "hands", "sfb", "slow"].every(n => !shown(plainPage, n)) &&
            ["wpm", "session", "acc", "keys", "layer", "mode"].every(n => shown(plainPage, n)));
      const asked = Object.assign({}, data.stats, { time: true, hands: true, sfb: true, slow: true, wpm: false });
      const blind = loadPage();
      blind.hud.load(Object.assign({}, data, { stats: asked, fingers: null }));
      check("hands and same-finger need to know the fingers", !shown(blind, "hands") && !shown(blind, "sfb") &&
            shown(blind, "time") && shown(blind, "slow") && !shown(blind, "wpm"));
      const c = withFingers(hands, { stats: asked });
      const txt = name => c.document.getElementById("stats").querySelectorAll(".stat")
        .find(x => x.classList.contains(name));
      for (let i = 0; i < 12; i++) { tap(c, P.pos); c.clock.advance(20); tap(c, R.pos); c.clock.advance(200); }
      tap(c, Q.pos); tap(c, P.pos); tap(c, Q.pos);
      c.clock.advance(settle);
      // P after R: 300 ms, eleven times, and once 100 ms after Q; R after P: 120 ms. Of 26
      // bigrams, Q-P and P-Q are one finger's.
      check("the slowest key and its time", /^slowest \S+ \/ its time /.test(rowsText(txt("slow"))) &&
            rowsText(txt("slow")).endsWith(` ${Math.round((11 * 300 + 100) / 12)} ms`), rowsText(txt("slow")));
      check("same-finger bigrams as a share", rowsText(txt("sfb")) === `same finger ${(Math.round(2 / 26 * 1000) / 10).toFixed(1)}%`, rowsText(txt("sfb")));
      check("each hand's share", rowsText(txt("hands")) === `left hand ${Math.round(15 / 27 * 100)}% / right hand ${Math.round(12 / 27 * 100)}%`, rowsText(txt("hands")));
      typeText(c, "x".repeat(60), 200);
      check("and the time spent typing", rowsText(txt("time")) === "typing 11s", rowsText(txt("time")));

      // The heatmaps the session's counts make besides its own: every layer at once, and speed.
      const other = data.layer_order.find(l => l !== base && idOf(l) !== null && binding(P.idx, [l, base]) === l &&
                                               binding(Q.idx, [l, base]) !== base);
      if (other) {
        const h = withFingers(hands);
        tap(h, P.pos); for (let i = 0; i < 3; i++) tap(h, Q.pos);
        h.hud.setLayers([idOf(other)]);
        h.clock.advance(1000);
        for (let i = 0; i < 9; i++) tap(h, P.pos);       // 10 in all against Q's 3: steps apart on a log scale
        h.hud.setLayers([]);
        h.clock.advance(1000 + settle);
        h.hud.setHeatmap("session");
        const byLayer = [sessionLevel(h, P.idx), sessionLevel(h, Q.idx)];
        h.hud.setHeatmap("physical");
        const physical = [sessionLevel(h, P.idx), sessionLevel(h, Q.idx)];
        check(`physical adds up every layer's presses of a key (${other})`, byLayer[0] < byLayer[1] && physical[0] > physical[1],
              JSON.stringify({ byLayer, physical }));
      } else notes.push("no layer binding a key of its own over the base: the physical heatmap not checked");
      const sp = withFingers(hands);
      for (let i = 0; i < 4; i++) { tap(sp, P.pos); sp.clock.advance(20); tap(sp, R.pos); sp.clock.advance(300); }
      sp.clock.advance(settle);
      sp.hud.setHeatmap("speed");
      check("speed: the slower key is the hotter", sessionLevel(sp, P.idx) === 6 && sessionLevel(sp, R.idx) === 1,
            `${sessionLevel(sp, P.idx)} vs ${sessionLevel(sp, R.idx)}`);
      check("and a key timed too seldom has none", sessionLevel(sp, Q.idx) === 0);

      // The session gets them, and shows what it has.
      const posted = [];
      const b = loadPage();
      b.window.webkit = { messageHandlers: { zmkhud: { postMessage: m => { const x = JSON.parse(m); if (x.kind === "tally") posted.push(x); } } } };
      b.hud.load(Object.assign({}, data, { fingers: fingersFor(hands) }));
      b.hud.setLayers([]);
      tap(b, P.pos); b.clock.advance(50); tap(b, Q.pos);
      b.clock.advance(settle + 2500);
      const got = posted.reduce((n, m) => ({ bigrams: n.bigrams + m.bigrams, sfb: n.sfb + m.sfb, timed: n.timed + timed(m, Q.pos) }),
                                { bigrams: 0, sfb: 0, timed: 0 });
      check("the session is sent each key's time and the bigrams", got.timed === 1 && got.bigrams === 1 && got.sfb === 1, JSON.stringify(posted));
      b.hud.receive({ kind: "session", v: 1, id: "s1", gen: 0, name: "w", heatmap: "live", presses: {}, combos: {},
                      ms: { [base]: { [P.pos]: 900 } }, timed: { [base]: { [P.pos]: 3 } }, totals: { sfb: 2, bigrams: 40 }, acks: {} });
      const v = b.hud.stats.view();
      check("and shows the session's own", timed(v, P.pos) === 3 && ms(v, P.pos) === 900 && v.bigrams === 41, JSON.stringify(v));
    } else notes.push("fewer than four base keys without a hold: key times and same-finger bigrams not checked");
  }

  // ---------- the panel's size ----------
  {
    // The Linux panel sizes its surface from the page on a handler of its own; it is not the
    // bridge a session's counts go through, which on Linux is the socket and its token.
    const p = loadPage();
    const posted = [];
    p.window.webkit = { messageHandlers: { zmkhudsize: { postMessage: s => posted.push(JSON.parse(s)) } } };
    p.hud.load(data);
    p.hud.setLayers([]);
    const size = posted.find(m => m.kind === "size");
    // (The tests' DOM does no layout, so the height itself is 0 here.)
    check("the Linux panel is told how tall the page is", size && Number.isInteger(size.height), JSON.stringify(posted));
    tap(p, A.pos);
    p.clock.advance(settle + 3000);
    // What its stats column shows, for the bar icon, but never the counts: those go through the socket.
    check("and is never sent the counts", posted.every(m => ["size", "nodrag", "stats"].includes(m.kind)),
          JSON.stringify(posted.map(m => m.kind)));
    // It moves its surface itself, so it is told where the controls are, as the macOS panel is.
    check("and is told where the controls are, to drag from anywhere else", posted.some(m => m.kind === "nodrag"),
          JSON.stringify(posted.map(m => m.kind)));
  }

  // ---------- the hide button ----------
  {
    // Offered where a panel can hide the HUD -- either panel's handler -- and it asks that panel.
    for (const name of ["zmkhud", "zmkhudsize"]) {
      const p = loadPage();
      const posted = [];
      p.window.webkit = { messageHandlers: { [name]: { postMessage: s => posted.push(JSON.parse(s)) } } };
      p.hud.load(data);
      p.hud.setLayers([]);
      check(`with ${name}, the hide button is offered`, p.document.documentElement.classList.contains("hideable"));
      p.document.getElementById("hide").click();
      check(`and asks ${name} to hide`, posted.some(m => m.kind === "hide"), JSON.stringify(posted.map(m => m.kind)));
    }
    {
      // The live WPM goes to the panel too, for its menubar icon: said when it changes.
      const p = loadPage();
      const posted = [];
      p.window.webkit = { messageHandlers: { zmkhud: { postMessage: s => posted.push(JSON.parse(s)) } } };
      p.hud.load(data);
      p.hud.setLayers([]);
      for (let i = 0; i < 30; i++) {
        p.hud.key({ type: "keyDown", name: "a", chars: "a", code: 4, flags: {} });
        p.clock.advance(120);
      }
      const said = posted.filter(m => m.kind === "wpm").map(m => m.wpm);
      check("the panel is told the live WPM as it changes", said.length > 1 && Math.max(...said) > 0, JSON.stringify(said));
      check("and only when it changes", said.every((w, i) => i === 0 || w !== said[i - 1]), JSON.stringify(said));
      p.clock.advance(60000);
      const last = posted.filter(m => m.kind === "wpm").pop();
      check("and that it is back to 0 once the typing stops", last && last.wpm === 0, JSON.stringify(last));
    }
    {
      // ...and the stats column, for the icons' menus and Omarchy's hover panel: what it shows, as
      // label and value, with the session's speeds as numbers, at most once a second.
      const p = loadPage();
      const posted = [];
      p.window.webkit = { messageHandlers: { zmkhud: { postMessage: s => posted.push(Object.assign(JSON.parse(s), { at: p.clock.now })) } } };
      p.hud.load(data);
      p.hud.setLayers([]);
      typeText(p, "x".repeat(60), 200);
      p.clock.advance(15000);              // past the WPM window: the live speed has run down to 0
      const said = posted.filter(m => m.kind === "stats"), last = said[said.length - 1];
      const column = p.document.getElementById("stats").querySelectorAll(".stat")
        .filter(c => !c.classList.contains("off") && !["named", "mode", "theme", "opacity"].some(n => c.classList.contains(n)))
        .flatMap(c => c.children.filter(row => row.className === "row").map(row => row.children.map(x => x.textContent)));
      check("the panel is told what the stats column shows", last && JSON.stringify(last.rows) === JSON.stringify(column),
            JSON.stringify([last && last.rows, column]));
      check("and the session's average and top speed", last && last.avg === 60 && last.top >= 60, JSON.stringify(last));
      check("at most once a second", said.length > 1 && said.every((m, i) => i === 0 || m.at - said[i - 1].at >= 1000),
            JSON.stringify(said.map(m => m.at)));
      p.clock.advance(60000);
      check("and not again while nothing changes", posted.filter(m => m.kind === "stats").length === said.length,
            `${said.length} then ${posted.filter(m => m.kind === "stats").length}`);
    }
    const p = loadPage();
    p.hud.load(data);
    p.hud.setLayers([]);
    check("a page with no panel offers none", !p.document.documentElement.classList.contains("hideable"));
    p.document.getElementById("hide").click();     // and a press there does nothing, and throws nothing
  }

  // ---------- a session the host keeps ----------
  {
    const TALLY_MS = 2000;
    const bridged = () => {
      const posted = [];
      const p = loadPage();
      // The macOS panel's bridge: the one page that may report the keyboard's counts.
      p.window.webkit = { messageHandlers: { zmkhud: { postMessage: s => {
        const m = JSON.parse(s); if (m.kind === "tally" || m.kind === "heatmap") posted.push(m);
      } } } };
      p.hud.load(data);
      p.hud.setLayers([]);
      return { p, posted };
    };
    const sessionMsg = (extra) => Object.assign({ kind: "session", v: 1, id: "s1", gen: 0, name: "week1", heatmap: "live",
                                                  presses: {}, combos: {}, totals: {}, acks: {} }, extra);
    const shown = p => total(p.hud.stats.view());

    const { p, posted } = bridged();
    tap(p, A.pos);
    p.clock.advance(settle + TALLY_MS);
    const first = posted.find(m => m.kind === "tally");
    check("the keyboard's counts are sent to the host", first && first.seq === 1 && count(first, base, A.pos) === 1,
          JSON.stringify(posted));
    // The host has it, and says so: shown once, not once from the host and once from the page.
    p.hud.receive(sessionMsg({ presses: { [base]: { [A.pos]: 1 } }, acks: { [first.page]: 1 } }));
    check("what the host has acknowledged is not counted again", shown(p) === 1, shown(p));
    tap(p, B.pos);
    p.clock.advance(settle + TALLY_MS);
    check("what it has not yet is shown with it", shown(p) === 2 && posted.filter(m => m.kind === "tally").length === 2, shown(p));
    p.hud.receive(sessionMsg({ presses: { [base]: { [A.pos]: 1, [B.pos]: 1 } }, acks: { [first.page]: 2 } }));
    check("and once it has it, once", shown(p) === 2, shown(p));
    const statText = name => rowsText(p.document.getElementById("stats").querySelectorAll(".stat").find(c => c.classList.contains(name)));
    check("the bar names the session, apart from the heat", statText("named") === "session week1" &&
          statText("mode") === "heat live", `${statText("named")} | ${statText("mode")}`);

    // A reset (or another session loaded): what was on the way belongs to the one before.
    tap(p, C.pos);
    p.clock.advance(settle + TALLY_MS);
    p.hud.receive(sessionMsg({ gen: 1, acks: {} }));
    check("a reset leaves nothing of the session before on the bar", shown(p) === 0, shown(p));

    p.hud.receive(sessionMsg({ gen: 1, heatmap: "session" }));
    check("the host's heatmap is the page's", p.hud.state.heatMode === "session");
    const before = posted.filter(m => m.kind === "heatmap").length;
    p.hud.setHeatmap("off");
    check("and the page's choice is sent to it", posted.filter(m => m.kind === "heatmap").length === before + 1 &&
          posted[posted.length - 1].mode === "off");

    // Typing sent in lights and counts on the page, but is never sent.
    const q = bridged();
    tap(q.p, A.pos, true);
    q.p.clock.advance(settle + TALLY_MS);
    check("typing sent in is never sent to the session", !q.posted.some(m => m.kind === "tally"));

    // A page that does not report (a browser on the socket): the host's counts, and none of its own.
    const r = fresh();
    tap(r, A.pos);
    r.clock.advance(settle);
    r.hud.receive(sessionMsg({ presses: { [base]: { [B.pos]: 4 } } }));
    check("a page that does not report shows the session as the host has it", shown(r) === 4, shown(r));
  }

  // ---------- the keys' theme, and the stats block out or away ----------
  {
    const posted = [];
    const p = loadPage();
    p.window.webkit = { messageHandlers: { zmkhud: { postMessage: s => {
      const m = JSON.parse(s); if (m.kind === "pref" || m.kind === "size") posted.push(m);
    } } } };
    p.hud.load(Object.assign({}, data, { hud: Object.assign({}, data.hud, { width: 600 }) }));
    p.hud.setLayers([]);
    const dark = page => page.document.body.classList.contains("dark");
    const box = (page, name) => rowsText(page.document.getElementById("stats").querySelectorAll(".stat").find(c => c.classList.contains(name)));
    check("the keys are light unless the config says otherwise", !dark(p) && box(p, "theme") === "keys light", box(p, "theme"));
    tap(p, A.pos);
    const lightGlow = level(p, A.idx);
    p.hud.setPref("theme", "dark");
    check("a click makes them dark", dark(p) && box(p, "theme") === "keys dark", box(p, "theme"));
    check("and the host is told, to remember it", posted.some(m => m.kind === "pref" && m.name === "theme" && m.value === "dark"),
          JSON.stringify(posted));
    // Dark keys have no glow: a warm key takes the ramp's step, dark blue hot and light blue as it
    // cools (hud.js paintKeyHeat), and then none.
    const liveStep = page => {
      const cl = page.hud.state.keyEls[A.idx].classList;
      for (let i = 1; i <= 6; i++) if (cl.contains("lh" + i)) return i;
      return 0;
    };
    const was = liveStep(p);
    check("dark keys take the ramp's step instead of a glow", lightGlow > 0 && level(p, A.idx) === 0 && was >= 1,
          `${lightGlow} -> ${level(p, A.idx)}, step ${was}`);
    p.clock.advance(1500);
    check("and as one cools it takes a lighter step", liveStep(p) >= 1 && liveStep(p) < was, `${was} -> ${liveStep(p)}`);
    p.clock.advance(2000);
    check("and then none", liveStep(p) === 0 && level(p, A.idx) === 0, `step ${liveStep(p)}`);
    const width = () => posted.filter(m => m.kind === "size").pop().width;
    const out = width();
    p.hud.setPref("side", "hidden");
    check("the chevron puts the stats away", p.document.body.classList.contains("side-hidden") &&
          p.document.getElementById("side").title === "show the stats", p.document.getElementById("side").title);
    check("and the panel is narrower by the block", out - width() > 100 && width() === 600, `${out} -> ${width()}`);
    p.hud.setPref("side", "bogus");
    check("a value that is not one is ignored", p.hud.state.prefs.side === "hidden");

    const alpha = page => page.document.documentElement.style.getPropertyValue("--panel-alpha");
    check("the background starts at the config's hud.opacity", alpha(p) === String(data.hud && data.hud.opacity != null ? data.hud.opacity / 100 : 0.86) &&
          box(p, "opacity").startsWith("background "), `${alpha(p)} ${box(p, "opacity")}`);
    const before = posted.filter(m => m.kind === "pref").length;
    p.hud.setPref("opacity", 40);
    check("the slider sets it", alpha(p) === "0.4" && box(p, "opacity") === "background 40%", `${alpha(p)} ${box(p, "opacity")}`);
    const prefs = posted.filter(m => m.kind === "pref");
    check("and the host is told once", prefs.length === before + 1 && prefs[prefs.length - 1].value === 40);
    p.hud.setPref("opacity", 140); p.hud.setPref("opacity", "50");
    check("nothing past 0-100, and only numbers", alpha(p) === "0.4");

    // A layer with no drawing of its own (the Diamond's 1, Alt OS) is what the viewer is on: the
    // box names it, as the banner does, though its keys are its base's.
    const undrawn = Object.values(data.zmk_layers || {}).find(z => z.id > 0 && z.drawer == null);
    if (undrawn) {
      p.hud.setLayers([undrawn.id]);
      check("the layer box names the layer as the banner does", box(p, "layer").startsWith(undrawn.label + " "), box(p, "layer"));
      p.hud.setLayers([]);
    }

    const q = fresh({ dark: 1 });
    check("hud.dark: 1 makes the keys dark", dark(q));
    q.hud.receive({ kind: "session", v: 1, id: "s1", gen: 0, name: "", heatmap: "live", prefs: { theme: "light" },
                    presses: {}, combos: {}, totals: {}, acks: {} });
    check("and what the host remembers wins", !dark(q) && box(q, "theme") === "keys light", box(q, "theme"));
  }

  // ---------- macOS secure input ----------
  {
    const p = fresh();
    const strip = () => p.document.getElementById("keys").children.map(c => c.textContent);
    const lit = () => p.hud.state.keyEls.filter(e => e.classList.contains("pressed")).length;
    p.hud.pressAt(A.pos);
    p.hud.key({ type: "keyDown", name: "a", chars: "a", code: 4, flags: {}, repeat: false });
    const counted = total(p.hud.stats.local());
    p.hud.receive({ kind: "secure", on: true });
    check("secure input clears the strip", strip().length === 0, JSON.stringify(strip()));
    check("and the keys lit, and their glow", lit() === 0 && warmKeys(p).length === 0, `${lit()} lit, ${warmKeys(p).length} warm`);
    check("and the banner says why", p.document.getElementById("layerSub").textContent === "secure input · typing hidden" &&
          p.document.body.classList.contains("secure"));
    p.hud.pressAt(B.pos);
    p.hud.key({ type: "keyDown", name: "p", chars: "p", code: 19, flags: {}, repeat: false });
    p.clock.advance(settle);
    check("while it is on, nothing typed shows or counts",
          strip().length === 0 && lit() === 0 && p.hud.stats.local().chars <= 1 && total(p.hud.stats.local()) <= counted + 1,
          `${strip().length} chips, ${lit()} lit, ${total(p.hud.stats.local())} counted`);
    p.hud.receive({ kind: "secure", on: false });
    p.hud.pressAt(C.pos);
    check("once it is off, the board follows again", lit() === 1 && p.document.getElementById("layerSub").textContent !== "secure input · typing hidden");
  }

  for (const n of notes) console.log("note " + n);
  for (const f of fail) console.log("FAIL " + f);
  console.log(`${checked} heat, count and speed checks, ${fail.length} failures`);
  process.exit(fail.length ? 1 : 0);
}

main();
