/* zmk-layer-hud — renderer + keymap resolver.
 *
 * The page carries no keymap: the host sends one, built at runtime from a keymap-drawer YAML by
 * host/keymap.py (physical layout, layers, combos, the ZMK layer id table, optional hints), and
 * sends it again whenever that file changes. The host drives the page through window.hud:
 *   hud.load(keymap)                   the {"kind":"keymap", ...} message
 *   hud.setLayers([ids])               the keyboard's active ZMK layer ids (ground truth; the
 *                                      firmware's layer signal, decoded by host/hudfeed.py)
 *   hud.key({type, chars, name, flags}) one key or modifier event, decoded from the same reports;
 *                                      with `combos` (true/false) it is typing sent in instead
 *                                      (poke, a WebSocket client), placed by technique rather than
 *                                      on the keyboard's layers, combos used only if it says so
 *   hud.pressAt(pos[, sent]) / hud.releaseAt(pos)  a key going down / up by ZMK position
 *                                      (firmware `positions;`): the exact key lights while held,
 *                                      then fades; characters then only feed the strip. `sent`:
 *                                      a press sent in, lit the same and never counted in a session
 *   hud.press([idx...])                light keys directly (tests)
 *   hud.setHeatmap(mode)               what the keys glow with: live, session or off
 *   hud.receive(message)               any feed message (the WebSocket's dispatcher), including
 *                                      {"kind":"session"}: the host's active session, which the
 *                                      page reports the keyboard's own counts to
 *   hud.stats                          the counts behind the bar and the session heatmap (tests)
 *
 * Every keystroke drawn is also counted (the ledger, once nothing can take it back), and typing
 * is timed: the bar above the panel shows words per minute, accuracy and how the keys are used.
 *
 * Two modes:
 *   live      after the first setLayers: the stack is exactly what the keyboard reports; a key is
 *             resolved on that stack (combos included). A key that cannot be placed there is
 *             attributed by inference and drawn dashed ("inferred").
 *   emulated  before any setLayers (firmware without the module): only the base layer is known
 *             and everything else is inferred from the typed characters, guided by `extras`.
 *
 * In a browser (http://) it accepts real key events and `?keymap=keymap.json` loads a dumped
 * message, so the page can be developed without a host. With `?embed` it is framed by another page
 * (the landing page's demo), which owns the keyboard and drives it through window.hud.
 */
