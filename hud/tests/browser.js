/* zmk-layer-hud — the same sweep, in a real browser.
 *
 * hud/tests/dom.js is a DOM small enough to read, which is exactly why it cannot be trusted on its
 * own: an assertion is only as truthful as the page it ran against. This runs hud/tests/cases.js
 * unchanged in the real page, so the two can be compared. A case the two runners disagree about is
 * a hole in the shim, and the shim is what gets fixed.
 *
 * Load it from a served copy of hud/ and call it:
 *   await import("./tests/browser.js"); await window.hudBrowserSweep("tests/fixtures/diamond.json")
 *
 * Time is virtualised here too. What is being compared is what the page draws — legends, classes,
 * pill text, geometry — not how a browser schedules a timeout, and a sweep that waited out every
 * combo_pill_ms would take an hour.
 */
(async function () {
  "use strict";

  function virtualClock() {
    let now = Date.now(), seq = 0;
    const timers = new Map();
    const realSetTimeout = window.setTimeout.bind(window);
    window.setTimeout = (fn, ms) => { const id = ++seq; timers.set(id, { fn, at: now + (Number(ms) || 0), seq: id }); return id; };
    window.clearTimeout = id => timers.delete(id);
    Date.now = () => now;
    return {
      advance(ms) {
        const until = now + (Number(ms) || 0);
        for (;;) {
          let next = null;
          for (const t of timers.values()) if (t.at <= until && (!next || t.at < next.at || (t.at === next.at && t.seq < next.seq))) next = t;
          if (!next) break;
          timers.delete(next.seq);
          now = Math.max(now, next.at);
          next.fn();
        }
        now = until;
      },
      pending: () => timers.size,
      real: realSetTimeout,
    };
  }

  window.hudBrowserSweep = async function (keymapUrl) {
    const casesSrc = await (await fetch("tests/cases.js")).text();
    const cases = new Function(casesSrc + "\n;return (typeof module !== 'undefined' && module.exports) || self.hudCases;")
      .call(window);
    const data = await (await fetch(keymapUrl)).json();
    const clock = virtualClock();
    const hud = window.hud, board = document.getElementById("board");
    const held = new Set();
    const legend = e => (!e ? "" : (e.children.length ? e.innerHTML : e.textContent));

    const driver = {
      async load(d) { hud.load(d); },
      async setLayers(ids) { hud.setLayers(ids); },
      async press(pos) { held.add(pos); hud.pressAt(pos); },
      async release(pos) { held.delete(pos); hud.releaseAt(pos); },
      async advance(ms) { clock.advance(ms); },
      async reset(ids, isBase) {
        for (const pos of [...held]) { held.delete(pos); hud.releaseAt(pos); }
        clock.advance(6000);
        hud.setLayers([]);
        clock.advance(6000);
        if (ids !== null && ids !== undefined) hud.setLayers(isBase ? [] : [ids]);
      },
      async legends() {
        return hud.state.keyEls.map(e => ({
          tap: legend(e.querySelector(".tap")), hold: legend(e.querySelector(".hold")), shifted: legend(e.querySelector(".shifted")),
        }));
      },
      async lit() {
        const out = []; hud.state.keyEls.forEach((e, i) => { if (e.classList.contains("pressed")) out.push(i); }); return out;
      },
      async activators() {
        const out = []; hud.state.keyEls.forEach((e, i) => { if (e.classList.contains("activator")) out.push(i); }); return out;
      },
      async pills() {
        return [...board.querySelectorAll(".combo-pill")].map(p => ({
          tap: legend(p.querySelector(".combo-tap")), sub: legend(p.querySelector(".combo-sub")),
        }));
      },
    };

    const r = await cases.sweep(driver, data, {});
    return {
      checked: r.checked, failures: r.fail.length,
      byCheck: r.fail.reduce((m, f) => (m[f.check] = (m[f.check] || 0) + 1, m), {}),
      signature: r.fail.map(f => `${f.check}|${f.where}|${f.expected}|${f.actual}`).sort(),
      contested: r.contested.length, notes: r.notes.length,
    };
  };
})();
