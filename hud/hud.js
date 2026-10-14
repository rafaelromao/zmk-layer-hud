/* zmk-layer-hud — renderer + keymap resolver.
 *
 * The page carries no keymap: the host sends one, built by host/keymap.py from the HUD's config
 * and the definitions `zmk-layer-hud import` wrote beside it (physical layout, layers, combos, the
 * ZMK layer id table, optional hints), and sends it again whenever either file changes. The host
 * drives the page through window.hud:
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
 *             resolved on that stack (combos included). A key that cannot be placed there lights
 *             nothing: guessing another layer would be wrong.
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
  // The keystrokes' own (press_ms, combo_slack_ms, ...) are keystrokes.js's.
  const Keystrokes = window.ZmkKeystrokes;
  const DEFAULTS = Object.assign({ opacity: 86, heatmap_ms: 3000, wpm_window_ms: 10000, wpm_idle_ms: 3000, stats_bar: 1, dark: 0 },
                                 Keystrokes.DEFAULTS);
  const T = name => (state.data && state.data.hud && state.data.hud[name] != null) ? state.data.hud[name] : DEFAULTS[name];
  // The stats bar's chips, from the config's `stats:` section; again only the fallback.
  const STAT_DEFAULTS = { wpm: true, session: true, accuracy: true, keys: true, layer: true, time: false,
    hands: false, sfb: false, slow: false, heatmap: true, theme: true, opacity: true };
  const statOn = name => (state.data && state.data.stats && state.data.stats[name] != null) ? !!state.data.stats[name] : STAT_DEFAULTS[name];
  // What the keyboard's messages say was struck (keystrokes.js). Until the page is only its caller,
  // what it has read is reached from here too (KS).
  const ks = Keystrokes.create();
  const KS = ks._s;

  // Modifier flags → the glyph a hold legend uses for them (home-row mods light while held).
  const MOD_GLYPH = { shift: "⇧", ctrl: "⌃", alt: "⌥", cmd: "⌘" };
  // Where the keys go and which legend each shows (hud/view.js), the same answers a native board draws from.
  const View = window.ZmkView;

  const state = {
    data: null,                 // flag -> true while held
    device: "",               // the keyboard's HID product name
    pick: null,               // a layer the viewer picked to look at (the stats' layer tile), until a keystroke
    keyEls: [],
    keyBoxes: [],             // key idx -> {l, t, r, b}: its box on the board as drawn (buildBoard)
    keyUnit: 1,               // px: the smallest key's side, what a pill's distance is measured in
    boardSize: { w: 0, h: 0 },
    pillSpots: new Map(),     // chord + pill size -> where its pill goes (pillSpot)
    timers: new Map(),
    scale: 1,
    heat: new Map(),          // key idx -> {h, t}: the live heatmap (see bumpHeat)
    heatTimer: null,
    heatMode: "live",         // what the keys glow with: live | session | physical | speed | off
    prefs: {},                // what the viewer chose (setPref): theme light | dark, side shown | hidden
    secure: false,            // secure input: a secret is being typed, and nothing of it shows
    mini: false,              // docked and minimized: the keyboard down to its banner (setMini)
    touchNote: "",            // why the board that was asked to type cannot (touch.js setTouchNote)
    watched: null,            // what the feed says sees a secret being typed ([] = nothing); null until told
    demo: false,              // a GIF still (&demo=N): drawn as it always was, no heat, no counts
    lastStroke: null,         // the keystroke before, for the time a key takes and same-finger bigrams
    lastOwnStroke: null,      // ...among the keyboard's own
    local: emptyTally(),      // everything counted since the page loaded, from any source
    unsent: emptyTally(),     // the keyboard's own counts, not yet handed to a session
    typing: newTyping(),      // live WPM, over everything shown
    ownTyping: newTyping(),   // ...and over the keyboard's own typing, for the session's peak
    wpmTimer: null,
    statsSaid: null,          // the stats column as the panel was last told it (postStats)
    statsWaiting: null,       // ...and as it is now, when that has not been said yet
    statsTimer: null,
    statsAt: 0,
    session: null,            // the host's active session ({"kind":"session"}), when it keeps one
    inflight: [],             // counts sent to it that it has not said it has
    seq: 0,
    tallyTimer: null,
    viewCache: null,
    touch: null,              // the touch keyboard's view (touch.js, setTouchView): {ids, down, latched, mods, note}
    dock: null,               // docked along the bottom of the screen (setDock): {maxHeight, split, pxPerMm, width, fit}
    offsetX: 0,               // the board's left margin when docked: it is centred, not stretched
  };
  // What keystrokes.js has read, reached as the page's own: the layers (live, baseLayers, the inferred
  // momentary and oneShot, activatorOf, drawnSince, pendingLayers), the keys down (held, down), the
  // modifiers and the keys that turned them on (mods, modSource), what waits to be settled (ledger,
  // comboEntry) or told a tap from a hold (strokes), the last press and when a key last went down
  // (lastPress, posAt, lastKeyAt), and each key's ZMK position (posOf).
  for (const name of ["live", "baseLayers", "momentary", "oneShot", "activatorOf", "drawnSince", "pendingLayers",
                      "held", "down", "mods", "modSource", "ledger", "comboEntry", "lastPress", "posAt", "lastKeyAt",
                      "posOf", "strokes"]) {
    Object.defineProperty(state, name, { get: () => KS[name], set: v => { KS[name] = v; }, enumerable: true });
  }
  // What other scripts on the page hook into (hud.on): touch.js follows the keymap and the layers.
  const hooks = {};
  let attachOwn = null;      // a page's own attach and detach (hud.setAttach), else the panel's
  let widthDue = null;       // the board's width checked again soon (followWidth)
  function fire(name, arg) {
    for (const fn of hooks[name] || []) {
      try { fn(arg); } catch (e) { console.error(e); }
    }
  }
  // Docked, the banner is one thin line and nothing else sits around the board: this is the
  // height of what is not the board (hud.css html.dock #hud and #layer), which the host's
  // max_height has to make room for.
  const DOCK_CHROME = 40;
  // ...and the width of what is not the board either side (hud.css html.dock #hud: 10px of padding and
  // a 1px border each): the screen's width less this is the board's.
  const DOCK_SIDES = 22;

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
    // Docked: as wide as the screen allows and no taller than the host lets the strip be, a split
    // board's halves at the sides (View.geometry).
    // A touch keyboard's keys as big as the board's own, when they fit (dockFit).
    const fit = state.dock && state.dock.fit;
    const g = View.geometry(lay, { width, gap: GAP, chrome: DOCK_CHROME, dock: state.dock &&
      { maxHeight: state.dock.maxHeight || window.innerHeight || 360, split: state.dock.split,
        scale: fit && fit.fits ? fit.scale : null } });
    state.scale = g.scale; state.offsetX = g.offsetX; state.gap = g.gap;
    // Each key's centre on the board, in px: what a touch between keys is measured from (touch.js),
    // and its box, which the combo chips keep off.
    state.centers = g.centers;
    state.rects = g.rects;
    state.chipsEl = null;   // went with the keys
    board.style.height = g.height + "px";
    state.boardHeight = g.height;
    state.builtWidth = width;
    // Legends scale with the typical key height (see --kh in hud.css).
    board.style.setProperty("--kh", Math.max(20, g.kh) + "px");
    // What a combo's pill is placed around (pillSpot): the board's extent, each key's box as drawn
    // (a rotated key's is the box around it), and the size of a key, the unit its distances go by.
    state.boardSize = { w: width, h: g.height };
    state.keyBoxes = [];
    state.keyUnit = Math.max(1, Math.min(...lay.keys.map(k => Math.min(k.w, k.h))) * g.scale - g.gap);
    state.pillSpots = new Map();
    lay.keys.forEach((k, idx) => {
      const e = el("div", "key");
      e.dataset.idx = idx;
      e.title = `#${idx}`;
      const r = g.rects[idx];
      const a = r.r * Math.PI / 180, cx = g.centers[idx].x, cy = g.centers[idx].y;
      const hw = (Math.abs(r.w * Math.cos(a)) + Math.abs(r.h * Math.sin(a))) / 2;
      const hh = (Math.abs(r.w * Math.sin(a)) + Math.abs(r.h * Math.cos(a))) / 2;
      state.keyBoxes[idx] = { l: cx - hw, t: cy - hh, r: cx + hw, b: cy + hh };
      e.style.left = r.x + "px";
      e.style.top = r.y + "px";
      e.style.width = r.w + "px";
      e.style.height = r.h + "px";
      if (k.r) e.style.transform = `rotate(${k.r}deg)`;
      if (r.h < 50) e.classList.add("short");
      e.appendChild(el("div", "shifted"));
      e.appendChild(el("div", "tap"));
      e.appendChild(el("div", "hold"));
      board.appendChild(e);
      state.keyEls[idx] = e;
    });
    paintHeat();   // the glow lives by index, not on the elements just replaced
    placeStats(g);
  }

  /* Docked as a touch keyboard, its keys are as big as the keyboard's own -- touch.key_mm apart, 1u
   * being the layout's typical key (View.unitOf) -- and never made smaller to fit: what is typed on
   * is a keyboard, not a picture of one. The host says how many CSS pixels a millimetre is on the
   * dock's screen and how wide that screen is ({"kind":"dock", "px_per_mm", "width"}); a host that
   * does not say (the remote page, the iPad's keyboard) gets the board fitted, as before.
   *
   * A board that does not fit -- the screen too narrow for it, or the dock's share of the screen
   * too short -- is the HUD and not a keyboard: drawn to fit, typing nothing (touch.js, the "fit"
   * hook), the banner saying why. The panel is told ({"kind":"dock","fits":false}) and floats the
   * HUD again; a tablet's keyboard stays, a HUD, until it is turned or another keyboard is picked.
   * Decided from the host's numbers, not the window's, which may not have caught up yet. */
  function dockFit() {
    const d = state.dock;
    if (!d || !(d.pxPerMm > 0) || !state.data) return null;
    const lay = state.data.layout;
    const keyMm = Number(state.data.touch && state.data.touch.key_mm) || 18;
    const scale = View.realScale(lay, keyMm, d.pxPerMm);
    const width = Math.max(0, (d.width || window.innerWidth || 0) - DOCK_SIDES);
    const maxHeight = d.maxHeight || window.innerHeight || 360;
    const g = View.geometry(lay, { width, gap: GAP, chrome: DOCK_CHROME, dock: { maxHeight, split: d.split, scale } });
    const mm = (px) => Math.round(px / d.pxPerMm);
    return { fits: g.fits, scale, keyMm, needMm: [mm(g.need.w), mm(g.need.h)], roomMm: [mm(width), mm(maxHeight)] };
  }

  // The fit measured again, before the board is built with it: true when the answer is news, which
  // sayFit then gives touch.js and the panel, once the board is drawn.
  function checkFit() {
    if (!state.dock) return false;
    const before = state.dock.fit;
    const fit = state.dock.fit = dockFit();
    return before === undefined || (!before || before.fits) !== (!fit || fit.fits);
  }

  function sayFit() {
    const fit = state.dock && state.dock.fit;
    fire("fit", !fit || fit.fits);
    if (fit && !fit.fits) hud.post({ kind: "dock", fits: false, key_mm: fit.keyMm, need_mm: fit.needMm, room_mm: fit.roomMm });
  }

  // The banner's word on a board too big to type on here.
  function fitNote() {
    const fit = state.dock && state.dock.fit;
    if (!fit || fit.fits) return state.touchNote;
    return `a HUD only · at ${fit.keyMm} mm a key the board is ${fit.needMm[0]} × ${fit.needMm[1]} mm, ` +
      `the screen has ${fit.roomMm[0]} × ${fit.roomMm[1]} mm for it`;
  }

  // Docked with a split board's halves at the sides, the room between them holds the stats column
  // when it fits (hud.css html.stats-middle): centred there, from the board's top and no taller than
  // it. Not on a tablet's pages (the remote page, a tablet keyboard's), where touch typing counts
  // nothing and the column would only say so.
  const STATS_WIDTH = 188;          // hud.css #stats width
  const STATS_ROOM = STATS_WIDTH + 2 * 12;
  function placeStats(g) {
    const root = document.documentElement, host = $("stats"), frame = $("frame"), board = $("board");
    const fits = !!(state.dock && g.middle && g.middle.w >= STATS_ROOM && host && frame &&
                    !root.classList.contains("remote") && !root.classList.contains("embed"));
    root.classList.toggle("stats-middle", fits);
    if (!host) return;
    if (!fits) { host.style.left = host.style.top = host.style.maxHeight = ""; return; }
    const b = board.getBoundingClientRect(), f = frame.getBoundingClientRect();
    host.style.left = (b.left - f.left + g.middle.x + (g.middle.w - STATS_WIDTH) / 2) + "px";
    host.style.top = (b.top - f.top) + "px";
    fitMiddle();
  }

  // The column in the middle as tall as its stats: the strip grows to hold it, within the room the
  // host allows, and only past that does it scroll. The keys stay where they are.
  function fitMiddle() {
    if (!document.documentElement.classList.contains("stats-middle") || !state.dock) return;
    const host = $("stats"), board = $("board");
    host.style.maxHeight = "";
    const need = Math.ceil(host.scrollHeight || 0);
    const room = Math.max(40, (state.dock.maxHeight || window.innerHeight || 360) - DOCK_CHROME);
    const h = Math.max(state.boardHeight || 0, Math.min(need, room));
    if (board.style.height !== h + "px") board.style.height = h + "px";
    host.style.maxHeight = h + "px";
  }

  // ZMK layer id → keymap entry ({name, drawer, label, cls}); falls back to the YAML order.
  function zl(id) { return View.zl(state.data, id); }

  // The live stack as drawer layer names, top first; the base layer is always last (keystrokes.js).
  const liveStack = () => ks._liveStack();
  const baseStack = () => ks._baseStack();

  function stack() {
    // A layer picked to look at is drawn over the base alone, until the next keystroke (unpick).
    if (state.pick) return state.pick === base() ? [base()] : [state.pick, base()];
    return ks._stack();
  }

  // The binding that wins at idx, walking the stack through transparent keys.
  function resolveBinding(idx, layersTopFirst) { return View.resolve(state.data, idx, layersTopFirst); }

  // A legend is text, or the SVG keymap-drawer draws for a $$glyph$$ (sent with the keymap). The
  // glyph replaces the text rather than joining it: a key's `tap` carries the glyph's own text
  // spelling so something still shows when the SVG could not be resolved.
  //
  // A glyph goes into the page as markup, and this page sees every keystroke, so a glyph is used
  // as it came or not at all -- never rewritten -- and only when nothing in it can run. The SVGs
  // come from icon repos on moving branches, or from a drawer config's own glyph_urls.
  // host/keymap.py (glyph_problem) applies the same rules when the definitions are written and
  // when the message is built; this is the last word, and the two lists are kept the same.
  const GLYPH_TAGS = new Set(["svg", "g", "path", "circle", "ellipse", "rect", "line", "polyline", "polygon",
                              "defs", "clippath", "mask", "lineargradient", "radialgradient", "stop", "title",
                              "desc", "symbol", "use", "text", "tspan"]);
  const glyphVerdicts = new Map();
  function glyphOK(svg) {
    if (!glyphVerdicts.has(svg)) glyphVerdicts.set(svg, glyphIsDrawing(svg));
    return glyphVerdicts.get(svg);
  }
  function glyphIsDrawing(svg) {
    if (typeof svg !== "string" || svg.includes("\0")) return false;
    // Comments as HTML reads them (an icon's licence header is one); then no doctype, entity,
    // CDATA section or processing instruction may be left.
    const s = svg.replace(/<!--(?:-?>|[\s\S]*?--!?>)/g, "");
    if (/<[!?]/.test(s)) return false;
    const tag = /<\/?([A-Za-z][^\s/>]*)((?:[^>"']|"[^"]*"|'[^']*')*)\/?>/g;
    const attr = /([^\s"'<>/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;
    let first = true, between = "", last = 0, m;
    while ((m = tag.exec(s)) !== null) {
      if (first && s.slice(0, m.index).trim() !== "") return false;  // nothing but space outside the root
      if (!first) between += s.slice(last, m.index);
      last = tag.lastIndex;
      const name = m[1].toLowerCase().replace(/^[^:]*:/, "");         // svg:path is path
      if (first) { if (name !== "svg" || m[0][1] === "/") return false; first = false; }
      if (!GLYPH_TAGS.has(name)) return false;
      let a;
      while ((a = attr.exec(m[2])) !== null) {
        const an = a[1].toLowerCase(), av = (a[2] !== undefined ? a[2] : a[3] !== undefined ? a[3] : a[4]) || "";
        if (an.startsWith("on") || an === "style") return false;
        if ((an === "href" || an === "xlink:href") && !av.trim().startsWith("#")) return false;
      }
    }
    if (first || s.slice(last).trim() !== "") return false;          // ...and after it
    return !between.includes("<");                                     // text between tags is text
  }
  function legendHTML(text, glyph) {
    const svg = glyph && state.data.glyphs && state.data.glyphs[glyph];
    return svg && glyphOK(svg) ? `<span class="glyph">${svg}</span>` : null;
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
    const heldMods = Object.keys(state.mods).filter(f => state.mods[f] && MOD_GLYPH[f]).map(f => ({ flag: f, glyph: MOD_GLYPH[f] }));
    // Shift is a modifier, not a layer, so nothing else on the board would show it. Caps word and
    // caps line have their own drawn layer and arrive as a layer change; plain shift, held or
    // tapped as a one-shot, only ever reaches us as this flag. A single lowercase letter is the
    // whole of what it changes: \p{Ll} so the accented alphabets shift too (á -> Á), and a glyph,
    // a word or a symbol is left alone, because shift does not make ␣ or `nav` into anything.
    // A Shift latched on glass (touch.js: the report's modifier byte, either Shift) shifts the
    // legends as one held on the keyboard does.
    const touch = state.touch;
    const shifting = !!state.mods.shift || !!(touch && touch.mods & View.SHIFT_BITS);
    const shiftLegend = t => View.shiftLegend(t, shifting);
    state.data.layout.keys.forEach((k, idx) => {
      const e = state.keyEls[idx];
      const r = resolveBinding(idx, layers);
      e.classList.remove("trans", "blank", "ghost", "held", "activator", "mod", "latched", "locked");
      // A finger on the key lights it however often the board is rebuilt under it; a hold let go
      // and still on (one-shot or locked) is ringed.
      for (const c of View.touchClasses(touch, idx)) e.classList.add(c);
      for (const c of View.bindingClasses(state.data, idx, layers, r)) e.classList.add(c);
      if (!r) {
        fit(e.querySelector(".tap"), "");
        e.querySelector(".hold").textContent = "";
        e.querySelector(".shifted").textContent = "";
        return;
      }
      if (activators.has(idx)) e.classList.add("activator");
      // A held modifier rings the key that turned it on (modSource): the home-row mod held, or the
      // sticky shift tapped. Without positions there is no telling which, so every key that
      // carries it is ringed.
      if (heldMods.some(({ flag, glyph }) => state.modSource[flag] !== undefined
        ? state.modSource[flag] === idx : carries(r.key, glyph))) e.classList.add("mod");
      fit(e.querySelector(".tap"), shiftLegend(r.key.tap) || "", r.key.glyph);
      setLegend(e.querySelector(".hold"), r.key.hold, r.key.glyph_hold);
      setLegend(e.querySelector(".shifted"), r.key.shifted, r.key.glyph_shifted);
    });
  }

  // A drawer layer's banner label: the ZMK table's label when one id is drawn by it, else its name.
  function layerLabel(name) { return View.layerLabel(state.data, name); }

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

  // The name of the drawing on screen, as the banner says it: a layer with no drawing of its own
  // (the Diamond's MACOS, over its base) shows the one under it, and is what the viewer knows it
  // by -- so the highest active layer that is this drawing, or sits undrawn right above it.
  function drawingLabel(drawer) {
    if (state.live && !state.oneShot && !state.momentary.length) {
      let undrawn = null;
      for (const z of state.live.ids.slice().sort((a, b) => b - a).map(zl).filter(Boolean)) {
        if (z.drawer == null) { undrawn = undrawn || z; continue; }
        if (z.drawer === drawer) return (undrawn || z).label;
        undrawn = null;
      }
      if (undrawn && drawer === base()) return undrawn.label;   // the base is under everything, reported or not
    }
    return layerLabel(drawer);
  }

  // The touch keyboard's layers on the banner, as liveSummary says the keyboard's, and what glass
  // is holding on the sub line (touch.js: touch · held, touch · one shot, touch · locked).
  function touchSummary() { return View.touchSummary(state.data, state.touch); }

  // What to print on the banner: an inferred held/one-shot layer on top of the base, else the base.
  function activeSummary() {
    if (state.pick) return { name: layerLabel(state.pick), cls: state.pick === base() ? "off" : "momentary", sub: "picked · type to follow the keyboard" };
    if (state.touch && state.touch.ids) return touchSummary();
    const b = baseSummary();
    const top = state.oneShot || (state.momentary.length ? state.momentary[state.momentary.length - 1].layer : null);
    if (!top) return b;
    return { name: layerLabel(top), cls: "momentary",
      sub: (state.oneShot ? "one shot" : "held") + (state.live ? " (inferred)" : "") + " · over " + b.name.toLowerCase() };
  }

  function renderBanner() {
    const a = activeSummary();
    $("layer").className = a.cls;
    $("layerName").textContent = a.name;
    // A feed that says nothing sees a secret being typed (watched: []) is said on the banner, so
    // that nobody types a password in front of it trusting that it would hold back. Before it, why a
    // board asked to type cannot (fitNote).
    const unwatched = !state.secure && Array.isArray(state.watched) && state.watched.length === 0;
    const sub = fitNote() || a.sub;
    $("layerSub").textContent = state.secure ? "secure input · typing hidden"
      : unwatched ? (sub ? sub + " · typing not watched" : "typing not watched") : sub;
    document.body.classList.toggle("unwatched", unwatched);
    $("board").className = a.cls;
  }

  /* Secure input (host/hudfeed.py SecureInput): a secret is being typed, and the feed has
   * stopped passing on keys. What is still on screen of the typing before it goes too -- the
   * strip, the keys lit, their glow, the macro memory -- and the banner says why the board has
   * stopped following. Anything that arrives anyway (sent in) is drawn nowhere until it ends. */
  function setSecure(on, watched) {
    on = !!on;
    if (Array.isArray(watched)) state.watched = watched.filter(w => typeof w === "string");
    if (on === state.secure) { if (state.data) renderBanner(); return; }
    state.secure = on;
    document.body.classList.toggle("secure", on);
    // What keystrokes.js keeps of the typing -- the keys down, the last few typed, what waits to be
    // settled or told a tap from a hold -- goes with it (and nothing is read until it ends).
    apply(ks.read({ kind: "secure", on }, Date.now()));
    if (on) {
      if (window.keys && window.keys.clear) window.keys.clear();
      for (const id of state.timers.values()) clearTimeout(id);
      state.timers.clear();
      for (const e of state.keyEls) if (e) e.classList.remove("pressed", "combo", "combo-key");
      if (state.comboShown) { state.comboShown.remove(); state.comboShown = null; }
      state.heat.clear();
      paintHeat();
      // Nothing typed before it is counted or sent either: a key that slipped in as the secret
      // began would say what it was. The counts not yet handed to the session and the WPM windows
      // go, as they do when a keymap is replaced; what was already sent (inflight) is the host's.
      state.lastStroke = null; state.lastOwnStroke = null;
      state.unsent = emptyTally();
      state.typing = newTyping(); state.ownTyping = newTyping();
      state.viewCache = null;
    }
    if (state.data) renderBanner();
    renderStats();
  }

  function renderFeed() {
    const f = $("feed");
    if (!f) return;
    // Only the two waiting states are worth a line; once the keyboard reports, say nothing.
    if (!state.data) { f.textContent = "waiting for the keymap…"; f.className = ""; return; }
    // A touch keyboard waits for no keyboard: there may be none plugged in.
    const waiting = !state.live && !state.dock && !document.documentElement.classList.contains("touch");
    f.textContent = waiting ? "waiting for the keyboard's layers…" : "";
    f.className = state.live ? "live" : "";
  }

  // Look at another layer: drawn over the base, the banner saying so, until a keystroke. null
  // (the list's "auto") follows the keyboard again.
  function pickLayer(name) {
    if (name != null && !(state.data && state.data.layers[name])) return;
    state.pick = name == null ? null : name;
    render();
    postSize();
  }
  function unpick() { if (state.pick) pickLayer(null); }

  // The touch keyboard's combos as chips over the board (view.js chips), when it types and the
  // config wants them (touch.combos: "chips"). Drawn again with the keys: the layer decides which.
  function renderChips() {
    if (state.chipsEl) state.chipsEl.remove();
    state.chipsEl = null;
    const t = state.touch;
    if (!t || !t.ids || !state.rects || (state.data.touch || {}).combos !== "chips") return;
    const list = View.chips(state.data, { scale: state.scale, centers: state.centers, rects: state.rects }, t);
    if (!list.length) return;
    const box = el("div");
    box.id = "chips";
    for (const c of list) {
      const e = el("div", ["combo-chip"].concat(c.cls).join(" "));
      e.dataset.combo = c.combo;
      e.style.left = (c.x - c.size / 2) + "px";
      e.style.top = (c.y - c.size / 2) + "px";
      e.style.width = e.style.height = c.size + "px";
      setLegend(e, c.tap, c.glyph);
      box.appendChild(e);
    }
    $("board").appendChild(box);
    state.chipsEl = box;
  }

  function render() {
    if (!state.data) { renderFeed(); renderStats(); return; }
    renderBanner(); renderKeys(); renderChips(); renderFeed();
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
  // Both panels move when the page is dragged, and decide before the page sees the press: each is
  // told where the controls are (host/macos/panel.py DragWebView, host/linux/panel.py drag), so
  // dragging the slider moves the slider. Said again whenever the layout may have moved them.
  function postNoDrag() {
    // Both panels move the window themselves: the macOS bridge, or the Linux panel's handler.
    const handler = panelHandler();
    if (!handler) return;
    // Everything that takes a click: a press there goes to the page, anywhere else it moves the
    // panel, and the macOS panel shows the hand there (host/macos/panel.py DragWebView). The
    // slider's whole box, not just its track, so a press a little off the thumb works.
    let rects = [];
    try {
      // The board itself while it types (touch.js): a press on a key is a keystroke, not a drag.
      const typing = document.documentElement.classList.contains("touch") ? $("board") : null;
      const controls = [$("mini"), $("attach"), $("side"), $("hide"), $("close"), $("statsHead"), bar.layer && bar.layer.pick, typing]
        .concat(["heatmap", "theme", "opacity", "feedback"].map(n => bar[n] && bar[n].chip));
      rects = controls.filter(Boolean).map(e => e.getBoundingClientRect())
        .filter(r => r.width > 0 && r.height > 0)
        .map(r => [r.left, r.top, r.width, r.height].map(v => Math.round(v)));
    } catch (e) { /* no layout (the tests' DOM) */ }
    const said = JSON.stringify(rects);
    if (said === state.noDragSaid) return;
    state.noDragSaid = said;
    handler.postMessage(JSON.stringify({ kind: "nodrag", v: 1, rects }));
  }

  function postSize() {
    let width = (state.data && state.data.hud && state.data.hud.width) || null;
    if (width && bar.host && !bar.host.classList.contains("off") && sideShown()) width += STATS_COLUMN;
    // Docked, the host decides the width (the screen's) and the page only says how tall it came out.
    if (state.dock) width = null;
    const height = Math.ceil(document.body.scrollHeight);
    // The macOS panel reads it on its bridge. The Linux panel has a handler for nothing else
    // (host/linux/panel.py), named apart so that the page never takes it for the bridge a
    // session's counts go through (bridge(), below).
    for (const name of ["zmkhud", "zmkhudsize"]) {
      try { window.webkit.messageHandlers[name].postMessage(JSON.stringify({ kind: "size", width, height })); } catch (e) { /* not this host */ }
    }
    // A panel that can hide the HUD is one that has one of these: offer the button there only.
    document.documentElement.classList.toggle("hideable", !!panelHandler());
    // ...and one that can dock it, attach and detach.
    if (!attachOwn) document.documentElement.classList.toggle("attachable", !!panelHandler());
    postNoDrag();
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
  // The light keys' glow at full heat, the overlay's opacity: the legends must stay readable.
  const HEAT_MAX = 0.55;
  // Dark keys have no glow: a warm key takes the step of the session's ramp its heat is at (hud.css
  // .lh1 to .lh6), dark blue hot and light blue as it cools, the way the light keys' glow goes.
  const LIVE_STEPS = 6;
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

  // The key's --heat, the overlay's opacity, written only when its step changes -- or on dark keys
  // its .lhN, the ramp's step. A key that has cooled is forgotten and both cleared, not left at 0.
  function paintKeyHeat(idx, now) {
    const e = state.heat.get(idx);
    const v = e ? heatAt(e, now) : 0;
    if (e && v <= 0) state.heat.delete(idx);
    const el = state.keyEls[idx];
    if (!el) return;
    const dark = theme() === "dark", warm = Math.pow(v, HEAT_GAMMA);
    const live = dark && v > 0 ? Math.max(1, Math.ceil(warm * LIVE_STEPS)) : 0;   // ceil: a key still warm never paints cold
    for (let i = 1; i <= LIVE_STEPS; i++) if (i !== live && el.classList.contains("lh" + i)) el.classList.remove("lh" + i);
    if (live && !el.classList.contains("lh" + live)) el.classList.add("lh" + live);
    const step = dark ? 0 : Math.ceil(warm * HEAT_LEVELS);
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
   * that was never its own. The counts are the ledger's, so this is the live heatmap summed.
   *
   * Two more read the same steps. `physical`: every layer's presses of a key together, which is
   * where the fingers went whatever the legends said, so every key shows, transparent or not.
   * `speed`: how long each of the layer's own keys takes after the key before it (strokes), the
   * slowest the hottest, from the keys timed at least SPEED_MIN times. */
  const SESSION_LEVELS = 6;
  const SPEED_MIN = 3;
  function heatLevels() {                // drawer idx -> 1..SESSION_LEVELS, for the keys that have one
    const out = new Map(), mode = state.heatMode;
    if (mode !== "session" && mode !== "physical" && mode !== "speed") return out;
    const layers = stack(), top = layers[0], v = view();
    const own = idx => { const r = resolveBinding(idx, layers); return r && r.layer === top && zmkPos(idx) !== null; };
    const logScale = counts => {
      let most = 0;
      for (const [, n] of counts) most = Math.max(most, n);
      for (const [idx, n] of counts) if (n) out.set(idx, Math.max(1, Math.ceil(Math.log1p(n) / Math.log1p(most) * SESSION_LEVELS)));
    };
    if (mode === "session") {
      const counts = v.presses[top] || {};
      logScale(state.keyEls.map((el, idx) => [idx, own(idx) ? counts[zmkPos(idx)] || 0 : 0]));
    } else if (mode === "physical") {
      const total = {};
      for (const layer in v.presses) for (const pos in v.presses[layer]) total[pos] = (total[pos] || 0) + v.presses[layer][pos];
      logScale(state.keyEls.map((el, idx) => [idx, zmkPos(idx) !== null ? total[zmkPos(idx)] || 0 : 0]));
    } else {
      const timed = v.timed[top] || {}, ms = v.ms[top] || {}, means = [];
      state.keyEls.forEach((el, idx) => {
        const pos = zmkPos(idx);
        if (own(idx) && (timed[pos] || 0) >= SPEED_MIN) means.push([idx, ms[pos] / timed[pos]]);
      });
      const lo = Math.min(...means.map(m => m[1])), hi = Math.max(...means.map(m => m[1]));
      for (const [idx, mean] of means) out.set(idx, hi > lo ? 1 + Math.round((mean - lo) / (hi - lo) * (SESSION_LEVELS - 1)) : 1);
    }
    return out;
  }
  function paintSessionHeat() {
    if (!state.data || !state.keyEls.length) return;
    const levels = heatLevels();
    state.keyEls.forEach((el, idx) => {
      const level = levels.get(idx) || 0;
      for (let i = 1; i <= SESSION_LEVELS; i++) if (i !== level && el.classList.contains("hs" + i)) el.classList.remove("hs" + i);
      if (level) el.classList.add("hs" + level, "heated");
      else if (el.classList.contains("heated")) el.classList.remove("heated");
    });
  }

  // Which the keys glow with (host/session.py HEATMAP_MODES). Leaving live lets its glow go at
  // once rather than fade under the other. A choice made here is the host's to keep (it survives
  // a restart, and `zmk-layer-hud heatmap` reads it); one the host sends is only applied.
  const HEAT_MODES = ["live", "session", "physical", "speed", "off"];
  function setHeatmap(mode, fromHost) {
    if (!HEAT_MODES.includes(mode) || mode === state.heatMode) return;
    state.heatMode = mode;
    if (mode !== "live") state.heat.clear();
    paintHeat();
    renderStats();
    if (!fromHost) postToHost({ kind: "heatmap", v: 1, mode });
  }

  // What the viewer chooses on the page besides the heatmap: the keys light or dark, and whether the
  // stats block is out. The feed remembers a choice (host/session.py PREFS) and hands it back with
  // the session; until one is made, the theme is the config's (hud.dark). A page with no feed to
  // tell (the landing page) keeps it in its own storage.
  const PREFS = {
    theme: v => v === "light" || v === "dark",
    side: v => v === "shown" || v === "hidden",
    opacity: v => Number.isInteger(v) && v >= 0 && v <= 100,   // the slider (host/session.py PREFS)
    feedback: v => v === "on" || v === "off",                  // the touch keyboard's click and buzz (the switch)
  };
  const PREFS_KEY = "zmkhud.prefs";
  try { Object.assign(state.prefs, JSON.parse(localStorage.getItem(PREFS_KEY) || "{}")); } catch (e) { /* no storage */ }
  const theme = () => state.prefs.theme || (T("dark") > 0 ? "dark" : "light");
  const sideShown = () => state.prefs.side !== "hidden";
  // The touch keyboard's click and buzz (touch.js): the viewer's choice once made, everywhere; until
  // then on where the board is a touch screen's keyboard (docked: the panels' dock, the remote page,
  // a tablet's keyboard) and off elsewhere.
  const feedbackOn = () => (state.prefs.feedback ? state.prefs.feedback === "on" : !!state.dock);
  // The panels' background: the slider's, else the config's hud.opacity. A GIF still's script may
  // set its own (showDemo), which outranks both.
  const opacity = () => (state.demoOpacity != null ? state.demoOpacity : state.prefs.opacity != null ? state.prefs.opacity : T("opacity"));
  function applyPrefs() {
    const body = document.body;
    const alpha = String(opacity() / 100);
    if (document.documentElement.style.getPropertyValue("--panel-alpha") !== alpha) {
      document.documentElement.style.setProperty("--panel-alpha", alpha);
    }
    if (body.classList.contains("dark") !== (theme() === "dark")) {
      body.classList.toggle("dark", theme() === "dark");
      paintHeat();                  // light keys glow, dark keys take the ramp's steps (paintKeyHeat)
    }
    if (body.classList.contains("side-hidden") === sideShown()) {
      body.classList.toggle("side-hidden", !sideShown());
      followWidth();                // the board beside it is narrower or wider now, unless a panel grows
    }
    const side = $("side");
    if (side) {
      const say = sideShown() ? "hide the stats" : "show the stats";
      if (side.title !== say) { side.title = say; side.setAttribute("aria-label", say); }
      side.setAttribute("aria-pressed", String(sideShown()));
    }
  }
  function setPref(name, value, fromHost) {
    if (!PREFS[name] || !PREFS[name](value)) return;
    const same = state.prefs[name] === value;
    state.prefs[name] = value;
    // A slider being dragged has shown each value already (bar.opacity's input); the one it is let
    // go at is still the one to remember.
    if (!fromHost && (!same || state.pendingPref === name)) {
      state.pendingPref = null;
      try { localStorage.setItem(PREFS_KEY, JSON.stringify(state.prefs)); } catch (e) { /* no storage */ }
      postToHost({ kind: "pref", v: 1, name, value });
    }
    if (same) return;
    applyPrefs();
    renderStats();
    postSize();                     // the block in or out changes the panel's width
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
    return { presses: {}, combos: {}, ms: {}, timed: {}, chars: 0, deleted: 0, active_ms: 0, active_net: 0,
             sfb: 0, bigrams: 0, peak_wpm: 0 };
  }
  function bump(map, layer, key, by) {
    const m = map[layer] || (map[layer] = {});
    m[key] = (m[key] || 0) + (by === undefined ? 1 : by);
  }
  // The drawer key's ZMK position, which is what a session is kept by; null for a key the
  // keymap places nowhere the firmware could report.
  function zmkPos(idx) {
    return state.posOf[idx] !== undefined ? state.posOf[idx] : null;
  }

  // The keyboard's own typing, as opposed to typing sent in (which says whether combos count) or
  // a rehearsal's (synthetic).
  const fromKeyboard = ev => ev.sent !== true && typeof ev.combos !== "boolean" && !ev.synthetic;

  // What keystrokes.js says was struck, once nothing can take it back, counted where it says.
  // true when it counted something.
  function count(f) {
    const tallies = f.own ? [state.local, state.unsent] : [state.local];
    if (f.say === "press") {
      for (const t of tallies) bump(t.presses, f.layer, f.pos);
    } else if (f.say === "combo") {
      for (const t of tallies) bump(t.combos, f.layer, f.key);
    } else if (f.say === "guess") {
      for (const k of f.keys) {
        if (k.pos !== null) for (const t of tallies) bump(t.presses, k.layer, k.pos);
        bumpHeat(k.idx);   // certain now, so it may glow: a guess taken back never did
      }
      if (f.combo) for (const t of tallies) bump(t.combos, f.layer, f.combo);
    } else if (f.say === "keystroke") {
      state.lastStroke = strokeInto(state.local, f, state.lastStroke);
      if (f.own) state.lastOwnStroke = strokeInto(state.unsent, f, state.lastOwnStroke);
    } else {
      return false;
    }
    return true;
  }

  // ---------- strokes ----------

  /* The time each key takes and same-finger bigrams -- keybr's number and a layout analyser's --
   * from the keystrokes in the order they went down (keystrokes.js says which typed: a tap, a
   * chord; a key held typed nothing, and the key struck under it follows the keystroke before it).
   * A key's time is from the keystroke before it, within wpm_idle_ms: after a pause the key is the
   * start of a burst, and its time says nothing about it. A bigram is two keystrokes in a row by
   * two fingers -- a thumb or a chord on either side is not one -- and it is same-finger when one
   * finger struck two different keys. Kept as totals and per key, like every other count: never
   * which key came after which. */
  const fingerOf = idx => (state.data && state.data.fingers && state.data.fingers[idx]) || null;
  const isThumb = f => f === "lt" || f === "rt";

  function strokeInto(tally, f, last) {
    if (f.as === "tap" && last && f.t - last.t <= T("wpm_idle_ms")) {
      bump(tally.ms, f.layer, f.pos, f.t - last.t);
      bump(tally.timed, f.layer, f.pos);
      const finger = fingerOf(f.idx);
      if (finger && last.finger && !isThumb(finger) && !isThumb(last.finger)) {
        tally.bigrams++;
        if (finger === last.finger && f.pos !== last.pos) tally.sfb++;
      }
    }
    return f.as === "chord" ? { t: f.t, finger: null, pos: null } : { t: f.t, finger: fingerOf(f.idx), pos: f.pos };
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
    postWpm(now);
  }

  // The live WPM, to the panel, which puts it beside its menubar icon (host/panelstate.py): said
  // when the number changes, shown or hidden. The keyboard's own typing, as the session counts it.
  function postWpm(now) {
    const wpm = Math.round(wpmOf(state.ownTyping, now));
    if (wpm === state.wpmSaid) return;
    const h = panelHandler();
    if (!h) return;
    state.wpmSaid = wpm;
    h.postMessage(JSON.stringify({ kind: "wpm", wpm }));
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
    postWpm(now);
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

  // A keystroke's time and bigram can be known only after its press was sent (strokes), so they
  // are counts to send of their own.
  const hasCounts = t => Object.keys(t.presses).length || Object.keys(t.combos).length || Object.keys(t.timed).length ||
                         t.chars || t.deleted || t.active_ms || t.bigrams;
  const MAPS = ["presses", "combos", "ms", "timed"];
  const TOTALS = ["chars", "deleted", "active_ms", "active_net", "sfb", "bigrams"];
  function addTally(into, t) {
    for (const kind of MAPS) {
      for (const layer in t[kind] || {}) for (const k in t[kind][layer]) {
        const m = into[kind][layer] || (into[kind][layer] = {});
        m[k] = (m[k] || 0) + t[kind][layer][k];
      }
    }
    for (const k of TOTALS) into[k] += t[k] || 0;
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
                      ms: m.ms || {}, timed: m.timed || {}, totals: m.totals || {} };
    const acked = (m.acks || {})[PAGE_ID] || 0, now = Date.now();
    state.inflight = state.inflight.filter(b => b.seq > acked && now - b.at < INFLIGHT_MS &&
                                                (b.session === null || b.session === m.id));
    state.viewCache = null;
    if (HEAT_MODES.includes(m.heatmap)) setHeatmap(m.heatmap, true);
    for (const name in m.prefs || {}) setPref(name, m.prefs[name], true);
    paintSessionHeat();
    renderStats();
  }

  /* The counts the bar and the session heatmap show. With a host keeping a session: its counts,
   * and -- on the page that reports to it -- what is on the way and what is not sent yet. With
   * none (the demo, a page in a browser): everything this page has been shown since it loaded. */
  function view() {
    if (!state.session) return state.local;
    if (state.viewCache) return state.viewCache;
    const s = state.session, out = emptyTally();
    addTally(out, Object.assign({}, s.totals, { presses: s.presses, combos: s.combos, ms: s.ms, timed: s.timed }));
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

  // A column right of the keys (hud.css #stats): one box per stat, all as wide as the column, each
  // row a label and its value. The column is added to the panel's width rather than taken out of
  // the board's (postSize), so the keys stay the size hud.width gives them. Each value is written
  // only when its text changes.
  const STATS_COLUMN = 188 + 8;    // hud.css: #stats width (a session name and its label fit) + its margin-left
  // Each stat's rows, by label, in the column's order (STAT_DEFAULTS); null is the layer tile's,
  // which says the layer's name instead.
  const STAT_ROWS = { wpm: ["wpm"], session: ["avg wpm", "top wpm"], accuracy: ["accurate"], keys: ["keys", "combos"],
                      layer: [null], time: ["typing"], hands: ["left hand", "right hand"], sfb: ["same finger"],
                      slow: ["slowest", "its time"] };
  const bar = {};
  function buildStats() {
    const host = $("stats");
    if (!host || bar.host) return;
    bar.host = host;
    // labels: one per row; null is a label the stat writes itself (the layer's name). A short
    // value is a tile half the block wide, and a stat of two values two tiles side by side
    // (hud.css #stats .half, .pair), so the block fits beside the panel without making it taller.
    const HALF = ["wpm", "acc", "time", "sfb", "mode", "theme"], PAIR = ["session", "keys", "hands", "slow"];
    const chip = (name, labels) => {
      const c = el("div", "stat " + name + (HALF.includes(name) ? " half" : PAIR.includes(name) ? " pair" : ""));
      const vals = [], names = [];
      for (const label of labels) {
        const row = el("div", "row");
        const l = el("span", null, label == null ? "" : label), b = el("b");
        row.appendChild(l); row.appendChild(b); c.appendChild(row);
        names.push(l); vals.push(b);
      }
      host.appendChild(c);
      return { chip: c, vals, names };
    };
    // In the config's order (host/keymap.py STATS_DEFAULTS), each shown or not by its `stats:` word.
    bar.wpm = chip("wpm", STAT_ROWS.wpm);
    bar.session = chip("session", STAT_ROWS.session);
    bar.accuracy = chip("acc", STAT_ROWS.accuracy);
    bar.keys = chip("keys", STAT_ROWS.keys);
    bar.layer = chip("layer", STAT_ROWS.layer);
    // A combobox of every drawn layer: one picked is drawn until the next keystroke (pickLayer).
    // Its list opens over the page, so picking never makes the panel taller.
    const pick = el("select", "layer-pick");
    pick.setAttribute("aria-label", "look at another layer");
    pick.title = "look at another layer until the next keystroke";
    pick.addEventListener("change", () => pickLayer(pick.value || null));
    bar.layer.chip.appendChild(pick);
    bar.layer.pick = pick;
    bar.time = chip("time", STAT_ROWS.time);
    bar.hands = chip("hands", STAT_ROWS.hands);
    bar.sfb = chip("sfb", STAT_ROWS.sfb);
    bar.sfb.chip.title = "same-finger bigrams: two keys in a row struck by one finger";
    bar.slow = chip("slow", STAT_ROWS.slow);
    bar.slow.chip.title = "the key that takes longest after the key before it, on average";
    // `stats: heatmap` shows both: the session the counts go to, and what the keys glow with.
    bar.named = chip("named", ["session"]);
    bar.heatmap = chip("mode", ["heat"]);
    bar.heatmap.chip.title = "click: " + HEAT_MODES.join(", ");
    bar.heatmap.chip.addEventListener("click", () => setHeatmap(HEAT_MODES[(HEAT_MODES.indexOf(state.heatMode) + 1) % HEAT_MODES.length]));
    bar.theme = chip("theme", ["keys"]);
    bar.theme.chip.title = "click: light or dark keys";
    bar.theme.chip.addEventListener("click", () => setPref("theme", theme() === "dark" ? "light" : "dark"));
    // The touch keyboard's click and buzz, as a switch, while the board types: a tile across the
    // panel, as the background's is (hud.css #stats .stat.feedback).
    bar.feedback = chip("feedback", ["feedback"]);
    bar.feedback.chip.title = "a click, and a buzz where the device has one, on each touch";
    const sw = el("button", "switch");
    sw.type = "button";
    sw.setAttribute("role", "switch");
    sw.setAttribute("aria-label", "sound and vibration on each touch");
    sw.addEventListener("click", () => setPref("feedback", feedbackOn() ? "off" : "on"));
    bar.feedback.chip.appendChild(sw);
    bar.feedback.sw = sw;
    // The panels' background, as a slider: every step shows at once, and the value it is let go at
    // is the one the feed remembers.
    bar.opacity = chip("opacity", ["background"]);
    const slider = el("input", "slider");
    slider.type = "range"; slider.min = "0"; slider.max = "100"; slider.step = "1";
    slider.setAttribute("aria-label", "the panels' background opacity");
    slider.addEventListener("input", () => {
      state.prefs.opacity = Number(slider.value);
      state.pendingPref = "opacity";
      applyPrefs(); renderStats();
    });
    slider.addEventListener("change", () => setPref("opacity", Number(slider.value)));
    bar.opacity.chip.appendChild(slider);
    bar.opacity.slider = slider;
  }
  // The layer tile's combobox: "auto", following the keyboard, then every drawn layer, the one
  // picked selected. Its options are built again only when the keymap changes.
  function renderLayerPick() {
    const pick = bar.layer && bar.layer.pick;
    if (!pick || !state.data) return;
    if (bar.pickFor !== state.data) {
      bar.pickFor = state.data;
      pick.textContent = "";
      for (const name of [null].concat(state.data.layer_order || [])) {
        const o = el("option", null, name ? layerLabel(name) : "auto");
        o.value = name || "";
        pick.appendChild(o);
      }
    }
    const want = state.pick || "";
    if (pick.value !== want) pick.value = want;
  }

  function put(chip, values, names) {
    values.forEach((v, i) => { if (chip.vals[i].textContent !== v) chip.vals[i].textContent = v; });
    (names || []).forEach((v, i) => { if (chip.names[i].textContent !== v) chip.names[i].textContent = v; });
  }
  function toggleChip(chip, on) {
    if (chip.chip.classList.contains("off") === on) chip.chip.classList.toggle("off", !on);
  }
  function fmtDuration(ms) {
    const s = Math.floor(ms / 1000), m = Math.floor(s / 60), h = Math.floor(m / 60);
    return h ? `${h}h ${String(m % 60).padStart(2, "0")}m` : m ? `${m}m` : `${s}s`;
  }
  // The key slowest to strike after the key before it, from those timed often enough to say.
  const SLOW_MIN = 5;
  const SFB_MIN = 20;       // same-finger bigrams are a share once there are this many bigrams (session.py too)
  function slowest(v) {
    let best = null;
    for (const layer in v.timed) for (const pos in v.timed[layer]) {
      const n = v.timed[layer][pos];
      if (n < SLOW_MIN) continue;
      const mean = ((v.ms[layer] || {})[pos] || 0) / n;
      if (!best || mean > best.mean) best = { layer, pos, mean };
    }
    return best;
  }
  function legendAt(layer, pos) {
    const k = ((state.data.layers || {})[layer] || [])[idxAt(pos)];
    const text = (k && (k.tap || k.glyph)) || "?";
    return text.length > 8 ? text.slice(0, 7) + "…" : text;
  }

  /* What every stat says now, by the stat (STAT_ROWS), worked out once for the column and for the
   * panel (postStats); `avg` and `top` are the session's speeds as numbers, null while there is none. */
  function statValues(now) {
    const v = view();
    const fingers = !!(state.data && state.data.fingers);
    const live = Math.round(wpmOf(state.typing, now));
    const avg = v.active_ms >= 10000 ? Math.round(v.active_net / 5 / (v.active_ms / 60000)) : null;
    const out = { fingers, avg, top: v.peak_wpm || null, hands: ["—", "—"], sfb: ["—"] };
    out.wpm = [v.chars || state.typing.win.length ? String(live) : "—"];
    out.session = [avg === null ? "—" : String(avg), v.peak_wpm ? String(v.peak_wpm) : "—"];
    out.accuracy = [v.chars ? pct(Math.max(0, 1 - v.deleted / v.chars)) : "—"];
    out.time = [v.active_ms ? fmtDuration(v.active_ms) : "—"];
    if (fingers) {
      const hand = { l: 0, r: 0 };
      for (const layer in v.presses) for (const pos in v.presses[layer]) {
        const f = fingerOf(idxAt(pos));
        if (f) hand[f[0]] += v.presses[layer][pos];
      }
      const both = hand.l + hand.r;
      if (both) out.hands = [pct(hand.l / both), pct(hand.r / both)];
      out.sfb = [v.bigrams >= SFB_MIN ? (Math.round(v.sfb / v.bigrams * 1000) / 10).toFixed(1) + "%" : "—"];
    }
    const slow = slowest(v);
    out.slow = slow ? [legendAt(slow.layer, slow.pos), Math.round(slow.mean) + " ms"] : ["—", "—"];
    // A combo is one keystroke made with several keys: its share is of keystrokes, not of keys.
    let presses = 0, combos = 0, members = 0;
    for (const layer in v.presses) presses += sum(v.presses[layer]);
    for (const layer in v.combos) for (const key in v.combos[layer]) {
      combos += v.combos[layer][key]; members += v.combos[layer][key] * key.split(",").length;
    }
    const strokes = presses - members + combos;
    out.keys = [fmtCount(presses), strokes > 0 ? pct(combos / strokes) : "—"];
    const top = state.data ? stack()[0] : null;
    out.layer = [top && presses ? pct(sum(v.presses[top] || {}) / presses) : "—"];
    out.layerName = top ? (state.pick ? layerLabel(top) : drawingLabel(top)) : "layer";
    return out;
  }

  // The column's rows as [label, value], as it shows them: the stats the config turns on
  // (`stats:`), the hands and same-finger bigrams only with fingers to tell them by. Not its
  // controls: the layer picker, the heatmap, the keys, the background.
  function statRows(s) {
    const rows = [];
    for (const name of Object.keys(STAT_ROWS)) {
      if (!statOn(name) || (!s.fingers && (name === "hands" || name === "sfb"))) continue;
      STAT_ROWS[name].forEach((label, i) => rows.push([label === null ? s.layerName : label, s[name][i]]));
    }
    return rows;
  }

  /* The column, to the panel, which keeps it in panel.json for the icons (host/panelstate.py):
   * their menus' average and top WPM, and Omarchy's hover panel. Said when it changes, at most
   * once a second, and whether or not the column is on screen: hidden, the HUD still counts. */
  const STATS_POST_MS = 1000;
  function postStats(s) {
    if (!panelHandler()) return;
    // The session's name too, where the column names it.
    const named = statOn("heatmap") && state.session && state.session.name || null;
    const text = JSON.stringify({ kind: "stats", avg: s.avg, top: s.top, session: named, rows: statRows(s) });
    state.statsWaiting = text === state.statsSaid ? null : text;
    if (state.statsWaiting && state.statsTimer === null) {
      state.statsTimer = setTimeout(sayStats, Math.max(0, state.statsAt + STATS_POST_MS - Date.now()));
    }
  }
  function sayStats() {
    state.statsTimer = null;
    const h = panelHandler(), text = state.statsWaiting;
    state.statsWaiting = null;
    if (!h || !text) return;
    state.statsSaid = text;
    state.statsAt = Date.now();
    h.postMessage(text);
  }

  function renderStats() {
    if (!bar.host) buildStats();
    if (!bar.host) return;
    const s = statValues(Date.now());
    postStats(s);
    const off = !(T("stats_bar") > 0);
    if (bar.host.classList.contains("off") !== off) bar.host.classList.toggle("off", off);
    if (off) { followBar(); return; }
    for (const name of Object.keys(STAT_DEFAULTS)) {
      // Hands and same-finger bigrams need to know whose finger struck each key.
      toggleChip(bar[name], statOn(name) && (s.fingers || (name !== "hands" && name !== "sfb")));
    }
    const named = state.session && state.session.name;
    toggleChip(bar.named, statOn("heatmap") && !!named);
    for (const name of Object.keys(STAT_ROWS)) {
      if (name === "layer") put(bar.layer, s.layer, [s.layerName]);
      else if (s.fingers || (name !== "hands" && name !== "sfb")) put(bar[name], s[name]);
    }
    renderLayerPick();
    put(bar.named, [named || ""]);
    bar.named.chip.title = named || "";   // a long name is cut short on the column
    put(bar.heatmap, [state.heatMode]);
    put(bar.theme, [theme()]);
    toggleChip(bar.feedback, document.documentElement.classList.contains("touch"));
    if (bar.feedback.sw.getAttribute("aria-checked") !== String(feedbackOn())) bar.feedback.sw.setAttribute("aria-checked", String(feedbackOn()));
    put(bar.feedback, [feedbackOn() ? "on" : "off"]);
    put(bar.opacity, [opacity() + "%"]);
    if (bar.opacity.slider.value !== String(opacity())) bar.opacity.slider.value = String(opacity());
    bar.opacity.slider.style.setProperty("--fill", opacity() + "%");   // the track's filled part (hud.css)
    followBar();
  }
  // A column taller than the board makes the page taller, and one shown or hidden changes the
  // panel's width (postSize): the panel sizes itself from what the page says.
  function followBar() {
    fitMiddle();          // stats shown or hidden change how tall the column in the middle is
    const shape = bar.host.offsetHeight + (bar.host.classList.contains("off") ? "/off" : "") + (sideShown() ? "" : "/hidden");
    if (shape !== bar.shape) { bar.shape = shape; postSize(); } else postNoDrag();
  }

  // ---------- resolver ----------

  // Where a combo's pill goes: as near the chord as it can be without covering any of its keys,
  // and all of it on the board -- the panel is sized to the board, so a pill over its edge would
  // be cut off. Spots every 4px are tried. One over other keys costs the share of the pill they
  // would hide, weighed against distance, so open room nearby (between the halves, beside a
  // thumb cluster, above a staggered column) wins, and a board with none still gets the pill
  // right beside its chord, over keys that are not in it. Only where every spot touches the
  // chord does the pill go over its own keys, as little as it can. Kept per chord and size, so a
  // combo always lands in the same place.
  const PILL_CLEAR = 3;      // px kept between a pill and its chord's keys
  const PILL_HIDE = 2.5;     // a pill wholly over other keys costs as much as being this many keys away
  function pillSpot(positions, w, h) {
    const memo = positions.slice().sort((a, b) => a - b).join(",") + ":" + Math.round(w) + "x" + Math.round(h);
    if (state.pillSpots.has(memo)) return state.pillSpots.get(memo);
    const boxes = state.keyBoxes, mine = new Set(positions), own = positions.map(i => boxes[i]);
    const tx = own.reduce((a, k) => a + (k.l + k.r) / 2, 0) / own.length;
    const ty = own.reduce((a, k) => a + (k.t + k.b) / 2, 0) / own.length;
    const area = (l, t, r, b, k, pad) => Math.max(0, Math.min(r, k.r + pad) - Math.max(l, k.l - pad))
      * Math.max(0, Math.min(b, k.b + pad) - Math.max(t, k.t - pad));
    // Every centre a box of `size` can take inside `extent`, ends included; the middle if it cannot fit.
    const span = (size, extent) => {
      if (size >= extent) return [extent / 2];
      const out = [];
      for (let c = size / 2; c < extent - size / 2; c += 4) out.push(c);
      out.push(extent - size / 2);
      return out;
    };
    // Nearest first: the distance alone is the least a spot can cost, so once it reaches the best
    // found so far nothing further out can win.
    const spots = [];
    for (const y of span(h, state.boardSize.h)) {
      for (const x of span(w, state.boardSize.w)) spots.push({ x, y, d: Math.hypot(x - tx, y - ty) / state.keyUnit });
    }
    spots.sort((p, q) => p.d - q.d || p.y - q.y || p.x - q.x);
    let best = null;
    for (const clear of [true, false]) {
      for (const { x, y, d } of spots) {
        if (best && d >= best.cost) break;
        const l = x - w / 2, t = y - h / 2, r = x + w / 2, b = y + h / 2;
        const onOwn = own.reduce((a, k) => a + area(l, t, r, b, k, PILL_CLEAR), 0);
        if (clear && onOwn > 0) continue;
        let hidden = 0;
        boxes.forEach((k, i) => { if (!mine.has(i)) hidden += area(l, t, r, b, k, 0); });
        const cost = d + PILL_HIDE * hidden / (w * h) + 1000 * onOwn / (w * h);
        if (!best || cost < best.cost) best = { x, y, cost };
      }
      if (best) break;
    }
    const spot = { x: best.x, y: best.y };
    state.pillSpots.set(memo, spot);
    return spot;
  }

  // A combo is drawn the way keymap-drawer draws it: a pill with the combo's legend, linked to
  // each of its keys, fading after a moment. The pill sits beside the chord rather than on it
  // (pillSpot): the keys flashing there are what it is there to explain.
  function showCombo(positions, key) {
    const board = $("board");
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
    board.appendChild(pill);
    // Its laid-out size (the grow-in transform does not count), with hud.css's least as the
    // fallback where nothing lays it out.
    const spot = pillSpot(positions, pill.offsetWidth || 64, pill.offsetHeight || 43);
    pill.style.left = spot.x + "px"; pill.style.top = spot.y + "px";

    // Links from the pill to each key of the combo.
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "combo-links");
    svg.setAttribute("width", state.boardSize.w); svg.setAttribute("height", state.boardSize.h);
    for (const idx of positions) {
      const k = state.keyBoxes[idx];
      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("x1", spot.x); line.setAttribute("y1", spot.y);
      line.setAttribute("x2", (k.l + k.r) / 2); line.setAttribute("y2", (k.t + k.b) / 2);
      svg.appendChild(line);
    }
    board.appendChild(svg);
    requestAnimationFrame(() => { pill.classList.add("show"); svg.classList.add("show"); });
    for (const idx of positions) state.keyEls[idx].classList.add("combo-key");
    const shown = { positions };
    livePills.add(shown);
    // A key is a combo's while a pill up names it: when this one goes, its keys stop being marked
    // unless another pill still up names them too (the bigger combo that took this one's place).
    const unmark = () => {
      livePills.delete(shown);
      for (const idx of positions) {
        if (![...livePills].some(o => o.positions.includes(idx))) state.keyEls[idx].classList.remove("combo-key");
      }
    };
    const timer = setTimeout(() => {
      pill.classList.remove("show"); svg.classList.remove("show");
      unmark();
      setTimeout(() => { pill.remove(); svg.remove(); }, 200);
    }, T('combo_pill_ms'));
    // A handle to take the pill down early when a longer legend supersedes it: its time is over.
    return { remove() { if (!livePills.has(shown)) return; clearTimeout(timer); pill.remove(); svg.remove(); unmark(); } };
  }
  const livePills = new Set();   // the pills up: {positions}

  // `held`: keys down at the time (keystrokes.js), lit until they come up and timed by their own.
  function flash(indices, cls, held) {
    for (const idx of indices) {
      const e = state.keyEls[idx];
      if (!e) continue;
      e.classList.add("pressed");
      if (cls) for (const c of cls.split(" ")) if (c) e.classList.add(c);
      if (held && held.includes(idx)) continue;
      clearTimeout(state.timers.get(idx));
      state.timers.set(idx, setTimeout(() => e.classList.remove("pressed", "combo"), T('press_ms')));
    }
  }

  // A key that carries a modifier: a home-row mod (hold legend) or the modifier key itself (tap).
  const carries = (key, glyph) => !!key && ((key.hold && key.hold.includes(glyph)) || key.tap === glyph);

  // ---------- public API ----------

  /* What keystrokes.js answered. The layers it applied go on the board, and to the scripts that
   * follow them (touch.js); when it is waiting for something, it is asked again then. */
  let ksTimer = null, ksDue = null;
  function apply(out) {
    let counted = false;
    for (const said of out.said) {
      if (said.say === "layers") { render(); fire("layers", said.ids); }
      else if (!state.demo && count(said)) counted = true;   // a GIF still: drawn, never counted
    }
    if (counted) { state.viewCache = null; armTally(); paintSessionHeat(); renderStats(); }
    for (const op of out._paint) paintOp(op);
    armKs(out.next);
    return out;
  }
  // A guess's pill, by the id keystrokes.js gave it, while it may still be taken down early.
  const pills = new Map();
  // What the page still draws itself of a keystroke (keystrokes.js _paint).
  function paintOp(op) {
    if (op.op === "down") {
      flash([op.idx]);
      bumpHeat(op.idx);    // a position is never taken back, so it warms its key at once
      // Stay lit until the release arrives (a safety timeout covers a lost report).
      clearTimeout(state.timers.get(op.idx));
      state.timers.set(op.idx, setTimeout(() => hud.releaseAt(op.pos), T('held_timeout_ms')));
    } else if (op.op === "keys") {
      renderKeys();
    } else if (op.op === "group") {
      state.comboShown = null;         // a new chord: the pill up is an earlier one's, and fades by itself
    } else if (op.op === "combo") {
      // A third key within the term makes a bigger combo: the smaller one's pill comes down.
      if (state.comboShown) state.comboShown.remove();
      // Not flash(): these keys are down, and it is their release that unlights them. flash's
      // press_ms timer would replace the held timer each key got from its own press and take
      // the chord out from under the user's fingers after a third of a second — along with
      // the safety net that covers a lost release.
      for (const p of op.positions) { const e = state.keyEls[p]; if (e) e.classList.add("pressed", "combo"); }
      state.comboShown = showCombo(op.positions, op.key);
    } else if (op.op === "flash") {
      flash(op.idxs, op.cls, op.held);
    } else if (op.op === "pill") {
      for (const [id, p] of pills) if (op.t - p.t > T("combo_pill_ms") + 1000) pills.delete(id);   // long gone by itself
      pills.set(op.id, { t: op.t, handle: showCombo(op.positions, op.key) });
    } else if (op.op === "unlight") {
      // A guess taken back: its keys and its pill go now.
      for (const idx of op.idxs) {
        const e = state.keyEls[idx];
        if (e) { e.classList.remove("pressed", "combo"); clearTimeout(state.timers.get(idx)); }
      }
      if (op.pill !== null && pills.has(op.pill)) { pills.get(op.pill).handle.remove(); pills.delete(op.pill); }
    } else if (op.op === "render") {
      render();
    } else if (op.op === "up") {
      // The flash fades out from now.
      const e = state.keyEls[op.idx];
      clearTimeout(state.timers.get(op.idx));
      state.timers.set(op.idx, setTimeout(() => e.classList.remove("pressed", "combo"), T('release_ms')));
    }
  }
  function armKs(next) {
    if (next === ksDue) return;
    clearTimeout(ksTimer);
    ksTimer = null; ksDue = next;
    if (next === null) return;
    ksTimer = setTimeout(() => { ksTimer = null; ksDue = null; apply(ks.tick(Date.now())); }, Math.max(0, next - Date.now()));
  }

  const hud = {
    // The keymap message. Re-sent by the host when a sync rewrites it: geometry and legends
    // are rebuilt, the live layer set and the daemon code are kept.
    load(data) {
      if (typeof data === "string") data = JSON.parse(data);
      if (!data || !data.layout || !data.layers) return;
      state.data = data;
      // Read now, answered once the board is built for it.
      const loaded = ks.read(data.kind === "keymap" ? data : Object.assign({ kind: "keymap" }, data), Date.now());
      applyPrefs();                 // the config's hud.dark, until the viewer chose
      state.pick = null; state.comboShown = null;
      // Keystrokes on the keymap going away are not followed by the next one's keys.
      state.lastStroke = null; state.lastOwnStroke = null;
      // Heat outlives a reload (it fades in seconds anyway), but not on keys this layout lacks.
      for (const idx of [...state.heat.keys()]) if (idx >= data.layout.keys.length) state.heat.delete(idx);
      const fitNews = checkFit();   // a keyboard of another size, docked: does it still fit?
      buildBoard();
      if (fitNews) sayFit();
      renderTitle();
      postSize();
      render();
      apply(loaded);
      fire("load", data);
    },
    // The keyboard's active ZMK layer ids (layer 0 omitted). Clears every inference: from now on
    // the stack is what the keyboard says (keystrokes.js, which may hold a drop back for a flash).
    setLayers(ids) {
      if (typeof ids === "string") ids = JSON.parse(ids);
      if (!Array.isArray(ids)) return;
      apply(ks.read({ kind: "layers", ids }, Date.now()));
    },
    // Firmware `positions;`: the physical key at ZMK position `pos` was pressed. The one exact
    // source for what to light, whatever the key produced (chords, combos, macros, modifiers,
    // layer keys). Two or more positions within a combo term that form a combo on the live
    // stack draw that combo's pill. `sent`: this press was sent in, not the keyboard's (it lights
    // its key the same, and is never counted into a session).
    pressAt(pos, sent) {
      if (!state.data || state.secure) return;
      unpick();                        // a key down: the board follows the keyboard again
      const now = Date.now();
      // A key going down is a keystroke of its own: a layer change held back for the previous
      // one's flash is the keyboard's state now, and this key resolves on it -- and whatever follows
      // the layers (touch.js) hears of them first.
      apply(ks._flush(now));
      apply(ks.read({ kind: "press", pos, sent: !!sent }, now));
    },
    // Leave live mode (tests, or a host that lost the keyboard).
    clearLayers() { apply(ks.read({ kind: "layers", ids: null }, Date.now())); render(); },
    // The key at ZMK position `pos` went up: the flash fades out from now.
    releaseAt(pos) {
      if (!state.data) return;
      apply(ks.read({ kind: "release", pos }, Date.now()));
    },
    // The keyboard that was opened (its HID product name): the default title.
    setDevice(name) { if ((name || "") !== state.device) { state.device = name || ""; renderTitle(); } },
    key(ev) {
      if (typeof ev === "string") ev = JSON.parse(ev);
      if (state.secure) return;   // nothing typed shows while it is a secret (setSecure)
      statsKey(ev);   // first: what it typed counts even while positions place the keys
      if (state.data && ev.type === "keyDown" && !ev.repeat) unpick();   // typing again: the board follows the keyboard
      const out = apply(ks.read(ev.kind === "key" ? ev : Object.assign({ kind: "key" }, ev), Date.now()));
      // The typed-keys strip on the same page, with the board's name for a key the feed cannot name.
      if (window.keys) window.keys.key(out.label ? Object.assign({}, ev, { label: out.label }) : ev);
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
      else if (m.kind === "secure") setSecure(m.on, m.watched);
      else if (m.kind === "heat") setHeatmap(m.mode, true);   // a demo's moment: shown, never kept
      else if (m.kind === "dock") hud.setDock(m);
      else if (hooks[m.kind]) fire(m.kind, m);   // touch and typing: touch.js (docs/touch.md)
      if (m.device) hud.setDevice(m.device);  // the keyboard that is typing names the panel
    },
    // Another script's hook: "load" (the keymap message), "layers" (the keyboard's ids), or a
    // message kind hud.receive has no handling of its own for ("touch", "typing").
    on(name, fn) { (hooks[name] = hooks[name] || []).push(fn); },
    // What the touch keyboard holds (touch.js): {ids: its layers top first, down: drawer idx under
    // a finger, latched: {idx: "oneshot"|"locked"}, mods: the report's modifier byte, note: the
    // banner's sub line}; null when it is off. Drawn over whatever the keyboard reports.
    setTouchView(view) {
      const before = (state.touch && state.touch.down) || [];
      state.touch = view || null;
      apply(ks.read({ kind: "touch", ids: view && view.ids ? view.ids : null }, Date.now()));
      const now = (view && view.down) || [];
      for (const idx of before) {
        // A finger lifted: its key goes dark, unless the keyboard itself is holding it.
        if (!now.includes(idx) && state.keyEls[idx] && !state.held.has(idx)) state.keyEls[idx].classList.remove("pressed");
      }
      if (view && now.length) unpick();
      if (state.data) render();
    },
    // Docked along the bottom of a screen ({"kind":"dock","on":true,"max_height":N}): the board as
    // wide as the window and no taller than the host allows, and nothing around it but the banner.
    // A split board's halves go to the sides unless it says "split": false. With "px_per_mm" and
    // "width" (the screen's), its keys are their real size, or it is a HUD only (dockFit).
    setDock(d) {
      const on = !!(d && d.on);
      const was = state.dock;
      state.dock = on ? { maxHeight: Number(d.max_height) || null, split: d.split !== false,
                          pxPerMm: Number(d.px_per_mm) || null, width: Number(d.width) || null } : null;
      document.documentElement.classList.toggle("dock", on);
      const fitNews = on ? checkFit() : !!(was && was.fit && !was.fit.fits);   // floating, there is no keyboard to refuse
      if (!on) setMini(false);
      $("attach").setAttribute("aria-pressed", String(on));
      $("attach").setAttribute("aria-label", on ? "detach" : "attach as a touch keyboard");
      $("attach").title = !on ? "attach: a touch keyboard along the bottom of the screen" :
        attachOwn ? attachOwn.title || "detach" : "detach: the floating HUD again";
      if (state.data) { buildBoard(); render(); }
      if (fitNews) sayFit();
      postSize();
    },
    // Attach and detach for a page that does it its own way (the browser's, in touch.js; a tablet
    // keyboard's, handing the field back to the keyboard used before): fn(on) is called with
    // what is asked for, and fn.title names what detaching does. Else a panel is asked, as its
    // menu's Dock and Float ask it ({"kind":"attach","on":bool}).
    setAttach(fn) {
      attachOwn = fn || null;
      document.documentElement.classList.toggle("attachable", !!attachOwn || !!panelHandler());
    },
    setMini(on) { setMini(on); },
    // Why the board, asked to type, cannot (touch.js): on the banner while it is so.
    setTouchNote(text) {
      if ((text || "") === state.touchNote) return;
      state.touchNote = text || "";
      if (state.data) renderBanner();
    },
    // A touched key's glow: never counted, never in a session (the keyboard's own counts are the
    // session's), and none at all while a secret is being typed.
    warmKey(idx) { if (!state.secure) bumpHeat(idx); },
    // Whether a touch clicks and buzzes (touch.js): the switch's choice, else the mode's.
    feedbackOn() { return feedbackOn(); },
    // A message for the panel this page is in (its bridge); false where there is none.
    post(obj) {
      const h = panelHandler();
      if (!h) return false;
      h.postMessage(JSON.stringify(obj));
      return true;
    },
    // Something the panel's drag rects depend on changed (touch.js turning typing on or off).
    relayout() { postSize(); },
    // Where the keys are drawn, for hit-testing a touch between them (touch.js).
    geometry() {
      return state.data ? { scale: state.scale, gap: state.gap || GAP, keys: state.data.layout.keys,
                            centers: state.centers, posOf: state.posOf } : null;
    },
    // What the keys glow with: "live" (what was just typed), "session" (every press counted) or
    // "off". The bar's last chip cycles it too.
    setHeatmap(mode) { setHeatmap(mode); },
    setPref(name, value) { setPref(name, value); },
    pickLayer(name) { pickLayer(name); },
    // The counts, for tests and hosts: everything shown (`local`), the keyboard's own not yet
    // handed on (`unsent`), keystrokes still waiting to be counted, and the live WPM.
    stats: {
      local: () => JSON.parse(JSON.stringify(state.local)),
      unsent: () => JSON.parse(JSON.stringify(state.unsent)),
      pending: () => (state.demo ? 0 : state.ledger.length),
      wpm: () => Math.round(wpmOf(state.typing, Date.now())),
      view: () => JSON.parse(JSON.stringify(view())),   // what the bar shows
    },
    state,
  };
  window.hud = hud;

  // The chevron: the stats block out or away.
  const sideBtn = $("side");
  if (sideBtn) sideBtn.addEventListener("click", () => setPref("side", sideShown() ? "hidden" : "shown"));
  applyPrefs();

  // The panel's own handler: the macOS bridge, or the Linux panel's, which carries size and hide.
  function panelHandler() {
    for (const name of ["zmkhud", "zmkhudsize"]) {
      try { const h = window.webkit.messageHandlers[name]; if (h) return h; } catch (e) { /* not WebKit */ }
    }
    return null;
  }

  // Attach and detach (setAttach): docked along the bottom of the screen as a touch keyboard, or the
  // floating HUD again. Minimize, docked: the keyboard down to its banner, a bar of buttons, and
  // back -- room for the windows above without undocking it; it types nothing while it is a bar.
  const attachBtn = $("attach");
  if (attachBtn) attachBtn.addEventListener("click", () => {
    const on = !state.dock;
    if (attachOwn) { attachOwn(on); return; }
    const h = panelHandler();
    if (h) h.postMessage(JSON.stringify({ kind: "attach", on }));
  });
  function setMini(on) {
    on = !!on && !!state.dock;
    if (on === state.mini) return;
    state.mini = on;
    document.documentElement.classList.toggle("mini", on);
    const b = $("mini");
    if (b) {
      b.setAttribute("aria-pressed", String(on));
      b.title = on ? "maximize: the keyboard back" : "minimize: the keyboard down to this bar, still docked";
    }
    fire("mini", on);                         // touch.js lets go of whatever is held
    if (!on && state.data) { buildBoard(); render(); }   // laid out afresh: it was not, while hidden
    postSize();
  }
  const miniBtn = $("mini");
  if (miniBtn) miniBtn.addEventListener("click", () => setMini(!state.mini));

  // Hide: the panel takes itself off screen, and this page goes on counting as before.
  const hideBtn = $("hide");
  if (hideBtn) hideBtn.addEventListener("click", () => {
    const h = panelHandler();
    if (h) h.postMessage(JSON.stringify({ kind: "hide" }));
  });

  // The stats block's heading, the HUD's name, links to the landing page. A panel's web view opens
  // no link of its own, so there the panel is asked to open it in the browser; it knows the one
  // address, and the page names none. A browser follows the link into a tab of its own.
  const siteLink = $("statsHead");
  if (siteLink) siteLink.addEventListener("click", e => {
    const h = panelHandler();
    if (!h) return;
    e.preventDefault();
    h.postMessage(JSON.stringify({ kind: "site" }));
  });

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
  // ...and when the board's own width changes while the window's does not: the stats column shown or
  // hidden in a browser, which no panel resizes, would leave the keys drawn over it. A moment later,
  // so a panel that grows the window for the column has done so, and the board is drawn once, at its
  // width; asked by the column's chevron itself as well as by an observer, which a page that is not
  // being painted (a hidden tab) never hears from. Put away (a minimized keyboard), the board has no
  // width to follow.
  function followWidth() {
    clearTimeout(widthDue);
    widthDue = setTimeout(() => {
      widthDue = null;
      const w = $("board").clientWidth;
      if (state.data && !state.demoShown && w > 0 && Math.abs(w - (state.builtWidth || 0)) >= 1) { buildBoard(); render(); postSize(); }
    }, 150);
  }
  if (window.ResizeObserver) new window.ResizeObserver(followWidth).observe($("board"));

  // Generic host: index.html?ws=ws://127.0.0.1:8766 — messages are
  //   {"kind":"keymap",…}  {"kind":"key", ...event}  {"kind":"layers","ids":[…]}   (host/hudfeed.py speaks this).
  const params = new URLSearchParams(location.search);
  // ?ws=, ?keymap=, ?script= and ?timeline= name what the page connects to and fetches. They are
  // for the hosts and for development, both of which load the page from disk or from this machine;
  // a published copy honours none of them, or a link could point it at any server and have the
  // page draw what that server says -- a glyph is markup.
  const LOCAL_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]", "::1"]);
  const local = location.protocol === "file:" || LOCAL_HOSTS.has(location.hostname);
  if (!local && ["ws", "keymap", "script", "timeline"].some(p => params.get(p) !== null)) {
    console.warn("hud: ?ws=, ?keymap=, ?script= and ?timeline= are honoured only from file: or a local server");
  }
  // A GIF still is drawn as it always was: no glow, whatever demoFrame presses. Said here, before
  // anything loads -- demoShown arrives only after the frame's keys have gone down.
  if (params.get("demo") !== null) { state.demo = true; document.body.classList.add("demo"); }
  // Framed by another page (the landing page's demo): that page has the keyboard, and sends what
  // is typed through hud.receive. So the dev keydown listener below stays off -- it swallows every
  // key but F5 and ⌘ chords, Tab included, and Tab is the way out of the frame -- and the ✕, which
  // has no host to close, goes (hud.css html.embed). The heading is no link there: the page around
  // the frame is the one it opens.
  const embedded = params.get("embed") !== null;
  if (embedded) {
    document.documentElement.classList.add("embed");
    if (siteLink) siteLink.removeAttribute("href");
  }
  const wsUrl = local ? params.get("ws") : null;
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
      state.demoOpacity = script.opacity;
      applyPrefs();
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

  if (local && params.get("keymap")) {
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
  // Not on the remote page (host/remote.py): a tablet's own keyboard types into the computer there
  // only through the board.
  const remote = document.documentElement.classList.contains("remote");
  if (location.protocol.startsWith("http") && !embedded && !remote) window.addEventListener("keydown", e => {
    const name = e.key.length === 1 ? null : e.key.toLowerCase().replace("arrow", "").replace("backspace", "delete").replace("enter", "return");
    hud.key({ type: "keyDown", chars: e.key.length === 1 ? e.key : "", name, flags: {} });
    if (e.key !== "F5" && !e.metaKey) e.preventDefault();
  });
  render();
})();
