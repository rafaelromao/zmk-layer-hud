/* zmk-layer-hud — the touch keyboard: the board types when its keys are touched (docs/touch.md).
 *
 * A finger on a key is a key going down at that key's ZMK position, and the virtual firmware
 * (engine.js) does with it what the keyboard would: hold-taps, layers, sticky keys, macros. What
 * it sends goes to the page's sink -- report snapshots for a desktop host, which presses the keys
 * at its operating system, or text for a tablet's keyboard (hidtext.js) -- and the board draws the
 * engine's layers, the keys under fingers, and what is latched.
 *
 * A sink comes from the host ({"kind":"touch","on":true,"sink":{...}}, sent through hud.receive,
 * typing then going back through the panel's bridge), or from a native shell or the remote page
 * calling hud.setTouch({output, repeat, pages, shortcuts, mouse, send}). Without one the board is
 * the HUD it always was -- and with one too, while the board is docked too big for its screen even
 * with its keys as small as touch.min_key_mm lets them be (hud.js dockFit, the "fit" hook).
 *
 * Touch typing counts nowhere: not the session, the WPM, the tallies. It warms the live heat
 * (touch.heat), except while a secret is being typed, and the typed-keys strip is put away while the
 * board types: it is the HUD's, not the keyboard's (hud.css html.touch).
 */