(function () {
  "use strict";

  const GAP = 6;             // px between keys at the drawn scale
  // Every timing worth tuning comes from the config's `hud:` section (host/keymap.py fills the
  // defaults in); these are only the fallbacks for a keymap message without it. What is left as a
  // constant (HEAT_TICK_MS, WPM_TICK_MS) is how often the page redraws, not anything a user would
  // notice.
  const DEFAULTS = { opacity: 86, press_ms: 320, release_ms: 60, held_timeout_ms: 5000, momentary_ms: 700, combo_slack_ms: 20, activator_ms: 400,
    positions_fresh_ms: 3000, combo_pill_ms: 1000, sequence_ms: 200, sequence_max: 6, one_shot_ms: 450, heatmap_ms: 3000,
    wpm_window_ms: 10000, wpm_idle_ms: 3000, stats_bar: 1 };
  const T = name => (state.data && state.data.hud && state.data.hud[name] != null) ? state.data.hud[name] : DEFAULTS[name];
  const recentPos = [];

  // Named keys → the legend text keymap-drawer keymaps usually use for them.
  const NAMED = {
    space: "␣", return: "↵", escape: "⎋", delete: "⌫", forwarddelete: "⌦", tab: "⇥",
    left: "←", right: "→", up: "↑", down: "↓", home: "⇱", end: "⇲",
    pagedown: "⇟", pageup: "⇞", insert: "⎀", capslock: "⇪",
  };
  // The function keys the decoder names (f1..f24): without them a keymap's F5 resolves to no
  // token at all and the key never lights when the firmware reports no positions.
  for (let i = 1; i <= 24; i++) NAMED["f" + i] = "F" + i;
  // Alternative spellings a drawer file may use for the same named key.
  const ALIASES = { "␣": ["space", "spc", "SPACE"], "↵": ["⏎", "enter", "ret", "RET"], "⎋": ["esc", "ESC"],
    "⌫": ["bspc", "BSPC", "backspace"], "⌦": ["del", "DEL"], "⇥": ["tab", "TAB", "↹"] };
  // Modifier flags → the glyph a hold legend uses for them (home-row mods light while held).
  const MOD_GLYPH = { shift: "⇧", ctrl: "⌃", alt: "⌥", cmd: "⌘" };

  const state = {
    data: null,
    baseLayers: [],           // emulated mode: just the base layer
    live: null,               // {ids: [..], at} once the keyboard has reported its layers
    inferred: false,          // last key was placed by inference while live
    momentary: [],            // [{layer, until}]  (inference only)
    oneShot: null,            // layer name        (inference only)
    mods: {},                 // flag -> true while held
    device: "",               // the keyboard's HID product name
    activatorOf: {},          // drawn layer -> the key idx that brought it in (positions)
    drawnSince: {},           // drawn layer -> when it appeared (applyLayers)
    pendingLayers: null,      // a layer change held back for a flash (setLayers)
    pendingDue: null,         // ...and when it applies, fixed at the first drop
    held: new Set(),          // key idx currently down, by reported position
    keyEls: [],
    timers: new Map(),
    scale: 1,
    heat: new Map(),          // key idx -> {h, t}: the live heatmap (see bumpHeat)
    heatTimer: null,
    heatMode: "live",         // what the keys glow with: live | session | off
    secure: false,            // macOS secure input: a secret is being typed, and nothing of it shows
    demo: false,              // a GIF still (&demo=N): drawn as it always was, no heat, no counts
    ledger: [],               // keystrokes drawn and not counted yet (see "ledger")
    ledgerTimer: null,
    ledgerDue: 0,
    comboEntry: null,         // the ledger's entry for the combo whose pill is up
    posOf: [],                // drawer idx -> ZMK position
    local: emptyTally(),      // everything counted since the page loaded, from any source
    unsent: emptyTally(),     // the keyboard's own counts, not yet handed to a session
    typing: newTyping(),      // live WPM, over everything shown
    ownTyping: newTyping(),   // ...and over the keyboard's own typing, for the session's peak
    wpmTimer: null,
    session: null,            // the host's active session ({"kind":"session"}), when it keeps one
    inflight: [],             // counts sent to it that it has not said it has
    seq: 0,
    tallyTimer: null,
    viewCache: null,
  };

  const $ = id => document.getElementById(id);
  const base = () => state.data ? state.data.base : null;
  const extras = () => (state.data && state.data.extras) || {};

  // ---------- rendering ----------

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  // Keys are placed from keymap-drawer's physical layout (centre x/y, width, height, rotation in
  // the drawer's units), scaled so the whole board spans the panel width.
  function buildBoard() {
    const lay = state.data.layout;
    const board = $("board");
    board.innerHTML = "";
    state.keyEls = [];
    const width = board.clientWidth || 570;
    const scale = width / lay.width;
    state.scale = scale;
    board.style.height = Math.ceil(lay.height * scale) + "px";
    // Legends scale with the typical key height (see --kh in hud.css).
    const kh = Math.min(...lay.keys.map(k => k.h)) * scale - GAP;
    board.style.setProperty("--kh", Math.max(20, kh) + "px");
    lay.keys.forEach((k, idx) => {
      const e = el("div", "key");
      e.dataset.idx = idx;
      e.title = `#${idx}`;
      const w = k.w * scale - GAP, h = k.h * scale - GAP;
      e.style.left = (k.x * scale - w / 2) + "px";
      e.style.top = (k.y * scale - h / 2) + "px";
      e.style.width = w + "px";
      e.style.height = h + "px";
      if (k.r) e.style.transform = `rotate(${k.r}deg)`;
      if (h < 50) e.classList.add("short");
      e.appendChild(el("div", "shifted"));
      e.appendChild(el("div", "tap"));
      e.appendChild(el("div", "hold"));
      board.appendChild(e);
      state.keyEls[idx] = e;
    });
    paintHeat();   // the glow lives by index, not on the elements just replaced
  }

  // ZMK layer id → keymap entry ({name, drawer, label, cls}); falls back to the YAML order.
  function zl(id) {
    const t = state.data.zmk_layers || {};
    if (t[String(id)]) return t[String(id)];
    const name = state.data.layer_order[id];
    return name ? { id, name, drawer: name, label: name, cls: id === 0 ? "off" : "momentary" } : null;
  }

  // The live stack as drawer layer names, top first: higher ZMK ids win, undrawn layers are
  // skipped, duplicates (two ids drawn by one layer) collapse; the base layer is always last.
  function liveStack() {
    const names = [];
    const ids = state.live.ids.slice().sort((a, b) => b - a);
    for (const id of ids) {
      const z = zl(id);
      if (z && z.drawer && !names.includes(z.drawer)) names.push(z.drawer);
    }
    if (!names.includes(base())) names.push(base());
    return names;
  }

  function baseStack() {
    // top first
    if (state.live) return liveStack();
    const s = [];
    for (let i = state.baseLayers.length - 1; i >= 0; i--) s.push(state.baseLayers[i]);
    if (!s.includes(base())) s.push(base());
    return s;
  }

  function stack() {
    const s = [];
    if (state.oneShot) s.push(state.oneShot);
    for (let i = state.momentary.length - 1; i >= 0; i--) s.push(state.momentary[i].layer);
    for (const name of baseStack()) if (!s.includes(name)) s.push(name);
    return s;
  }

  // The binding that wins at idx, walking the stack through transparent keys.
  function resolveBinding(idx, layersTopFirst) {
    for (const name of layersTopFirst) {
      const k = state.data.layers[name] && state.data.layers[name][idx];
      if (!k) continue;
      if (k.type === "trans") continue;
      return { key: k, layer: name };
    }
    return null;
  }

  // A legend is text, or the SVG keymap-drawer draws for a $$glyph$$ (sent with the keymap). The
  // glyph replaces the text rather than joining it: a key's `tap` carries the glyph's own text
  // spelling so something still shows when the SVG could not be resolved.
  function legendHTML(text, glyph) {
    const svg = glyph && state.data.glyphs && state.data.glyphs[glyph];
    return svg ? `<span class="glyph">${svg}</span>` : null;
  }
  function setLegend(el, text, glyph) {
    const html = legendHTML(text, glyph);
    if (html) el.innerHTML = html; else el.textContent = text || "";
  }
  function fit(tapEl, text, glyph) {
    setLegend(tapEl, text, glyph);
    tapEl.classList.remove("long", "mid");
    if (legendHTML(text, glyph)) return;
    if (text.length > 4) tapEl.classList.add("long");
    else if (text.length > 2) tapEl.classList.add("mid");
  }

  // The drawer key at a reported ZMK position. A position the keymap does not place lights
  // nothing: lighting the key that happens to share the number is worse than lighting none, and
  // the feed already says so ("key position N is not in the keymap's … drawer keys"). A message
  // with no map at all (a hand-written demo) still means position = index.
  function idxAt(pos) {
    const map = (state.data && state.data.positions) || {};
    if (map[String(pos)] !== undefined) return map[String(pos)];
    return Object.keys(map).length ? -1 : Number(pos);
  }

  function activatorsOf(layer) {
    return state.data.activators.filter(a => a.layer === layer).map(a => a.idx);
  }

  function renderKeys() {
    const layers = stack();
    const activators = new Set();
    for (const m of state.momentary) for (const a of activatorsOf(m.layer)) activators.add(a);
    if (state.oneShot) for (const a of activatorsOf(state.oneShot)) activators.add(a);
    if (state.live) {
      // The key that reached each live layer lights as its activator: the one pressed right
      // before the layer appeared when positions are reported, else every key that can reach it.
      for (const name of liveStack()) {
        if (name === base()) continue;
        if (state.activatorOf[name] !== undefined) { if (state.activatorOf[name] !== null) activators.add(state.activatorOf[name]); }
        else for (const a of activatorsOf(name)) activators.add(a);
      }
    }
    const heldMods = Object.keys(state.mods).filter(f => state.mods[f]).map(f => MOD_GLYPH[f]).filter(Boolean);
    // Shift is a modifier, not a layer, so nothing else on the board would show it. Caps word and
    // caps line have their own drawn layer and arrive as a layer change; plain shift, held or
    // tapped as a one-shot, only ever reaches us as this flag. A single lowercase letter is the
    // whole of what it changes: \p{Ll} so the accented alphabets shift too (á -> Á), and a glyph,
    // a word or a symbol is left alone, because shift does not make ␣ or `nav` into anything.
    const shifting = !!state.mods.shift;
    const shiftLegend = t => (shifting && typeof t === "string" && /^\p{Ll}$/u.test(t)) ? t.toUpperCase() : t;
    state.data.layout.keys.forEach((k, idx) => {
      const e = state.keyEls[idx];
      const r = resolveBinding(idx, layers);
      e.classList.remove("trans", "blank", "ghost", "held", "activator", "mod");
      if (!r) {
        e.classList.add("blank");
        fit(e.querySelector(".tap"), "");
        e.querySelector(".hold").textContent = "";
        e.querySelector(".shifted").textContent = "";
        return;
      }
      const top = layers[0];
      const topKey = state.data.layers[top] && state.data.layers[top][idx];
      if (r.layer !== top && topKey && topKey.type === "trans") e.classList.add("trans");
      if (r.key.type === "blank") e.classList.add("blank");
      if (r.key.type === "ghost") e.classList.add("ghost");
      if (r.key.type.startsWith("held")) e.classList.add("held");
      if (activators.has(idx)) e.classList.add("activator");
      // A held modifier lights the keys that carry it: home-row mods (hold legend) and the
      // modifier keys themselves, including a sticky shift that was tapped (tap legend).
      if (heldMods.length && heldMods.some(g => (r.key.hold && r.key.hold.includes(g)) || r.key.tap === g)) e.classList.add("mod");
      fit(e.querySelector(".tap"), shiftLegend(r.key.tap) || "", r.key.glyph);
      setLegend(e.querySelector(".hold"), r.key.hold, r.key.glyph_hold);
      setLegend(e.querySelector(".shifted"), r.key.shifted, r.key.glyph_shifted);
    });
  }

  // A drawer layer's banner label: the ZMK table's label when one id is drawn by it, else its name.
  function layerLabel(name) {
    const t = state.data.zmk_layers || {};
    for (const z of Object.values(t)) if (z.drawer === name) return z.label;
    return name;
  }

  // Emulated mode: only the base layer is known.
  function codeSummary() {
    return { name: layerLabel(base()), cls: "off", sub: "" };
  }

  // Live mode: the highest active layer names the banner; the sub line lists the whole set.
  function liveSummary() {
    const ids = state.live.ids.slice().sort((a, b) => b - a);
    const entries = ids.map(zl).filter(Boolean);
    const names = entries.map(z => z.name);
    if (!entries.length) return { name: layerLabel(base()), cls: "off", sub: "" };
    // A vim layer under a held layer keeps the vim tint on the board; the banner names the top
    // layer and, only when several are active, lists the whole set beside it.
    const top = entries[0];
    const vim = entries.find(z => (z.cls || "").startsWith("vim"));
    const cls = top.cls === "momentary" && vim ? "momentary" : (top.cls || "momentary");
    const sub = entries.length > 1 ? names.join(" · ") : "";
    return { name: top.label, cls, sub };
  }

  function baseSummary() { return state.live ? liveSummary() : codeSummary(); }

  // What to print on the banner: an inferred held/one-shot layer on top of the base, else the base.
  function activeSummary() {
    const b = baseSummary();
    const top = state.oneShot || (state.momentary.length ? state.momentary[state.momentary.length - 1].layer : null);
    if (!top) return b;
    return { name: layerLabel(top), cls: "momentary",
      sub: (state.oneShot ? "one shot" : "held") + (state.live ? " (inferred)" : "") + " · over " + b.name.toLowerCase() };
  }

  function renderBanner() {
    const a = activeSummary();
    $("layer").className = a.cls + (state.inferred ? " inferred" : "");
    $("layerName").textContent = a.name;
    $("layerSub").textContent = state.secure ? "secure input · typing hidden" : a.sub;
    $("board").className = a.cls;
  }

  /* macOS's secure input (host/hudfeed.py SecureInput): a secret is being typed, and the feed has
   * stopped passing on keys. What is still on screen of the typing before it goes too -- the
   * strip, the keys lit, their glow, the macro memory -- and the banner says why the board has
   * stopped following. Anything that arrives anyway (sent in) is drawn nowhere until it ends. */
  function setSecure(on) {
    on = !!on;
    if (on === state.secure) return;
    state.secure = on;
    document.body.classList.toggle("secure", on);
    if (on) {
      if (window.keys && window.keys.clear) window.keys.clear();
      for (const id of state.timers.values()) clearTimeout(id);
      state.timers.clear();
      state.held.clear();
      for (const e of state.keyEls) if (e) e.classList.remove("pressed", "combo", "inferred", "combo-key");
      if (state.comboShown) { state.comboShown.remove(); state.comboShown = null; }
      recent.length = 0;
      state.heat.clear();
      paintHeat();
    }
    if (state.data) renderBanner();
    renderStats();
  }

  function renderFeed() {
    const f = $("feed");
    if (!f) return;
    // Only the two waiting states are worth a line; once the keyboard reports, say nothing.
    if (!state.data) { f.textContent = "waiting for the keymap…"; f.className = ""; return; }
    f.textContent = state.live ? "" : "waiting for the keyboard's layers…";
    f.className = state.live ? "live" : "";
  }

  function render() {
    if (!state.data) { renderFeed(); renderStats(); return; }
    renderBanner(); renderKeys(); renderFeed();
    paintSessionHeat(); renderStats();   // both follow the layer on screen
  }

  // The corner text: the config's title, else the keyboard's own name, else the file's.
  function renderTitle() {
    const t = $("title");
    if (!t) return;
    const d = state.data || {};
    t.textContent = d.title || state.device || (d.source ? d.source.split("/").pop().replace(/\.ya?ml$/, "") : "");
  }

  // Tell a native host how tall the page wants to be (the layout decides), and how wide the
  // config says. Hosts that listen (host/macos/panel.py) resize their window to it.
  function postSize() {
    const width = (state.data && state.data.hud && state.data.hud.width) || null;
    const height = Math.ceil(document.body.scrollHeight);
    try { window.webkit.messageHandlers.zmkhud.postMessage(JSON.stringify({ kind: "size", width, height })); } catch (e) { /* not WebKit */ }
  }

  // ---------- heat ----------

  /* The live heatmap: a key glows when it goes down and cools over hud.heatmap_ms, the way QMK's
   * typing heatmap warms a key's LED. A press closes HEAT_STEP of the gap to full heat, so a key
   * struck again and again reads hotter than one struck once without ever saturating, and heat
   * falls at one rate from wherever it was left: a key pressed once is cold after heatmap_ms, one
   * that was hammered stays warm up to 1/HEAT_STEP times as long.
   *
   * Heat is kept here by drawer index, not on the key elements: buildBoard replaces all of them on
   * a keymap reload, a resize and every WebSocket reconnect (the Hub replays the keymap), and a
   * glow must not go out because the socket blinked. One timer serves every key and runs only
   * while one is warm. Not requestAnimationFrame: the tests' clock runs rAF inline, where a loop
   * that re-arms itself would never return. The tick is coarse; the overlay's opacity transition
   * (hud.css .key::before) is what makes the fade smooth. */
  const HEAT_TICK_MS = 100;
  const HEAT_STEP = 0.4;      // of the gap to full heat, closed by one press
  const HEAT_MAX = 0.55;      // the overlay's opacity at full heat: the legends must stay readable
  const HEAT_LEVELS = 20;     // opacity is written in this many steps, so a fading key is touched ~20 times
  const HEAT_GAMMA = 0.6;     // drawn as heat^0.6: a key pressed once still shows, not a faint blush

  function heatAt(e, now) {
    const ms = T("heatmap_ms");
    if (!(ms > 0)) return 0;
    // A clock that went backwards (sleep, a time change) must not heat a key up.
    return Math.max(0, e.h - Math.max(0, now - e.t) * HEAT_STEP / ms);
  }

  function bumpHeat(idx) {
    if (state.demo || state.heatMode !== "live" || !(T("heatmap_ms") > 0) || !state.keyEls[idx]) return;
    const now = Date.now();
    const e = state.heat.get(idx);
    const v = e ? heatAt(e, now) : 0;
    state.heat.set(idx, { h: 1 - (1 - v) * (1 - HEAT_STEP), t: now });
    paintKeyHeat(idx, now);
    armHeat();
  }

  function armHeat() {
    if (state.heatTimer === null && state.heat.size) state.heatTimer = setTimeout(heatTick, HEAT_TICK_MS);
  }

  function heatTick() {
    state.heatTimer = null;
    const now = Date.now();
    for (const idx of [...state.heat.keys()]) paintKeyHeat(idx, now);
    armHeat();
  }

  // The key's --heat, the overlay's opacity, written only when its step changes. A key that has
  // cooled is forgotten and the property cleared, not left at 0.
  function paintKeyHeat(idx, now) {
    const e = state.heat.get(idx);
    const v = e ? heatAt(e, now) : 0;
    if (e && v <= 0) state.heat.delete(idx);
    const el = state.keyEls[idx];
    if (!el) return;
    const step = Math.ceil(Math.pow(v, HEAT_GAMMA) * HEAT_LEVELS);   // ceil: a key still warm never paints cold
    const value = step > 0 ? String(Math.round(step / HEAT_LEVELS * HEAT_MAX * 1000) / 1000) : "";
    if (el.style.getPropertyValue("--heat") !== value) el.style.setProperty("--heat", value);
  }

  // Every key, onto a board buildBoard has just replaced.
  function paintHeat() {
    const now = Date.now();
    state.keyEls.forEach((el, idx) => paintKeyHeat(idx, now));
    paintSessionHeat();
  }

  /* The session's heatmap: how often each key has been pressed, on the layer on screen. Six
   * steps of ln(1+count)/ln(1+most), the most-pressed key of that layer being the top step: a
   * linear scale would leave Space and E alone lit and every other key the same pale. Only a key
   * whose binding is the top layer's own shows its count -- a transparent key draws the layer
   * underneath, whose counts are nearly always the largest, and would light a layer up with heat
   * that was never its own. The counts are the ledger's, so this is the live heatmap summed. */
  const SESSION_LEVELS = 6;
  function paintSessionHeat() {
    if (!state.data || !state.keyEls.length) return;
    const layers = stack(), top = layers[0];
    const counts = (state.heatMode === "session" && view().presses[top]) || {};
    let most = 0;
    for (const pos in counts) most = Math.max(most, counts[pos]);
    state.keyEls.forEach((el, idx) => {
      let level = 0;
      if (most) {
        const r = resolveBinding(idx, layers), pos = zmkPos(idx);
        const n = r && r.layer === top && pos !== null ? counts[pos] || 0 : 0;
        if (n) level = Math.max(1, Math.ceil(Math.log1p(n) / Math.log1p(most) * SESSION_LEVELS));
      }
      for (let i = 1; i <= SESSION_LEVELS; i++) if (i !== level && el.classList.contains("hs" + i)) el.classList.remove("hs" + i);
      if (level) el.classList.add("hs" + level, "heated");
      else if (el.classList.contains("heated")) el.classList.remove("heated");
    });
  }

  // live | session | off. Leaving live lets its glow go at once rather than fade under the other.
  // A choice made here is the host's to keep (it survives a restart, and `zmk-layer-hud heatmap`
  // reads it); one the host sends is only applied.
  const HEAT_MODES = ["live", "session", "off"];
  function setHeatmap(mode, fromHost) {
    if (!HEAT_MODES.includes(mode) || mode === state.heatMode) return;
    state.heatMode = mode;
    if (mode !== "live") state.heat.clear();
    paintHeat();
    renderStats();
    if (!fromHost) postToHost({ kind: "heatmap", v: 1, mode });
  }

  // ---------- ledger ----------

  /* What the board drew, counted once. A keystroke the page lights goes in here first and is
   * counted only when nothing can take it back any more: a press once no combo can claim it, a
   * combo once no longer one can supersede it, a key lit from a report alone once its position can
   * no longer arrive and no macro absorb it. Counted any sooner, a report that beats its own
   * position counts its keystroke twice, a guess the position corrects counts the wrong key, and a
   * macro's steps count as the keys they spell.
   *
   * Counts go two ways: `local`, everything the page has been shown, a demo included, and
   * `unsent`, the keyboard's own alone. Typing sent in (poke, a demo, a WebSocket client) and a
   * rehearsal's synthetic keys light the board all the same, and never reach a session. */
  function emptyTally() {
    return { presses: {}, combos: {}, chars: 0, deleted: 0, active_ms: 0, active_net: 0, peak_wpm: 0 };
  }
  function bump(map, layer, key) {
    const m = map[layer] || (map[layer] = {});
    m[key] = (m[key] || 0) + 1;
  }
  // The drawer key's ZMK position, which is what a session is kept by; null for a key the
  // keymap places nowhere the firmware could report.
  function zmkPos(idx) {
    return state.posOf[idx] !== undefined ? state.posOf[idx] : null;
  }
  const comboKey = idxs => idxs.map(zmkPos).sort((a, b) => a - b).join(",");
  const comboTerm = () => (state.data.combo_term || 50) + T("combo_slack_ms");

  // The keyboard's own typing, as opposed to typing sent in (which says whether combos count) or
  // a rehearsal's (synthetic).
  const fromKeyboard = ev => ev.sent !== true && typeof ev.combos !== "boolean" && !ev.synthetic;

  function ledgerAdd(entry) {
    if (state.demo) return entry;
    state.ledger.push(entry);
    armLedger();
    return entry;
  }

  function armLedger() {
    if (!state.ledger.length) return;
    const due = Math.min(...state.ledger.map(e => e.due));
    if (state.ledgerTimer !== null && state.ledgerDue <= due) return;
    clearTimeout(state.ledgerTimer);
    state.ledgerDue = due;
    state.ledgerTimer = setTimeout(() => { state.ledgerTimer = null; ledgerCommit(Date.now()); armLedger(); },
                                   Math.max(0, due - Date.now()));
  }

  // Count what is due (everything, with `all`: a keymap about to be replaced).
  function ledgerCommit(now, all) {
    const keep = [];
    let counted = false;
    for (const e of state.ledger) {
      if (!all && e.due > now) { keep.push(e); continue; }
      if (e.cancelled) continue;
      countEntry(e);
      counted = true;
    }
    state.ledger = keep;
    if (counted) { state.viewCache = null; armTally(); paintSessionHeat(); renderStats(); }
  }

  function countEntry(e) {
    const tallies = e.eligible ? [state.local, state.unsent] : [state.local];
    // A key counts where its binding came from on the layers that were up when it went down: a
    // transparent key on a held layer typed the layer underneath's legend, and counts there.
    const on = (idx, layers) => { const r = resolveBinding(idx, layers); return r ? r.layer : layers[0]; };
    if (e.kind === "press") {
      for (const t of tallies) bump(t.presses, on(e.idx, e.stack), e.pos);
    } else if (e.kind === "combo") {
      for (const t of tallies) bump(t.combos, e.layer, e.key);
    } else {   // a guess: the keys a report lit, with no position to say so
      for (const idx of e.idxs) {
        const pos = zmkPos(idx);
        if (pos !== null) for (const t of tallies) bump(t.presses, on(idx, e.stack), pos);
        bumpHeat(idx);   // certain now, so it may glow: a guess taken back never did
      }
      if (e.idxs.length > 1 && e.idxs.every(i => zmkPos(i) !== null)) for (const t of tallies) bump(t.combos, e.layer, comboKey(e.idxs));
    }
  }

  // Keys a report lit, entered against the record remember() keeps for the macro and pressAt
  // lookups: unlight() cancels the guess, and so does any position going down.
  function guess(rec, idxs, layer, ev) {
    if (!idxs.length) return;
    const now = Date.now();
    rec.guess = ledgerAdd({ kind: "guess", idxs: idxs.slice(), layer, t: now,
                            stack: [layer].concat(stack().filter(l => l !== layer)),
                            due: now + Math.max(comboTerm(), T("sequence_ms")), eligible: fromKeyboard(ev) });
  }

  /* ZMK sends a layer's frame after the key that decided its hold: a hold-tap turns into a hold
   * when the next key goes down, and that key's position is sent first. Counted on the stack it
   * went down on, the first key typed on every held layer would count on the one below. So when a
   * layer comes up, a press still waiting that followed the key now holding it is moved onto the
   * new stack. A layer going away moves nothing: the key before it was typed with it up. */
  function recredit(gained, now) {
    const term = comboTerm(), layers = stack();
    for (const e of state.ledger) {
      if (e.kind !== "press" || e.cancelled || now - e.t > term) continue;
      if (gained.some(p => e.t > p.t && e.idx !== p.idx)) e.stack = layers;
    }
  }

  // ---------- typing speed ----------

  /* Words per minute the way QMK's wpm.c counts them: characters over five, over a sliding
   * window (wpm_window_ms), less what was deleted. A pause longer than wpm_idle_ms starts the
   * window over, so a burst is measured from its own start instead of ramping up out of the
   * silence before it, and the window is never taken as shorter than WPM_FLOOR_MS -- two quick
   * keys are not 300 wpm. A session's time is the typing in it: the gaps between keys, no pause
   * longer than wpm_idle_ms, and not the moment before a burst's first key.
   *
   * Only text counts: printable characters, Space, Return and Tab; Backspace and Delete take one
   * away, five with a modifier (a word). A chord with ⌘ or ⌃ is a command; ⌥ counts when it typed
   * a character. The decoder never reports auto-repeat, so a Backspace held down counts once. */
  const WPM_TICK_MS = 500;
  const WPM_FLOOR_MS = 2000;
  const PEAK_MIN_CHARS = 10;     // a peak is taken from a full window with at least this many
  function newTyping() { return { win: [], burst: 0, last: 0 }; }

  function textOf(ev) {
    if (ev.type !== "keyDown" || ev.repeat) return null;
    const f = (ev.flags && !Array.isArray(ev.flags)) ? ev.flags : {};
    if (ev.name === "delete" || ev.name === "forwarddelete") return { chars: 0, deleted: f.alt || f.ctrl || f.cmd ? 5 : 1 };
    if (f.cmd || f.ctrl) return null;
    if (ev.name === "return" || ev.name === "tab" || ev.name === "space") return { chars: 1, deleted: 0 };
    const c = ev.chars;
    return c && c.length === 1 && c >= " " && c !== "\x7f" ? { chars: 1, deleted: 0 } : null;
  }

  function wpmOf(tr, now) {
    const W = T("wpm_window_ms");
    while (tr.win.length && tr.win[0].t <= now - W) tr.win.shift();
    let chars = 0, net = 0;
    for (const e of tr.win) { chars += e.chars; net += e.net; }
    if (chars < 2) return 0;
    const span = Math.min(W, Math.max(WPM_FLOOR_MS, now - tr.burst));
    return Math.max(0, net) / 5 / (span / 60000);
  }

  function typed(tr, tally, k, now) {
    const net = k.chars - k.deleted;
    const gap = tr.last ? now - tr.last : -1;
    if (gap >= 0 && gap <= T("wpm_idle_ms")) { tally.active_ms += gap; tally.active_net += net; }
    else { tr.burst = now; tr.win = []; }
    tr.last = now;
    tr.win.push({ t: now, net, chars: k.chars });
    tally.chars += k.chars; tally.deleted += k.deleted;
    const wpm = wpmOf(tr, now);
    if (now - tr.burst >= T("wpm_window_ms") && tr.win.reduce((a, e) => a + e.chars, 0) >= PEAK_MIN_CHARS) {
      tally.peak_wpm = Math.max(tally.peak_wpm, Math.round(wpm));
    }
  }

  function statsKey(ev) {
    if (state.demo) return;
    const k = textOf(ev);
    if (!k) return;
    const now = Date.now();
    typed(state.typing, state.local, k, now);
    if (fromKeyboard(ev)) { typed(state.ownTyping, state.unsent, k, now); armTally(); }
    state.viewCache = null;
    armWpm();
    renderStats();
  }

  // The number on the bar decays while nobody types; it ticks only while there is one to show.
  function armWpm() {
    if (state.wpmTimer === null) state.wpmTimer = setTimeout(wpmTick, WPM_TICK_MS);
  }
  function wpmTick() {
    state.wpmTimer = null;
    const now = Date.now();
    wpmOf(state.typing, now); wpmOf(state.ownTyping, now);
    renderStats();
    if (state.typing.win.length || state.ownTyping.win.length) armWpm();
  }

  // ---------- the session ----------

  /* A host that keeps sessions (host/session.py) is sent the keyboard's own counts every
   * TALLY_MS, and sends back the active session: its counts so far, which session and which reset
   * of it (id, gen), and in `acks` the last batch of each page it has added. The macOS panel's
   * bridge is this page's alone; on a WebSocket, only a page given the panel's token (`tally=` in
   * its URL) sends anything, so a second page on the socket does not count the same keys twice. */
  const TALLY_MS = 2000;
  const INFLIGHT_MS = 15000;   // a batch the host has not acknowledged by then is not coming back
  const PAGE_ID = Math.random().toString(36).slice(2, 12);
  const TALLY_TOKEN = new URLSearchParams(location.search).get("tally");

  function bridge() {
    try { return window.webkit.messageHandlers.zmkhud || null; } catch (e) { return null; }
  }
  function tallies() { return !!(bridge() || (TALLY_TOKEN && hud.socket)); }
  function postToHost(obj) {
    const b = bridge();
    if (b) { b.postMessage(JSON.stringify(obj)); return true; }
    if (TALLY_TOKEN && hud.socket && hud.socket.readyState === 1) {
      hud.socket.send(JSON.stringify(Object.assign({ token: TALLY_TOKEN }, obj)));
      return true;
    }
    return false;
  }

  const hasCounts = t => Object.keys(t.presses).length || Object.keys(t.combos).length || t.chars || t.deleted || t.active_ms;
  function addTally(into, t) {
    for (const kind of ["presses", "combos"]) {
      for (const layer in t[kind]) for (const k in t[kind][layer]) {
        const m = into[kind][layer] || (into[kind][layer] = {});
        m[k] = (m[k] || 0) + t[kind][layer][k];
      }
    }
    for (const k of ["chars", "deleted", "active_ms", "active_net"]) into[k] += t[k] || 0;
    into.peak_wpm = Math.max(into.peak_wpm, t.peak_wpm || 0);
  }

  function armTally() {
    if (state.tallyTimer === null && hasCounts(state.unsent) && tallies()) {
      state.tallyTimer = setTimeout(() => { state.tallyTimer = null; sendTally(); }, TALLY_MS);
    }
  }
  // What the keyboard typed since the last batch, for the session it was typed in (`to`, else
  // the one active now).
  function sendTally(to) {
    if (!hasCounts(state.unsent)) return;
    const s = to || state.session;
    const batch = Object.assign({ kind: "tally", v: 1, page: PAGE_ID, seq: state.seq + 1,
                                  session: s ? s.id : null, gen: s ? s.gen : null, device: state.device || "" }, state.unsent);
    if (!postToHost(batch)) return;
    state.seq++;
    state.inflight.push({ seq: batch.seq, at: Date.now(), session: batch.session, counts: state.unsent });
    state.unsent = emptyTally();
    state.viewCache = null;
  }

  function applySession(m) {
    if (!m || typeof m.id !== "string") return;
    const was = state.session;
    if (was && (was.id !== m.id || was.gen !== m.gen)) {
      // Another session, or this one reset: what was typed before belongs to the one before.
      sendTally(was);
      state.inflight = [];
    }
    state.session = { id: m.id, gen: m.gen, name: m.name || "", presses: m.presses || {}, combos: m.combos || {},
                      totals: m.totals || {} };
    const acked = (m.acks || {})[PAGE_ID] || 0, now = Date.now();
    state.inflight = state.inflight.filter(b => b.seq > acked && now - b.at < INFLIGHT_MS &&
                                                (b.session === null || b.session === m.id));
    state.viewCache = null;
    if (HEAT_MODES.includes(m.heatmap)) setHeatmap(m.heatmap, true);
    paintSessionHeat();
    renderStats();
  }

  /* The counts the bar and the session heatmap show. With a host keeping a session: its counts,
   * and -- on the page that reports to it -- what is on the way and what is not sent yet. With
   * none (the demo, a page in a browser): everything this page has been shown since it loaded. */
  function view() {
    if (!state.session) return state.local;
    if (state.viewCache) return state.viewCache;
    const s = state.session, t = s.totals;
    const out = { presses: {}, combos: {}, chars: t.chars || 0, deleted: t.deleted || 0, active_ms: t.active_ms || 0,
                  active_net: t.active_net || 0, peak_wpm: t.peak_wpm || 0 };
    addTally(out, { presses: s.presses, combos: s.combos });
    if (tallies()) { for (const b of state.inflight) addTally(out, b.counts); addTally(out, state.unsent); }
    return (state.viewCache = out);
  }

  // ---------- the stats bar ----------

  const sum = m => { let n = 0; for (const k in m) n += m[k]; return n; };
  function fmtCount(n) {
    if (n < 1000) return String(n);
    if (n < 10000) return (n / 1000).toFixed(1) + "k";
    if (n < 1e6) return Math.round(n / 1000) + "k";
    return (n / 1e6).toFixed(1) + "M";
  }
  const pct = x => Math.round(x * 100) + "%";

  // Chips of their own above the panel, like the strip's below it, so they read on a surface
  // with nothing behind them. Each is written only when its text changes.
  const bar = {};
  function buildStats() {
    const host = $("stats");
    if (!host || bar.host) return;
    bar.host = host;
    const chip = (name, parts) => {
      const c = el("span", "stat " + name);
      const vals = [];
      for (const p of parts) {
        if (p === null) { const b = el("b"); vals.push(b); c.appendChild(b); } else c.appendChild(el("span", null, p));
      }
      host.appendChild(c);
      return { chip: c, vals };
    };
    bar.wpm = chip("wpm", [null, " wpm"]);
    bar.session = chip("session", ["avg ", null, " · top ", null]);
    bar.acc = chip("acc", [null, " accurate"]);
    bar.keys = chip("keys", [null, " keys · ", null, " combos"]);
    bar.layer = chip("layer", [null, " ", null]);
    bar.mode = chip("mode", [null, "heat ", null]);
    bar.mode.chip.title = "click: live, session, off";
    bar.mode.chip.addEventListener("click", () => setHeatmap(HEAT_MODES[(HEAT_MODES.indexOf(state.heatMode) + 1) % HEAT_MODES.length]));
  }
  function put(chip, values) {
    values.forEach((v, i) => { if (chip.vals[i].textContent !== v) chip.vals[i].textContent = v; });
  }

  function renderStats() {
    if (!bar.host) buildStats();
    if (!bar.host) return;
    const off = !(T("stats_bar") > 0);
    if (bar.host.classList.contains("off") !== off) bar.host.classList.toggle("off", off);
    if (off) return;
    const now = Date.now(), v = view();
    const live = Math.round(wpmOf(state.typing, now));
    put(bar.wpm, [v.chars || state.typing.win.length ? String(live) : "—"]);
    put(bar.session, [v.active_ms >= 10000 ? String(Math.round(v.active_net / 5 / (v.active_ms / 60000))) : "—",
                      v.peak_wpm ? String(v.peak_wpm) : "—"]);
    put(bar.acc, [v.chars ? pct(Math.max(0, 1 - v.deleted / v.chars)) : "—"]);
    // A combo is one keystroke made with several keys: its share is of keystrokes, not of keys.
    let presses = 0, combos = 0, members = 0;
    for (const layer in v.presses) presses += sum(v.presses[layer]);
    for (const layer in v.combos) for (const key in v.combos[layer]) {
      combos += v.combos[layer][key]; members += v.combos[layer][key] * key.split(",").length;
    }
    const strokes = presses - members + combos;
    put(bar.keys, [fmtCount(presses), strokes > 0 ? pct(combos / strokes) : "—"]);
    const top = state.data ? stack()[0] : null;
    put(bar.layer, [top ? layerLabel(top) : "—", top && presses ? pct(sum(v.presses[top] || {}) / presses) : "—"]);
    put(bar.mode, [state.session && state.session.name ? state.session.name + " · " : "", state.heatMode]);
  }

  // ---------- resolver ----------

  // A combo is drawn the way keymap-drawer draws it: a pill with the combo's legend at the
  // midpoint of its keys, on top of the flashed keys, fading after a moment.
  function showCombo(positions, key) {
    const board = $("board");
    const bb = board.getBoundingClientRect();
    const centers = positions.map(idx => {
      const r = state.keyEls[idx].getBoundingClientRect();
      return { x: r.left + r.width / 2 - bb.left, y: r.top + r.height / 2 - bb.top };
    });
    const cx = centers.reduce((a, c) => a + c.x, 0) / centers.length;
    const cy = centers.reduce((a, c) => a + c.y, 0) / centers.length - 30;

    // Links from the output pill down to each key of the combo.
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "combo-links");
    svg.setAttribute("width", bb.width); svg.setAttribute("height", bb.height);
    for (const c of centers) {
      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("x1", cx); line.setAttribute("y1", cy);
      line.setAttribute("x2", c.x); line.setAttribute("y2", c.y);
      svg.appendChild(line);
    }
    board.appendChild(svg);

    const pill = el("div", "combo-pill");
    pill.appendChild(el("span", "combo-label", "combo"));
    const tapEl = el("span", "combo-tap");
    setLegend(tapEl, key.tap, key.glyph);
    pill.appendChild(tapEl);
    if (key.hold || key.shifted) {
      const sub = el("span", "combo-sub");
      setLegend(sub, key.hold || key.shifted, key.hold ? key.glyph_hold : key.glyph_shifted);
      pill.appendChild(sub);
    }
    pill.style.left = cx + "px"; pill.style.top = cy + "px";
    board.appendChild(pill);
    requestAnimationFrame(() => { pill.classList.add("show"); svg.classList.add("show"); });
    for (const idx of positions) state.keyEls[idx].classList.add("combo-key");
    setTimeout(() => {
      pill.classList.remove("show"); svg.classList.remove("show");
      for (const idx of positions) state.keyEls[idx].classList.remove("combo-key");
      setTimeout(() => { pill.remove(); svg.remove(); }, 200);
    }, T('combo_pill_ms'));
    // A handle to take the pill down early when a longer legend supersedes it.
    return { remove() { pill.remove(); svg.remove(); } };
  }

  function comboFor(layer, token) {
    return state.data.combos.find(c => c.layers.includes(layer) && legendMatches(c.key.tap, token)) || null;
  }

  function flash(indices, cls) {
    for (const idx of indices) {
      const e = state.keyEls[idx];
      if (!e) continue;
      e.classList.add("pressed");
      if (cls) for (const c of cls.split(" ")) if (c) e.classList.add(c);
      clearTimeout(state.timers.get(idx));
      state.timers.set(idx, setTimeout(() => e.classList.remove("pressed", "combo", "inferred"), T('press_ms')));
    }
  }

  // A legend matches a typed token exactly, through the alias table, or as one side of an
  // "a|b" legend (a key that produces either). A multi-key sequence (a macro) matches when its
  // printable core equals the legend's: motion keys the macro types (End, arrows) and the
  // glyphs the legend uses to hint at them (⇥ → ⇲ …) are ignored on both sides.
  const MOTION = /[⇥⇤→←↑↓⇲⇱⇢⇠⏎↵␣⌫⌦\s]/g;
  const core = s => s.replace(MOTION, "");
  // `exact`: the legend itself, not one alternative of an adaptive key's "a|b" (findOnLayer).
  function legendMatches(legend, token, exact) {
    if (!legend) return false;
    if (legend === token) return true;
    if (!exact && legend.includes("|") && legend.split("|").some(part => part.trim() === token)) return true;
    const alts = ALIASES[token];
    if (alts && alts.includes(legend)) return true;
    if (token.length > 1) { const c = core(token); return c.length > 1 && c === core(legend); }
    return false;
  }

  const isLetter = t => /^[\p{L}]$/u.test(t);

  // Letter-producing combos on the base layer are commands (e.g. vim motions) rather than typing
  // when `extras.letter_combos_on` is set: outside those layers they are ignored, so a typed
  // letter missing from the base is attributed to the secondary alpha layer instead.
  // `noCombos`: typing whose sender says combos are not how it is typed (handleKey).
  function findOnLayer(layer, token, commandLayersActive, noCombos) {
    const keys = state.data.layers[layer];
    if (!keys) return null;
    const gate = extras().letter_combos_on;
    // A legend that says exactly this beats one that only sometimes does. "h|v" is an adaptive
    // key -- h, or v right after a vowel -- while a combo drawn "v" always types v, so from a v
    // alone the combo is the likelier and the adaptive key the fallback.
    for (const exact of [true, false]) {
      for (let i = 0; i < keys.length; i++) if (keys[i].type !== "trans" && legendMatches(keys[i].tap, token, exact)) return [i];
      if (noCombos) continue;
      for (const c of state.data.combos) {
        if (!c.layers.includes(layer) || !legendMatches(c.key.tap, token, exact)) continue;
        if (gate && gate.length && isLetter(token) && layer === base() && !commandLayersActive) continue;
        return c.positions.slice();
      }
    }
    return null;
  }

  function tokenFor(ev) {
    if (ev.name && NAMED[ev.name]) return NAMED[ev.name];
    if (ev.chars && ev.chars.length === 1) {
      // On a board drawn in capitals, h is the H key (load).
      const upper = ev.chars.toUpperCase();
      return state.capitals && upper.length === 1 && upper !== ev.chars && isLetter(ev.chars) ? upper : ev.chars;
    }
    return null;
  }

  function armMomentary(layer) {
    const now = Date.now();
    const m = state.momentary.find(x => x.layer === layer);
    if (m) m.until = now + T('momentary_ms'); else state.momentary.push({ layer, until: now + T('momentary_ms') });
    setTimeout(expire, T('momentary_ms') + 20);
  }

  function expire() {
    const now = Date.now();
    const before = state.momentary.length;
    state.momentary = state.momentary.filter(m => m.until > now);
    if (state.momentary.length !== before) render();
  }

  function setInferred(on) {
    if (state.inferred === on) return;
    state.inferred = on;
    renderBanner();
  }

  // Resolve a typed token on the given stack (top first). Returns {hit, layer} or null.
  // An uppercase letter may live on a shifted layer the config names among the sticky ones.
  function resolveOnStack(token, layers, commandLayersActive, noCombos) {
    const shiftLayers = (extras().sticky || []).filter(l => /shift/i.test(l) && !layers.includes(l));
    // A capital the stack itself draws came from there -- Caps word up, K is its chord. Only a
    // capital the stack has no key for came from a Shift the layers do not show, and a shift
    // layer that is not up is where the drawer keeps it. Searched the other way round, a live
    // Caps word's K was drawn as Shift · Alpha 2's key and that layer's activator.
    const searchStack = /^\p{Lu}$/u.test(token) ? [...layers, ...shiftLayers] : layers;
    for (const layer of searchStack) {
      const hit = findOnLayer(layer, token, commandLayersActive, noCombos);
      if (hit) return { hit, layer, viaShift: shiftLayers.includes(layer) };
    }
    return null;
  }

  // Recent keyDown tokens with the keys they lit, for multi-key legends ("->", "=>", "&&", "()").
  const recent = [];
  function remember(token, lit, pill, layer) {
    const now = Date.now();
    while (recent.length && now - recent[0].t > T('sequence_ms')) recent.shift();
    const rec = { token, t: now, lit: lit || [], pill: pill || null, layer: layer || null, guess: null };
    recent.push(rec);
    if (recent.length > T('sequence_max')) recent.shift();
    return rec;
  }
  // `inferring`: placing a key the keyboard's own stack does not speak for (handleKey).
  function matchSequence(token, layers, cmdActive, noCombos, inferring, ev) {
    const now = Date.now();
    const fresh = recent.filter(r => now - r.t <= T('sequence_ms'));
    for (let n = Math.min(fresh.length, T('sequence_max') - 1); n >= 1; n--) {
      const parts = fresh.slice(fresh.length - n);
      const seq = parts.map(p => p.token).join("") + token;
      // A macro's first character can use up the one-shot layer the macro lives on (Qu on
      // Shift · Alpha 2, ão on the Ç extension), so by its last character that layer is off the
      // stack: look where its first characters were found, too. Without layers from the keyboard
      // that may never have been the macro's own -- Qu's Q reads as Alpha 2's q with Shift -- so
      // the sticky layers a macro can live on are looked at as well, and for typing sent in, which
      // never came from the keyboard's layers at all. Otherwise the keyboard reports a one-shot
      // layer for as long as it is up, and the live stack says all there is to say.
      const where = [...new Set(parts.map(p => p.layer).concat(inferring ? (extras().sticky || []) : [])
        .filter(l => l && !layers.includes(l)))];
      const r = resolveOnStack(seq, where.concat(layers), cmdActive, noCombos);
      if (!r) continue;
      // The keys lit so far were the macro's steps (or a shorter legend that matched first, like
      // "()" inside "();"): unlight them, pill included, and light the longer match.
      for (const p of parts) unlight(p);
      flash(r.hit, r.hit.length > 1 ? "combo" : null);
      let pill = null;
      if (r.hit.length > 1) { const c = comboFor(r.layer, seq); if (c) pill = showCombo(r.hit, c.key); }
      setInferred(false);
      // Keep the tokens: a still longer legend may follow (";" after "()", "⏎" after "do {").
      guess(remember(token, r.hit, pill, r.layer), r.hit, r.layer, ev);
      afterKey();
      return true;
    }
    return false;
  }
  function unlight(entry) {
    for (const idx of entry.lit) {
      const e = state.keyEls[idx];
      if (e) { e.classList.remove("pressed", "combo", "inferred", "combo-key"); clearTimeout(state.timers.get(idx)); }
    }
    if (entry.pill) entry.pill.remove();
    if (entry.guess) entry.guess.cancelled = true;   // taken back before it was counted
    entry.lit = []; entry.pill = null;
  }

  function commandLayersActive(layers) {
    const gate = extras().letter_combos_on;
    return !gate || !gate.length || gate.some(l => layers.includes(l));
  }

  function handleKey(ev) {
    if (!state.data) return;
    if (ev.type === "flagsChanged") {
      if (ev.flags && !Array.isArray(ev.flags)) { state.mods = ev.flags; renderKeys(); }
      return;
    }
    if (ev.type !== "keyDown" || ev.repeat) return;
    state.lastKeyAt = Date.now();
    // With key positions coming from the firmware, the board is lit from them; the character
    // only feeds the strip (hud.key forwards it).
    // Physical character reports are redundant while firmware position reports are fresh. The
    // rehearsal feed marks its uinput events synthetic: those have no matching firmware positions,
    // so they must still resolve and light their key after a synthetic thumb flash.
    if (!ev.synthetic && state.posAt && Date.now() - state.posAt < T('positions_fresh_ms')) return;
    const token = tokenFor(ev);
    if (!token) return;

    const layers = stack();
    // Typing sent in -- `zmk-layer-hud poke`, a WebSocket client -- carries `combos`: whether
    // combos are part of how it is typed (the feed makes it false for a sender that does not
    // say). It did not come from the keyboard's own layers, so it is placed the way inference
    // places a key, live or not: on the stack, else on the layer that has it -- z as Alpha 2's key
    // rather than the r+a chord, unless the sender says combos. A character only a combo types is
    // still that combo: the sender said how it types, not that the keymap has another way.
    const sent = typeof ev.combos === "boolean";
    const noCombos = sent && !ev.combos;
    // `letter_combos_on` is an inference hint, for a key the keyboard's live stack cannot explain.
    // A live stack is the keyboard's own word on which combos can fire -- ZMK matches a combo
    // against the highest active layer, and the import records each combo's real coverage -- so
    // on it a letter combo is typing, whatever the hint says. Gating it there hid every
    // base-layer letter combo (k w v q x z j y) whenever positions were not fresh. Typing sent in
    // says for itself whether combos count.
    const cmdActive = state.live || sent ? true : commandLayersActive(layers);

    // Macros type several keys back to back (-> is "-" then ">"): when the last few tokens
    // together spell a legend on the live stack, that key or combo is what was pressed.
    if (matchSequence(token, layers, cmdActive, noCombos, !state.live || sent, ev)) return;

    // Live: the keyboard told us the stack; the key must be on it (combos included). A chord
    // (⌘c, ⌃⇧a) first tries the legend spelled with its modifier glyphs.
    if (state.live && !sent) {
      const f = ev.flags || {};
      const modsGlyph = ["cmd", "ctrl", "alt", "shift"].filter(m => f[m] && (m !== "shift" || f.cmd || f.ctrl || f.alt)).map(m => MOD_GLYPH[m]).join("");
      const r = (modsGlyph && resolveOnStack(modsGlyph + token, layers, cmdActive)) || resolveOnStack(token, layers, cmdActive);
      if (r) {
        const extra = r.viaShift ? activatorsOf(r.layer) : [];
        flash(r.hit.concat(extra), r.hit.length > 1 ? "combo" : null);
        // The pill is remembered with the keys, so whatever supersedes this guess -- a macro's
        // longer legend, or the positions arriving after it -- takes the pill down too.
        let pill = null;
        if (r.hit.length > 1) { const c = comboFor(r.layer, token); if (c) pill = showCombo(r.hit, c.key); }
        guess(remember(token, r.hit.concat(extra), pill, r.layer), r.hit, r.layer, ev);
        setInferred(false);
        touchLayer(r.layer);
        afterKey();
        return;
      }
      // Not on the real stack: a chord whose legend is a label or an icon (shortcut layers), or
      // a legend the drawer spells differently. The keyboard is the only source, so guessing
      // another layer would be wrong: remember the token for a possible macro and light nothing.
      remember(token, []);
      return;
    }

    // Dashed when the keyboard's own stack could not explain it; typing sent in was never the
    // keyboard's to explain.
    const inferredCls = state.live && !sent ? "inferred" : "";
    const ex = extras();
    const sticky = new Set(ex.sticky || []);
    // 0. Typing goes through two alpha layers when the config names a secondary one: a letter
    //    that is not a plain base-layer key comes from the sticky alpha2 layer (one shot).
    if (ex.alpha2 && isLetter(token) && !cmdActive && !state.oneShot) {
      const lower = token.toLowerCase();
      const onBase = state.data.layers[base()].some(k => k.type !== "trans" && legendMatches(k.tap, lower));
      const direct = state.data.layers[ex.alpha2].findIndex(k => k.type !== "trans" && legendMatches(k.tap, lower));
      if (!onBase && direct >= 0) {
        state.oneShot = ex.alpha2;
        render();
        const shiftLayer = (ex.sticky || []).find(l => /shift/i.test(l));
        const extra = activatorsOf(ex.alpha2).concat(/^\p{Lu}$/u.test(token) && shiftLayer ? activatorsOf(shiftLayer) : []);
        flash([direct].concat(extra), inferredCls);
        guess(remember(token, [direct], null, ex.alpha2), [direct], ex.alpha2, ev);
        afterKey();
        return;
      }
    }
    const search = ex.search || state.data.layer_order.filter(l => l !== base());
    const order = isLetter(token)
      ? search
      : search.filter(l => !sticky.has(l)).concat(search.filter(l => sticky.has(l)));
    const place = noCombos => {
      // 1. the active stack, top first
      const r = resolveOnStack(token, layers, cmdActive, noCombos);
      if (r) {
        const extra = r.viaShift ? activatorsOf(r.layer) : [];
        flash(r.hit.concat(extra), [r.hit.length > 1 ? "combo" : "", inferredCls].join(" ").trim() || null);
        let pill = null;
        if (r.hit.length > 1) { const c = comboFor(r.layer, token); if (c) pill = showCombo(r.hit, c.key); }
        guess(remember(token, r.hit.concat(extra), pill, r.layer), r.hit, r.layer, ev);
        touchLayer(r.layer);
        afterKey();
        return true;
      }
      // 2. any other layer → a momentary or one-shot activation the host could not see. Letters
      //    most likely came from a sticky layer; anything else from a held one.
      for (const layer of order) {
        if (layers.includes(layer)) continue;
        const hit = findOnLayer(layer, token, cmdActive, noCombos);
        if (hit) {
          if (sticky.has(layer)) state.oneShot = layer; else armMomentary(layer);
          render();
          flash(hit.concat(activatorsOf(layer)), [hit.length > 1 ? "combo" : "", inferredCls].join(" ").trim() || null);
          let pill = null;
          if (hit.length > 1) { const c = comboFor(layer, token); if (c) pill = showCombo(hit, c.key); }
          guess(remember(token, hit.concat(activatorsOf(layer)), pill, layer), hit, layer, ev);
          afterKey();
          return true;
        }
      }
      return false;
    };
    // Without combos first; a combo only when nothing else on the keymap types this.
    if (place(noCombos) || (noCombos && place(false))) return;
    afterKey();
  }

  function touchLayer(layer) {
    const m = state.momentary.find(x => x.layer === layer);
    if (m) { m.until = Date.now() + T('momentary_ms'); return; }
    // a key that resolved only in the base drops any inferred momentary layer
    if (baseStack().includes(layer) && state.momentary.length) { state.momentary = []; render(); }
  }

  // A one-shot layer is consumed by the key, but stays on the banner long enough to be seen.
  let oneShotTimer = null;
  function afterKey() {
    if (!state.oneShot) return;
    clearTimeout(oneShotTimer);
    oneShotTimer = setTimeout(() => { state.oneShot = null; render(); }, T('one_shot_ms'));
  }

  // ---------- public API ----------

  function applyLayers(ids) {
    // Which key brought each new drawn layer in: the position pressed just before (its own
    // activator among the candidates), so an alternate activator elsewhere stays dark.
    const now = Date.now();
    const wasDrawn = state.live ? new Set(state.live.ids.map(id => (zl(id) || {}).drawer).filter(Boolean)) : new Set();
    for (const id of ids) {
      const name = (zl(id) || {}).drawer;
      if (!name || wasDrawn.has(name)) continue;
      state.drawnSince[name] = now;
      if (state.activatorOf[name] != null) continue;
      // Prefer a key the drawer marks as reaching this layer; else the key pressed right
      // before the layer appeared is the one holding it (a thumb whose legend says otherwise).
      const candidates = activatorsOf(name);
      // Still down: a key that was tapped and let go is not what is holding this layer, even
      // if it was the last thing pressed before the layer arrived.
      const fresh = [...recentPos].reverse().filter(p => now - p.t < T('activator_ms') && state.held.has(p.idx));
      const press = fresh.find(p => candidates.includes(p.idx)) || fresh[0];
      if (press) state.activatorOf[name] = press.idx;
    }
    const drawnNow = new Set(ids.map(id => (zl(id) || {}).drawer).filter(Boolean));
    // The presses of the keys now holding a layer that just came up (recredit).
    const gained = [];
    for (const name of drawnNow) {
      if (wasDrawn.has(name) || state.activatorOf[name] == null) continue;
      const p = [...recentPos].reverse().find(q => q.idx === state.activatorOf[name]);
      if (p) gained.push(p);
    }
    for (const name of Object.keys(state.activatorOf)) if (!drawnNow.has(name)) delete state.activatorOf[name];
    for (const name of Object.keys(state.drawnSince)) if (!drawnNow.has(name)) delete state.drawnSince[name];
    state.live = { ids, at: now };
    state.momentary = []; state.oneShot = null; state.inferred = false;
    if (gained.length) recredit(gained, now);
    render();
  }

  /* A layer change held back for a flash is the keyboard's word all the same, and the moment
   * another key goes down it applies, so that key resolves on the layers really up. Held back
   * any longer, a one-shot layer outlived its key for as long as typing went on: every keystroke
   * within press_ms of the last re-armed the delay, and at 170 ms a key the chord after an Alpha 2
   * letter drew Alpha 2's combo -- "wax" drew its x as "-", "quick" its k as a dead quote. */
  function flushLayers() {
    if (!state.pendingLayers) return;
    const ids = state.pendingLayers;
    state.pendingLayers = null; state.pendingDue = null;
    clearTimeout(state.layersTimer);
    applyLayers(ids);
  }

  const hud = {
    // The keymap message. Re-sent by the host when the drawer file changes: geometry and legends
    // are rebuilt, the live layer set and the daemon code are kept.
    load(data) {
      if (typeof data === "string") data = JSON.parse(data);
      if (!data || !data.layout || !data.layers) return;
      // What is still waiting to be counted was drawn with the keymap going away: count it with
      // that one, whose layers and keys it names.
      if (state.data) ledgerCommit(Date.now(), true);
      state.data = data;
      state.momentary = []; state.oneShot = null;
      state.activatorOf = {}; state.drawnSince = {}; state.held.clear(); state.comboShown = null; state.comboEntry = null;
      state.baseLayers = [data.base];
      // A board that draws its letters as capitals, as `keymap parse` does: there the legend is the
      // keycap, not the character, so a lowercase letter placed from what was typed is its
      // capital's key (tokenFor) -- the rule host/play.py spells text by.
      const letters = (data.layers[data.base] || []).map(k => k.tap).filter(t => typeof t === "string" && isLetter(t));
      state.capitals = letters.length > 0 && letters.every(t => t !== t.toLowerCase());
      // A session is kept by ZMK position: each drawer key's, inverted from the message's map (a
      // message with none is a keymap in the firmware's own order, where they are the same).
      const map = data.positions || {};
      state.posOf = [];
      for (const [pos, idx] of Object.entries(map)) state.posOf[idx] = Number(pos);
      if (!Object.keys(map).length) data.layout.keys.forEach((k, idx) => { state.posOf[idx] = idx; });
      // Heat outlives a reload (it fades in seconds anyway), but not on keys this layout lacks.
      for (const idx of [...state.heat.keys()]) if (idx >= data.layout.keys.length) state.heat.delete(idx);
      document.documentElement.style.setProperty("--panel-alpha", String(T("opacity") / 100));
      buildBoard();
      renderTitle();
      postSize();
      render();
    },
    // The keyboard's active ZMK layer ids (layer 0 omitted). Clears every inference: from now on
    // the stack is what the keyboard says.
    setLayers(ids) {
      if (typeof ids === "string") ids = JSON.parse(ids);
      if (!Array.isArray(ids)) return;
      ids = ids.map(Number).filter(n => Number.isInteger(n) && n > 0);
      // The change already held back, said again (a heartbeat, a re-assertion): it is on its way
      // and keeps its deadline. Starting the wait over each time kept a dropped layer up for as
      // long as the repeats kept coming.
      const sameIds = (a, b) => a.length === b.length && a.every(x => b.includes(x));
      if (state.pendingLayers && sameIds(state.pendingLayers, ids)) return;
      clearTimeout(state.layersTimer);
      // A one-shot layer leaves right after the key it served (and may enter another, undrawn
      // one). Keep the board on the drawn layers while the key's flash is visible, otherwise the
      // flash appears under the wrong legends. Layers that only appear apply at once.
      const now = Date.now();
      const since = now - (state.lastKeyAt || 0);
      // A drop is due press_ms after the key it followed, and stays due then: a change arriving
      // while it waits replaces what will apply, never when. Measured afresh each time, a run of
      // changes a keystroke apart kept the first layer up for as long as the run lasted.
      const due = state.pendingLayers ? state.pendingDue : now + T('press_ms') - since;
      state.pendingLayers = null; state.pendingDue = null;
      if (state.live && due > now) {
        const drawn = set => new Set(set.map(id => (zl(id) || {}).drawer).filter(Boolean));
        const before = drawn(state.live.ids), after = drawn(ids);
        const losesDrawn = [...before].some(name => !after.has(name));
        // Only a layer falling away is worth holding back. One that brings another drawn layer
        // up means the next key is already on it -- ç on Alpha 2, then ão on the Ç extension --
        // and holding the whole change back left the macro looking on Alpha 2, where it is not.
        const gainsDrawn = [...after].some(name => !before.has(name));
        if (losesDrawn && !gainsDrawn) {
          // Held back for the flash only: the timer applies it outright rather than asking
          // again, and the next key to go down applies it first (flushLayers).
          state.pendingLayers = ids; state.pendingDue = due;
          state.layersTimer = setTimeout(() => { state.pendingLayers = null; state.pendingDue = null; applyLayers(ids); }, due - now);
          return;
        }
      }
      applyLayers(ids);
    },
    // Firmware `positions;`: the physical key at ZMK position `pos` was pressed. The one exact
    // source for what to light, whatever the key produced (chords, combos, macros, modifiers,
    // layer keys). Two or more positions within a combo term that form a combo on the live
    // stack draw that combo's pill. `sent`: this press was sent in, not the keyboard's (it lights
    // its key the same, and is never counted into a session).
    pressAt(pos, sent) {
      if (!state.data || state.secure) return;
      const idx = idxAt(pos);
      const now = Date.now();
      // A key going down is a keystroke of its own: a layer change held back for the previous
      // one's flash is the keyboard's state now, and this key resolves on it.
      flushLayers();
      // The keyboard's own combo term (config combo_term_ms) plus slack for the reports' travel.
      const term = ((state.data.combo_term || 50) + T('combo_slack_ms'));
      // A report can beat its own positions: they travel on separate channels. What the character
      // drew for this keystroke was a guess the keyboard is replacing now -- take it down, pill
      // and all, or the board shows the guess and the truth side by side (two pills for one
      // chord). A guess older than the combo term was an earlier keystroke's, and fades by itself.
      for (const r of recent) if (now - r.t <= term) unlight(r);
      // Nor is a guess still waiting to be counted: with positions coming, this keystroke counts
      // by its position, and so does the one the guess was for.
      for (const e of state.ledger) if (e.kind === "guess") e.cancelled = true;
      state.posAt = now; state.lastKeyAt = now;
      if (!state.keyEls[idx]) return;
      state.held.add(idx);
      const entry = { kind: "press", idx, pos: Number(pos), t: now, due: now + term, stack: stack(), eligible: !sent };
      // The layer set and the key that brought it up are two reports, in no promised order. When
      // the key comes second, setLayers had nothing to attribute the layer to: if the drawer says
      // this key reaches a live layer and nothing is recorded as holding it, this is what did --
      // if the layer appeared at the same moment. One already up (a one-shot tapped earlier) came
      // with an earlier keystroke, and this press belongs to whatever chord it arrives in: with
      // Alpha 2 waiting, the 0 chord that uses Alpha 2's own key drew nothing.
      if (state.live) {
        for (const name of liveStack()) {
          if (name === base() || state.activatorOf[name] != null) continue;
          if (now - (state.drawnSince[name] || 0) > term) continue;
          if (activatorsOf(name).includes(idx)) {
            state.activatorOf[name] = idx;
            // It went down on the layers below the one it brought up, and counts there.
            entry.stack = entry.stack.filter(l => l !== name);
          }
        }
      }
      ledgerAdd(entry);
      flash([idx]);
      bumpHeat(idx);   // a position is never taken back, so it warms its key at once
      // Stay lit until the release arrives (a safety timeout covers a lost report).
      clearTimeout(state.timers.get(idx));
      state.timers.set(idx, setTimeout(() => hud.releaseAt(pos), T('held_timeout_ms')));
      // Older presses stay in the list for the activator lookup (setLayers); the combo group is
      // the trailing run of presses that started within the term of this one.
      while (recentPos.length && now - recentPos[0].t > T('activator_ms')) recentPos.shift();
      recentPos.push({ idx, t: now });
      // The keyboard has already decided. A key still down that is what brought one of the live
      // layers up was treated by ZMK as a layer hold, not as part of a chord — had it been half
      // of a combo, the combo would have fired and the layer would not have changed. So the group
      // stops there, however recently it was pressed. Without this the thumb joins the burst and
      // the chord after it is either missed or drawn as whichever larger combo happens to contain
      // that thumb: holding Alpha 2 and chording backspace drew Tab ([4,5] became [4,5,22]).
      // …but only a key pressed before this burst. ZMK releases a combo's captured positions
      // together, so they arrive in the same report and share a timestamp; a key that a combo
      // both holds a layer with and uses as one of its own (a sticky layer's own key) is still
      // part of the chord it arrived with.
      const holdsALayer = idx => state.held.has(idx) && Object.values(state.activatorOf).includes(idx);
      let start = recentPos.length - 1;
      while (start > 0) {
        const prev = recentPos[start - 1];
        if (now - prev.t > term || (prev.t < now && holdsALayer(prev.idx))) break;
        start--;
      }
      if (start === recentPos.length - 1) { state.comboShown = null; state.comboEntry = null; } // a new group begins
      // Only presses within the keymap's combo term form a combo: ZMK's combo module releases the
      // captured positions together when a combo completes, so they arrive within the term. A key
      // pressed later while a layer is held is that layer's key, never a combo with the holder.
      const pressedSet = recentPos.slice(start).map(p => p.idx);
      if (pressedSet.length > 1) {
        // The topmost active layer that defines a combo on these keys wins: the base layer is
        // always in the stack and often has a different combo on the same keys.
        const samePositions = c => c.positions.length === pressedSet.length && c.positions.every(p => pressedSet.includes(p));
        let combo = null, comboLayer = null;
        for (const layer of stack()) {
          combo = state.data.combos.find(c => c.layers.includes(layer) && samePositions(c));
          if (combo) { comboLayer = layer; break; }
        }
        if (combo) {
          // A third key within the term makes a bigger combo: take the smaller one's pill down,
          // and its count with it -- one chord, one combo.
          if (state.comboShown) state.comboShown.remove();
          if (state.comboEntry) state.comboEntry.cancelled = true;
          state.comboEntry = ledgerAdd({ kind: "combo", layer: comboLayer, key: comboKey(combo.positions), t: now,
                                         due: now + term, eligible: !sent });
          // Not flash(): these keys are down, and it is their release that unlights them. flash's
          // press_ms timer would replace the held timer each key got from its own press and take
          // the chord out from under the user's fingers after a third of a second — along with
          // the safety net that covers a lost release.
          for (const p of combo.positions) { const e = state.keyEls[p]; if (e) e.classList.add("pressed", "combo"); }
          state.comboShown = showCombo(combo.positions, combo.key);
        }
      }
    },
    // Leave live mode (tests, or a host that lost the keyboard).
    clearLayers() { state.live = null; render(); },
    // The key at ZMK position `pos` went up: the flash fades out from now.
    releaseAt(pos) {
      if (!state.data) return;
      const idx = idxAt(pos);
      const e = state.keyEls[idx];
      if (!e) return;
      state.held.delete(idx);
      for (const name of Object.keys(state.activatorOf)) if (state.activatorOf[name] === idx) state.activatorOf[name] = null;
      clearTimeout(state.timers.get(idx));
      state.timers.set(idx, setTimeout(() => e.classList.remove("pressed", "combo", "inferred"), T('release_ms')));
    },
    // The keyboard that was opened (its HID product name): the default title.
    setDevice(name) { if ((name || "") !== state.device) { state.device = name || ""; renderTitle(); } },
    key(ev) {
      if (typeof ev === "string") ev = JSON.parse(ev);
      if (state.secure) return;   // nothing typed shows while it is a secret (setSecure)
      statsKey(ev);   // before handleKey, which has nothing to do for it while positions are fresh
      handleKey(ev);
      if (window.keys) window.keys.key(ev);  // the typed-keys strip on the same page
    },
    press(indices) { flash(indices); },
    // Any feed message, as a host sends it: the one dispatcher, for the WebSocket, the macOS panel
    // (for what it has no call of its own for) and the demo. Kinds this page does not know are
    // left alone.
    receive(m) {
      if (typeof m === "string") m = JSON.parse(m);
      if (!m || typeof m !== "object") return;
      if (m.kind === "keymap") hud.load(m);
      else if (m.kind === "key") hud.key(m);
      else if (m.kind === "layers") hud.setLayers(m.ids);
      else if (m.kind === "device") hud.setDevice(m.name);
      else if (m.kind === "press") hud.pressAt(m.pos, m.sent === true || m.synthetic === true);
      else if (m.kind === "release") hud.releaseAt(m.pos);
      else if (m.kind === "session") applySession(m);
      else if (m.kind === "secure") setSecure(m.on);
      if (m.device) hud.setDevice(m.device);  // the keyboard that is typing names the panel
    },
    // What the keys glow with: "live" (what was just typed), "session" (every press counted) or
    // "off". The bar's last chip cycles it too.
    setHeatmap(mode) { setHeatmap(mode); },
    // The counts, for tests and hosts: everything shown (`local`), the keyboard's own not yet
    // handed on (`unsent`), keystrokes still waiting to be counted, and the live WPM.
    stats: {
      local: () => JSON.parse(JSON.stringify(state.local)),
      unsent: () => JSON.parse(JSON.stringify(state.unsent)),
      pending: () => state.ledger.length,
      wpm: () => Math.round(wpmOf(state.typing, Date.now())),
      view: () => JSON.parse(JSON.stringify(view())),   // what the bar shows
    },
    state,
  };
  window.hud = hud;

  // ✕: tell the host to close. Hammerspoon listens on a user-content controller; a
  // WebSocket host receives {"kind":"close"}.
  const closeBtn = $("close");
  if (closeBtn) closeBtn.addEventListener("click", () => {
    sendTally();   // what was counted and not sent yet goes before the host does
    try { window.webkit.messageHandlers.zmkhud.postMessage("close"); } catch (e) { /* not WebKit */ }
    if (hud.socket && hud.socket.readyState === 1) hud.socket.send(JSON.stringify({ kind: "close" }));
  });

  // Rebuild the geometry when the panel is resized (zoom, moveTo another screen).
  // A demo frame is final once drawn: buildBoard() empties #board, which would take the lit keys
  // and the combo pill with it. Headless browsers fire a resize after the first layout, which is
  // why a screenshot could come back with the board drawn but nothing on it.
  window.addEventListener("resize", () => { if (state.data && !state.demoShown) { buildBoard(); render(); postSize(); } });

  // Generic host: index.html?ws=ws://127.0.0.1:8766 — messages are
  //   {"kind":"keymap",…}  {"kind":"key", ...event}  {"kind":"layers","ids":[…]}   (host/hudfeed.py speaks this).
  const params = new URLSearchParams(location.search);
  // A GIF still is drawn as it always was: no glow, whatever demoFrame presses. Said here, before
  // anything loads -- demoShown arrives only after the frame's keys have gone down.
  if (params.get("demo") !== null) { state.demo = true; document.body.classList.add("demo"); }
  // Framed by another page (the landing page's demo): that page has the keyboard, and sends what
  // is typed through hud.receive. So the dev keydown listener below stays off -- it swallows every
  // key but F5 and ⌘ chords, Tab included, and Tab is the way out of the frame -- and the ✕, which
  // has no host to close, goes (hud.css html.embed).
  const embedded = params.get("embed") !== null;
  if (embedded) document.documentElement.classList.add("embed");
  const wsUrl = params.get("ws");
  if (wsUrl) {
    const connect = () => {
      const s = new WebSocket(wsUrl);
      hud.socket = s;
      s.onmessage = e => hud.receive(e.data);
      s.onclose = () => setTimeout(connect, 1000);
    };
    connect();
  }

  // Dev: index.html?keymap=keymap.json (python3 host/keymap.py --dump > hud/keymap.json) and real
  // key events, so the page can be exercised in a browser without a host.
  //
  // Dev: &demo=N renders step N of a scripted demo and stops there — no timers to race, so a
  // headless browser can screenshot one frame per step (docs/make-gif.sh assembles them). The
  // script comes from &script=<url>, or from demo.json beside the page; make-gif.sh copies the
  // one it was given there. Every step is absolute — it re-asserts the whole state — so the
  // frames are independent and render in any order:
  //   { "device": "Diamond",        // the panel's title, on every frame (each is a fresh load)
  //     "opacity": 100,             // override the config's hud.opacity for the rendering
  //     "steps": [
  //       { "layers": [2, 3],       // the keyboard's active ZMK layer ids -> hud.setLayers
  //         "hold":   [32],         // ZMK positions down since before: a thumb holding its layer
  //         "press":  [17, 18],     // ZMK positions -> hud.pressAt: two of them inside the combo
  //                                 // term draw that combo's pill, exactly as a real chord does
  //         "keys":   ["y"] } ] }   // chips for the typed-keys strip: a string or a key event
  // The same file plays in real time (docs/demo-scripts.md): steps then also take text to type
  // and pauses, which host/play.py --stills turns into frames of this shape for the GIF.
  function demoFrame(script, n) {
    const steps = script.steps || [];
    const step = steps[Math.max(0, Math.min(steps.length - 1, n))];
    if (!step) return;
    if (script.opacity !== undefined) {
      document.documentElement.style.setProperty("--panel-alpha", String(script.opacity / 100));
    }
    const device = step.device || script.device;
    if (device) hud.setDevice(device);
    // A still, not a moment in an animation. Transitions go first: a headless browser's virtual
    // clock does not advance them, so a key that has just been lit would paint with its old
    // background and the combo pill at opacity 0 — the classes are all there in the DOM, they
    // simply never arrive anywhere. Without transitions every element paints its final style.
    const frozen = document.createElement("style");
    frozen.textContent = "*, *::before, *::after { transition: none !important; animation: none !important; }";
    document.head.appendChild(frozen);
    // Then the teardowns the HUD schedules — a key unlighting after press_ms, the combo pill
    // after combo_pill_ms, the strip's fade — which would otherwise fire before the screenshot.
    // Timeouts are dropped while the frame is built, so what it draws stays drawn.
    const schedule = window.setTimeout;
    window.setTimeout = () => 0;
    try {
      // A key down since before this keystroke -- a thumb holding its layer, a Shift -- went down
      // earlier than the chord beside it: pressed first, and dated past the combo term, so it holds
      // its layer as it does on the keyboard rather than joining the chord.
      const held = (step.hold || []).map(Number);
      if (held.length) {
        const now = Date.now, then = now() - (state.data.combo_term || 50) - T("combo_slack_ms") - 1;
        Date.now = () => then;
        try { for (const p of held) hud.pressAt(p); } finally { Date.now = now; }
      }
      hud.setLayers(step.layers || []);
      // pressAt is the firmware's own path: it lights the exact key and resolves a combo on the
      // topmost active layer by itself — no second, shorter-lived flash on top of it.
      for (const p of step.press || []) if (!held.includes(Number(p))) hud.pressAt(p);
      // The strip only, so a frame shows the chips it scripts and nothing that inference adds. A
      // script not compiled for stills (host/play.py --stills) shows its text as it would type.
      for (const k of step.keys || (typeof step.type === "string" ? [...step.type] : [])) {
        if (window.keys) window.keys.key(Object.assign({ type: "keyDown", chars: "", name: null, flags: {} },
                                                       typeof k === "string" ? { chars: k } : k));
      }
    } finally {
      window.setTimeout = schedule;
    }
    // Pill, links and chips fade in on the next animation frame; a screenshot may not wait.
    document.querySelectorAll(".combo-pill, .combo-links, #keys .chip").forEach(e => e.classList.add("show"));
    state.demoShown = true;                       // nothing may rebuild the board from here on
  }
  /* Dev: &timeline=<url>&at=T draws the page as a script leaves it T ms in -- a key's glow half
   * gone, a pill still up, the speed on the bar -- for a GIF made of moments rather than stills
   * (docs/make-gif.sh --live). The timeline is host/play.py --capture's: the messages a keyboard
   * would send, and when. They are replayed on a clock of the page's own, every timer the page sets
   * firing in its turn, up to T; and there the clock stops, so nothing moves before the screenshot.
   * Transitions go, as for a still: a headless browser's virtual time does not run them. */
  function replayTo(data, capture, at) {
    const epoch = Date.now();
    let now = 0, seq = 0;
    const timers = new Map();
    window.setTimeout = (fn, ms) => { const id = ++seq; timers.set(id, { fn, at: now + (Number(ms) || 0), id }); return id; };
    window.clearTimeout = id => { timers.delete(id); };
    window.requestAnimationFrame = fn => { fn(); return 0; };
    Date.now = () => epoch + now;
    const advance = until => {
      for (;;) {
        let next = null;
        for (const t of timers.values()) if (t.at <= until && (!next || t.at < next.at || (t.at === next.at && t.id < next.id))) next = t;
        if (!next) break;
        timers.delete(next.id);
        now = Math.max(now, next.at);
        next.fn();
      }
      now = until;
    };
    const frozen = document.createElement("style");
    frozen.textContent = "*, *::before, *::after { transition: none !important; animation: none !important; }";
    document.head.appendChild(frozen);
    hud.load(data);
    if (capture.opacity !== undefined && capture.opacity !== null) {
      document.documentElement.style.setProperty("--panel-alpha", String(capture.opacity / 100));
    }
    if (capture.device) hud.setDevice(capture.device);
    let t = 0;
    for (const [wait, msg] of capture.timeline || []) {
      if (t + wait > at) break;
      t += wait;
      advance(t);
      hud.receive(msg);
    }
    advance(at);
    state.demoShown = true;                       // nothing may rebuild the board from here on
  }
  hud.replayTo = replayTo;

  if (params.get("keymap")) {
    const demo = params.get("demo"), timeline = params.get("timeline");
    const script = demo !== null ? fetch(params.get("script") || "demo.json").then(r => r.json())
                 : timeline ? fetch(timeline).then(r => r.json()) : Promise.resolve(null);
    Promise.all([fetch(params.get("keymap")).then(r => r.json()), script])
      .then(([data, script]) => {
        if (timeline && demo === null) { replayTo(data, script, Number(params.get("at")) || 0); return; }
        hud.load(data);
        if (script) demoFrame(script, Number(demo));
      })
      .catch(e => console.error(e));
  }
  if (location.protocol.startsWith("http") && !embedded) window.addEventListener("keydown", e => {
    const name = e.key.length === 1 ? null : e.key.toLowerCase().replace("arrow", "").replace("backspace", "delete").replace("enter", "return");
    hud.key({ type: "keyDown", chars: e.key.length === 1 ? e.key : "", name, flags: {} });
    if (e.key !== "F5" && !e.metaKey) e.preventDefault();
  });
  render();
})();
