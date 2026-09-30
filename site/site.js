/* zmk-layer-hud — the landing page's wiring: the boards, the frame the HUD runs in, the demo that
 * plays in it and the field a visitor types into. What turns events into the feed's messages, and
 * a compiled demo into those messages on time, is demo.js; this file only connects them.
 *
 * The frame is the HUD page as it ships (hud/index.html?embed), driven through its own
 * window.hud.receive, the dispatcher the feed's WebSocket uses. It never gets ?ws=: there is no
 * feed behind a public page. It takes no pointer events and no focus, so every control is here.
 */
(function () {
  "use strict";
  document.documentElement.classList.remove("nojs");

  const $ = id => document.getElementById(id);
  const card = $("demo"), holder = $("frame"), boardsEl = $("boards"), blurb = $("blurb");
  const playBtn = $("play"), field = $("type"), combosBox = $("combos");
  const heatButtons = Array.prototype.slice.call(document.querySelectorAll("[data-heat]"));
  const themeButtons = Array.prototype.slice.call(document.querySelectorAll("[data-theme]"));
  const reduced = !!(window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);

  const frame = document.createElement("iframe");
  frame.title = "The HUD, running in this page";
  frame.tabIndex = -1;
  holder.appendChild(frame);

  const state = {
    boards: [], board: null, hud: null, demo: null, played: false, height: 0,
    heat: "live", theme: storedTheme() || "dark", paused: reduced, typing: false, visible: !document.hidden, onScreen: true,
    queue: [],   // what was typed while a fresh frame loads
  };
  const cache = new Map();
  const fetchJSON = url => {
    if (!cache.has(url)) {
      cache.set(url, fetch(url).then(r => { if (!r.ok) throw new Error(`${url}: ${r.status}`); return r.json(); }));
    }
    return cache.get(url);
  };

  const receive = m => { if (state.hud) state.hud.receive(m); };
  const player = new demo.Player(m => { state.played = true; receive(m); });
  const translator = new demo.Translator(m => { if (state.hud) state.hud.receive(m); else state.queue.push(m); });

  // ---------- boards ----------

  /* A fresh frame for every board, and for typing after a demo: nothing of what was shown before
   * -- a key's glow, the counts, the speed, the clock of the last firmware position -- carries over. */
  function show(board, keepField) {
    player.stop();
    state.board = board;
    state.hud = null;
    state.played = false;
    for (const b of boardsEl.children) b.setAttribute("aria-pressed", String(b.dataset.id === board.id));
    blurb.textContent = board.blurb;
    playBtn.hidden = !board.demo;
    if (!keepField) { field.value = ""; translator.reset(""); state.queue = []; }
    frame.src = "hud/index.html?embed&board=" + encodeURIComponent(board.id);
  }

  frame.addEventListener("load", () => {
    const board = state.board, win = frame.contentWindow;
    if (!board || !win || !win.hud) return;
    Promise.all([fetchJSON(board.keymap), board.demo ? fetchJSON(board.demo) : null]).then(([keymap, capture]) => {
      if (state.board !== board || frame.contentWindow !== win) return;   // another board was chosen meanwhile
      state.hud = win.hud;
      state.demo = capture;
      state.hud.receive(keymap);
      state.hud.receive({ kind: "layers", ids: [] });   // live, as on a real board: the stack is the keyboard's
      state.hud.receive({ kind: "device", name: (capture && capture.device) || board.label });
      state.hud.setHeatmap(state.heat);
      state.hud.setPref("theme", state.theme);
      win.addEventListener("resize", () => requestAnimationFrame(fit));
      fit();
      for (const m of state.queue.splice(0)) state.hud.receive(m);
      // The first height set resizes the frame, and the HUD rebuilds its board on a resize, which
      // would take a key lit in the meantime with it: start once that has happened.
      requestAnimationFrame(() => requestAnimationFrame(run));
    }).catch(e => { blurb.textContent = `The demo could not load: ${e.message}.`; });
  });

  /* The frame as tall as the page in it. Only when that changed: every resize of the frame makes
   * the HUD rebuild its board, which a demo in progress would lose its lit keys to. */
  function fit() {
    const doc = frame.contentDocument;
    const h = doc && doc.body ? Math.ceil(doc.body.scrollHeight) : 0;
    if (h && h !== state.height) { state.height = h; frame.style.height = h + "px"; }
  }

  // ---------- the demo ----------

  function run() {
    if (state.hud && state.demo && !state.paused && !state.typing && state.visible && state.onScreen) {
      player.play(state.demo, { loop: true });
    }
    renderPlay();
  }
  function halt() { player.stop(); renderPlay(); }
  function renderPlay() {
    playBtn.textContent = player.playing ? "Pause" : "Play";
    playBtn.setAttribute("aria-label", player.playing ? "Pause the demo" : "Play the demo");
  }
  playBtn.addEventListener("click", () => {
    if (player.playing) { state.paused = true; halt(); return; }
    state.paused = false;
    state.typing = false;
    if (state.played) show(state.board, false);   // from the top, on a clean board
    else run();
  });

  // Out of sight, the demo stops; back in sight, it starts from the top.
  document.addEventListener("visibilitychange", () => {
    state.visible = !document.hidden;
    if (state.visible) run(); else halt();
  });
  if ("IntersectionObserver" in window) {
    new IntersectionObserver(entries => {
      const on = entries[entries.length - 1].isIntersecting;
      if (on === state.onScreen) return;
      state.onScreen = on;
      if (on) run(); else halt();
    }).observe(card);
  }

  // ---------- typing ----------

  field.addEventListener("focus", () => {
    state.typing = true;
    halt();
    if (state.played) show(state.board, true);   // the visitor's own session, not the demo's
  });
  field.addEventListener("blur", () => { state.typing = false; translator.blur(); renderPlay(); });
  field.addEventListener("keydown", e => translator.keydown(e));
  field.addEventListener("keyup", e => translator.keyup(e));
  field.addEventListener("input", e => {
    translator.input(e, field.value);
    if (field.value.length > 4000) { field.value = field.value.slice(-2000); translator.reset(field.value); }
  });
  field.addEventListener("compositionend", e => translator.compositionend(e, field.value));
  combosBox.addEventListener("change", () => { translator.combos = combosBox.checked; });

  for (const b of heatButtons) {
    b.addEventListener("click", () => {
      state.heat = b.dataset.heat;
      for (const o of heatButtons) o.setAttribute("aria-pressed", String(o === b));
      if (state.hud) state.hud.setHeatmap(state.heat);
    });
  }

  /* The keys light or dark. The frame's page keeps the choice itself, in its own storage on this
   * origin (hud.js setPref), so a board chosen next, and a visit after this one, start in it. */
  function storedTheme() {
    try { return (JSON.parse(localStorage.getItem("zmkhud.prefs") || "{}") || {}).theme || null; } catch (e) { return null; }
  }
  function renderTheme() {
    for (const b of themeButtons) b.setAttribute("aria-pressed", String(b.dataset.theme === state.theme));
  }
  for (const b of themeButtons) {
    b.addEventListener("click", () => {
      state.theme = b.dataset.theme;
      renderTheme();
      if (state.hud) state.hud.setPref("theme", state.theme);
    });
  }
  renderTheme();

  // ---------- code blocks ----------

  for (const pre of document.querySelectorAll("pre")) {
    const code = pre.querySelector("code");
    if (!code || !(navigator.clipboard && navigator.clipboard.writeText)) continue;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "copy";
    btn.textContent = "copy";
    btn.setAttribute("aria-label", "Copy this");
    btn.addEventListener("click", () => {
      navigator.clipboard.writeText(code.textContent).then(() => {
        btn.textContent = "copied";
        btn.classList.add("done");
        setTimeout(() => { btn.textContent = "copy"; btn.classList.remove("done"); }, 1500);
      }, () => { btn.textContent = "select it"; });
    });
    pre.appendChild(btn);
  }

  // ---------- start ----------

  fetchJSON("boards/index.json").then(index => {
    state.boards = index.boards || [];
    for (const board of state.boards) {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = board.label;
      b.dataset.id = board.id;
      b.setAttribute("aria-pressed", "false");
      b.addEventListener("click", () => { state.typing = false; show(board, false); });
      boardsEl.appendChild(b);
    }
    if (state.boards.length) show(state.boards[0], false);
  }).catch(e => { blurb.textContent = `The demo could not load: ${e.message}.`; });
})();
