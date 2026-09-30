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
    const text = name => { const c = chip(name); return c ? c.children.map(x => x.textContent).join("") : null; };
    check("the bar has its chips", ["wpm", "session", "acc", "keys", "layer", "mode"].every(chip),
          bar.children.map(c => c.className).join(" | "));
    check("with nothing typed, no speed", text("wpm") === "— wpm", text("wpm"));
    typeText(p, "x".repeat(60), 200);
    check("typing shows its speed", text("wpm") === "60 wpm", text("wpm"));
    check("its session average and peak", /^avg 60 · top 6[01]$/.test(text("session")), text("session"));
    check("and its accuracy", text("acc") === "100% accurate", text("acc"));
    // The x typed above is a chord on some boards (the Diamond's), so keys are counted apart.
    const k = fresh();
    const ktext = name => k.document.getElementById("stats").querySelectorAll(".stat")
      .find(c => c.classList.contains(name)).children.map(x => x.textContent).join("");
    tap(k, A.pos); tap(k, B.pos); tap(k, A.pos);
    k.clock.advance(settle);
    check("keys are counted", ktext("keys") === "3 keys · 0% combos", ktext("keys"));
    check("the layer on screen has its share", ktext("layer").endsWith(" 100%"), ktext("layer"));
    check("and the heatmap its mode", ktext("mode") === "heat live", ktext("mode"));

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
    const text = p.document.getElementById("stats").querySelectorAll(".stat").find(c => c.classList.contains("mode"))
      .children.map(x => x.textContent).join("");
    check("the bar names the session", text === "week1 · heat live", text);

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

  for (const n of notes) console.log("note " + n);
  for (const f of fail) console.log("FAIL " + f);
  console.log(`${checked} heat, count and speed checks, ${fail.length} failures`);
  process.exit(fail.length ? 1 : 0);
}

main();