(function () {
  "use strict";
  const hud = window.hud, Engine = window.ZmkEngine, HidText = window.ZmkHidText;
  const board = document.getElementById("board");
  if (!hud || !Engine || !HidText || !board) return;
  const html = document.documentElement;
  const HEARTBEAT_MS = 500;      // a host lets a silent page's keys go after 1.5 s (docs/touch.md)
  const REFUSED_MS = 300;

  let data = null, sink = null, engine = null, driver = null, decoder = null;
  let view = { ids: null, down: [], latched: {}, mods: 0 };
  let problem = "";              // the host's word on why typing cannot happen (typing.state)
  let fits = true;               // the board fits the dock, its keys their size or smaller (hud.js dockFit)
  const pointers = new Map();    // pointerId -> {input, idx}
  let last = null, seq = 0, beat = null, deadTimer = null;

  const typing = () => !!(sink && engine);

  function setTouch(s) {
    // Whatever is held goes up through the sink that pressed it, before another (or none) takes over.
    if (driver) { driver.releaseAll(); driver.stop(); engine = driver = null; }
    sink = s || null;
    rebuild();
    hud.relayout();
  }

  function rebuild() {
    if (driver) { driver.releaseAll(); driver.stop(); }
    engine = driver = null;
    pointers.clear();
    clearInterval(beat); beat = null; last = null;
    view = { ids: null, down: [], latched: {}, mods: 0 };
    const usable = data && data.actions && !data.actions.error && data.touch && data.touch.enabled;
    html.classList.toggle("touch", !!sink && fits && !!usable);
    // Asked to type and cannot: the banner says why, rather than the board looking like a keyboard.
    hud.setTouchNote(!sink || !data || usable ? "" :
      !data.actions ? "a HUD only · this keymap says nothing of what its keys do (zmk-layer-hud keymap -o has it)" :
      data.actions.error ? `a HUD only · ${data.actions.error}` : "a HUD only · touch.enabled is 0");
    if (!sink || !fits || !usable) { hud.setTouchView(null); hud.relayout(); return; }
    engine = Engine.create(data.actions, { touch: data.touch, sink: {
      repeat: sink.repeat || "os", pages: sink.pages || [7, 12], mouse: sink.mouse || "none" } });
    driver = Engine.drive(engine, { now: () => Date.now(), setTimeout: (f, ms) => setTimeout(f, ms),
                                    clearTimeout: (t) => clearTimeout(t), emit });
    decoder = HidText.decoder({ altgr: !!data.altgr });
    // The keyboard's own layers, as the engine's start: the board stays on the layer it shows.
    driver.adopt((hud.state.live && hud.state.live.ids) || []);
    view.ids = engine.state().layers;
    show();
  }

  // ---------- what the engine says ----------

  function emit(events) {
    let changed = false;
    for (const e of events) {
      if (e.kind === "report") { report(e); changed = true; }
      else if (e.kind === "layers") { view.ids = e.ids; changed = true; }
      else if (e.kind === "latches") { view.latched = byIdx(e.latches); changed = true; }
      else if (e.kind === "unsupported") refuse(e.input);
    }
    if (changed) show();
  }

  function report(e) {
    view.mods = e.mods;
    const keyEvents = decoder.feed(e, e.t);
    if (sink.output === "text") {
      sendOps(keyEvents);
    } else {
      last = { kind: "hid", v: 1, mods: e.mods, keys: e.keys.slice(0, 10) };
      if (e.consumer && e.consumer.length) last.consumer = e.consumer.slice();
      send(last);
      // While anything is held, said again: a host that hears nothing lets the page's keys go.
      clearInterval(beat); beat = null;
      if (last.mods || last.keys.length || last.consumer) beat = setInterval(() => send(last), HEARTBEAT_MS);
    }
    // A dead key waits for its letter; alone, it is typed when its time is up.
    clearTimeout(deadTimer);
    if (decoder.pending()) {
      deadTimer = setTimeout(() => { const late = decoder.flush(Date.now()); if (sink && sink.output === "text") sendOps(late); },
                             (window.ZmkKeyNames ? window.ZmkKeyNames.DEAD_KEY_MS : 60) + 5);
    }
  }

  function send(msg) {
    if (!sink || !sink.send) return;
    try { sink.send(Object.assign({}, msg, { seq: ++seq })); } catch (x) { console.error(x); }
  }

  function sendOps(keyEvents) {
    const ops = HidText.textEvents(keyEvents, { shortcuts: sink.shortcuts !== false });
    if (ops.length) send({ kind: "ops", v: 1, ops });
  }

  function idxOf(pos) {
    const map = data.positions || {};
    if (map[String(pos)] !== undefined) return map[String(pos)];
    return Object.keys(map).length ? null : pos;
  }

  function byIdx(latches) {
    const out = {};
    for (const l of latches) {
      const idx = idxOf(l.pos);
      if (idx != null) out[idx] = l.state;
    }
    return out;
  }

  function refuse(pos) {
    const idx = pos == null ? null : idxOf(pos);
    const el = idx == null ? null : hud.state.keyEls[idx];
    if (!el) return;
    el.classList.add("refused");
    setTimeout(() => el.classList.remove("refused"), REFUSED_MS);
  }

  function note() {
    if (problem) return problem;
    const states = Object.values(view.latched);
    if (states.includes("locked")) return "touch · locked";
    if (states.includes("oneshot")) return "touch · one shot";
    if (pointers.size && view.ids && view.ids[0] !== (data.actions.default || 0)) return "touch · held";
    return "";
  }

  function show() {
    const held = [...pointers.values()];
    view.down = [...new Set(held.flatMap((p) => (p.idx != null ? [p.idx] : []).concat(p.keys || [])))];
    view.chips = held.filter((p) => p.input.combo != null).map((p) => p.input.combo);
    hud.setTouchView(Object.assign({}, view, { note: note() }));
  }

  // ---------- fingers ----------

  // The drawer keys a combo presses, which light while its chip is held.
  function comboKeys(i) {
    const c = data.actions.combos && data.actions.combos[i];
    return c ? c.positions.map(idxOf).filter((x) => x != null) : [];
  }

  // The key under a finger: a chip, the key element itself, or -- in the gap between keys -- the
  // nearest key within half a gap and a little, its rotation undone. A key the firmware reports no
  // position for is drawn, not typed.
  function hit(e) {
    const t = e.target && e.target.closest ? e.target : null;
    const chip = t && t.closest(".combo-chip");
    if (chip) return { input: { combo: Number(chip.dataset.combo) }, idx: null, keys: comboKeys(Number(chip.dataset.combo)) };
    const keyEl = t && t.closest(".key");
    const idx = keyEl ? Number(keyEl.dataset.idx) : keyAt(e.clientX, e.clientY);
    if (idx == null || Number.isNaN(idx)) return null;
    const g = hud.geometry();
    const pos = g && g.posOf[idx];
    return pos == null ? null : { input: { pos }, idx };
  }

  function keyAt(clientX, clientY) {
    const g = hud.geometry();
    if (!g) return null;
    const rect = board.getBoundingClientRect();
    const x = clientX - rect.left, y = clientY - rect.top;
    let best = null, bestD = Infinity;
    g.keys.forEach((k, idx) => {
      const a = -(k.r || 0) * Math.PI / 180;
      const dx = x - g.centers[idx].x, dy = y - g.centers[idx].y;
      const lx = dx * Math.cos(a) - dy * Math.sin(a), ly = dx * Math.sin(a) + dy * Math.cos(a);
      const d = Math.hypot(Math.max(0, Math.abs(lx) - (k.w * g.scale - g.gap) / 2),
                           Math.max(0, Math.abs(ly) - (k.h * g.scale - g.gap) / 2));
      if (d < bestD) { bestD = d; best = idx; }
    });
    return bestD <= g.gap / 2 + 2 ? best : null;
  }

  board.addEventListener("pointerdown", (e) => {
    if (!typing() || e.button > 0 || html.classList.contains("mini")) return;   // a mouse's left button is a finger
    const h = hit(e);
    if (!h) return;
    e.preventDefault();
    feedback(kindOf(h.input));
    try { board.setPointerCapture(e.pointerId); } catch (x) { /* a pointer the board cannot hold */ }
    pointers.set(e.pointerId, h);
    if (data.touch.heat && h.idx != null) hud.warmKey(h.idx);
    driver.down(h.input);
    show();
  });
  function lift(e) {
    const h = pointers.get(e.pointerId);
    if (!h) return;
    pointers.delete(e.pointerId);
    driver.up(h.input);
    show();
  }
  for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) board.addEventListener(type, lift);
  // A long press is a hold, not a menu or a selection.
  board.addEventListener("contextmenu", (e) => { if (typing()) e.preventDefault(); });
  for (const type of ["gesturestart", "dblclick"]) document.addEventListener(type, (e) => { if (typing()) e.preventDefault(); });

  // ---------- feedback ----------

  /* A click, and a buzz where the device has one, the moment a finger lands on a key: on the
   * press, as phone keyboards give it, not the release, and none while a key repeats. On by default
   * where the board is a touch screen's keyboard (docked), off elsewhere, and the side panel's
   * switch decides once it is used (hud.feedbackOn). Four clicks,
   * as Android's keyboard sounds: a key, space, delete and return. touch.sound is the click's volume
   * (0: silent), touch.haptics the buzz, which a browser gives on Android alone (navigator.vibrate).
   * A sink with feedback of its own -- the Android keyboard's, which buzzes and clicks as the
   * system and its settings say -- is asked instead. The clicks are made here, a few milliseconds of
   * tone and noise each, so there is no sound file to fetch; the audio starts on the first touch,
   * which is what lets a page make sound at all. */
  const CLICKS = { standard: [3200, 0.012], space: [1500, 0.02], delete: [2200, 0.016], "return": [1300, 0.022] };
  let audio = null, clicks = null;

  function kindOf(input) {
    const acts = data.actions;
    let a = null;
    if (input.combo != null) {
      const c = (acts.combos || [])[input.combo];
      a = c ? acts.table[c.action] : null;
    } else {
      for (const id of (view.ids && view.ids.length ? view.ids : [acts.default || 0])) {
        const slots = acts.layers[String(id)];
        const x = slots ? acts.table[slots[input.pos]] : null;
        if (x && x.op !== "trans") { a = x; break; }
      }
    }
    if (a && a.op === "ht") a = a.tap;
    const id = a && a.op === "kp" && a.key && a.key.page === 7 ? a.key.id : null;
    return id === 0x2A || id === 0x4C ? "delete" : id === 0x2C ? "space" : id === 0x28 || id === 0x58 ? "return" : "standard";
  }

  function feedback(kind) {
    if (hud.feedbackOn && !hud.feedbackOn()) return;     // the side panel's switch, else the mode's default
    if (sink.feedback) { try { sink.feedback(kind); } catch (x) { /* the shell's own */ } return; }
    const volume = Math.min(100, Number(data.touch.sound) || 0) / 100;
    if (volume > 0) click(kind, volume);
    if (data.touch.haptics && window.navigator && typeof window.navigator.vibrate === "function") {
      try { window.navigator.vibrate(kind === "standard" ? 8 : 12); } catch (x) { /* none here */ }
    }
  }

  function click(kind, volume) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    try {
      if (!audio) { audio = new AC(); clicks = makeClicks(audio); }
      if (audio.state === "suspended") audio.resume();
      const src = audio.createBufferSource(), gain = audio.createGain();
      src.buffer = clicks[kind] || clicks.standard;
      gain.gain.value = volume * 0.6;
      src.connect(gain);
      gain.connect(audio.destination);
      src.start();
    } catch (x) { /* a page that may make no sound */ }
  }

  // Each click: a tone at its pitch and a little noise, dying away in a quarter of its length.
  function makeClicks(ctx) {
    const out = {};
    for (const [kind, [freq, dur]] of Object.entries(CLICKS)) {
      const n = Math.ceil(ctx.sampleRate * (dur + 0.01)), b = ctx.createBuffer(1, n, ctx.sampleRate), d = b.getChannelData(0);
      let seed = 1;
      for (let i = 0; i < n; i++) {
        const t = i / ctx.sampleRate;
        seed = (seed * 16807) % 2147483647;
        d[i] = Math.exp(-t / (dur / 4)) * (0.6 * Math.sin(2 * Math.PI * freq * t) + 0.4 * (seed / 1073741823.5 - 1));
      }
      out[kind] = b;
    }
    return out;
  }

  // A page going away (a reload, the panel hidden, a tablet locked, a tablet's field left) lets
  // everything go: what is held and latched, and a dead key waiting for its letter, which would
  // otherwise be typed when its time is up -- into whatever field is open by then.
  function releaseAll() {
    if (!driver) return;
    pointers.clear();
    driver.releaseAll();
    clearTimeout(deadTimer);
    deadTimer = null;
    decoder = HidText.decoder({ altgr: !!(data && data.altgr) });
    show();
  }
  document.addEventListener("visibilitychange", () => { if (document.hidden) releaseAll(); });
  window.addEventListener("pagehide", releaseAll);

  // ---------- the page ----------

  hud.on("load", (d) => { data = d; rebuild(); });
  hud.on("layers", (ids) => { if (engine && engine.idle()) driver.adopt(ids); });
  // Minimized, the keys are put away: nothing stays held under them.
  hud.on("mini", (on) => { if (on) releaseAll(); });
  hud.on("fit", (f) => {
    if (f === fits) return;
    fits = f;
    rebuild();          // too big: whatever is held goes up, and the board types nothing
    hud.relayout();
  });
  hud.on("touch", (m) => {
    if (m.on === false) { setTouch(null); return; }
    // The panel's sink: typing goes back through its bridge.
    setTouch(Object.assign({}, m.sink || {}, { send: (msg) => hud.post(msg) }));
  });
  hud.on("typing", (m) => {
    problem = !m.state || m.state === "ok" ? "" :
      m.state === "off" ? "touch typing is off" : `touch cannot type · ${m.fix || m.state}`;
    if (engine) show();
  });
  hud.setTouch = setTouch;
  hud.touch = { state: () => (engine ? engine.state() : null), releaseAll };

  // Dev, from disk or a local server: &touch attaches the board as a touch keyboard along the bottom
  // of the window, typing into the console; its button detaches it to the HUD and back. A browser
  // cannot measure its screen: keys are drawn at CSS's 96 pixels to the inch unless &mm= says how
  // many pixels a millimetre is (a 13" MacBook Air's 1470 points over 303 mm: &mm=4.85). &dock
  // docks the HUD without typing.
  const params = new URLSearchParams(location.search);
  const local = location.protocol === "file:" || ["127.0.0.1", "localhost", "[::1]", "::1"].includes(location.hostname);
  if (local && params.get("touch") !== null) {
    const pxPerMm = Number(params.get("mm")) || 96 / 25.4;
    const console_ = { output: "hid", repeat: "synthetic", send: (m) => console.log("touch:", JSON.stringify(m)) };
    let attached = false;
    const dock = () => hud.setDock({ on: true, split: true, px_per_mm: pxPerMm, width: window.innerWidth,
                                     max_height: Math.round(window.innerHeight * 0.6) });
    const attach = (on) => {
      attached = on;
      html.classList.toggle("bottom", on);
      setTouch(on ? console_ : null);
      if (on) dock(); else hud.setDock({ on: false });
    };
    attach.title = "detach: the floating HUD again";
    hud.setAttach(attach);
    attach(true);
    window.addEventListener("resize", () => { if (attached) dock(); });
  } else if (local && params.get("dock") !== null) {
    hud.setDock({ on: true, max_height: Number(params.get("dock")) || window.innerHeight });
  }
})();
