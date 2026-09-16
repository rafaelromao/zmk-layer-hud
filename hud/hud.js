/* zmk-layer-hud — renderer + keymap resolver.
 *
 * The page carries no keymap: the host sends one, built at runtime from a keymap-drawer YAML by
 * host/keymap.py (physical layout, layers, combos, the ZMK layer id table, optional hints), and
 * sends it again whenever that file changes. The host drives the page through window.hud:
 *   hud.load(keymap)                   the {"kind":"keymap", ...} message
 *   hud.setLayers([ids])               the keyboard's active ZMK layer ids (ground truth; the
 *                                      firmware's layer signal, decoded by host/hudfeed.py)
 *   hud.key({type, chars, name, flags}) one key or modifier event, decoded from the same reports
 *   hud.pressAt(pos) / hud.releaseAt(pos)  a key going down / up by ZMK position (firmware
 *                                      `positions;`): the exact key lights while held, then fades;
 *                                      characters then only feed the strip
 *   hud.press([idx...])                light keys directly (tests)
 *
 * Two modes:
 *   live      after the first setLayers: the stack is exactly what the keyboard reports; a key is
 *             resolved on that stack (combos included). A key that cannot be placed there is
 *             attributed by inference and drawn dashed ("inferred").
 *   emulated  before any setLayers (firmware without the module): only the base layer is known
 *             and everything else is inferred from the typed characters, guided by `extras`.
 *
 * In a browser (http://) it accepts real key events and `?keymap=keymap.json` loads a dumped
 * message, so the page can be developed without a host.
 */
(function () {
  "use strict";

  const GAP = 6;             // px between keys at the drawn scale
  // Every timing the page uses comes from the config's `hud:` section (host/keymap.py fills the
  // defaults in); these are only the fallbacks for a keymap message without it.
  const DEFAULTS = { opacity: 86, press_ms: 320, release_ms: 60, held_timeout_ms: 5000, momentary_ms: 700, combo_slack_ms: 20, activator_ms: 400,
    positions_fresh_ms: 3000, combo_pill_ms: 1000, sequence_ms: 200, sequence_max: 6, one_shot_ms: 450 };
  const T = name => (state.data && state.data.hud && state.data.hud[name] != null) ? state.data.hud[name] : DEFAULTS[name];
  const recentPos = [];

  // Named keys → the legend text keymap-drawer keymaps usually use for them.
  const NAMED = {
    space: "␣", return: "↵", escape: "⎋", delete: "⌫", forwarddelete: "⌦", tab: "⇥",
    left: "←", right: "→", up: "↑", down: "↓", home: "⇱", end: "⇲",
    pagedown: "⇟", pageup: "⇞",
  };
  // Alternative spellings a drawer file may use for the same named key.
  const ALIASES = { "␣": ["space", "spc", "SPACE"], "↵": ["⏎", "enter", "ret", "RET"], "⎋": ["esc", "ESC"],
    "⌫": ["bspc", "BSPC", "backspace"], "⌦": ["del", "DEL"], "⇥": ["tab", "TAB"] };
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
    keyEls: [],
    timers: new Map(),
    scale: 1,
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

  // A legend is text, or the SVG keymap-drawer draws for a $$glyph$$ (sent with the keymap).
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
        if (state.activatorOf[name] !== undefined) activators.add(state.activatorOf[name]);
        else for (const a of activatorsOf(name)) activators.add(a);
      }
    }
    const heldMods = Object.keys(state.mods).filter(f => state.mods[f]).map(f => MOD_GLYPH[f]).filter(Boolean);
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
      fit(e.querySelector(".tap"), r.key.tap || "", r.key.glyph);
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
    $("layerSub").textContent = a.sub;
    $("board").className = a.cls;
  }

  function renderFeed() {
    const f = $("feed");
    if (!f) return;
    // Only the two waiting states are worth a line; once the keyboard reports, say nothing.
    if (!state.data) { f.textContent = "waiting for the keymap…"; f.className = ""; return; }
    f.textContent = state.live ? "" : "waiting for the keyboard's layers…";
    f.className = state.live ? "live" : "";
  }

  function render() { if (!state.data) { renderFeed(); return; } renderBanner(); renderKeys(); renderFeed(); }

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
  function legendMatches(legend, token) {
    if (!legend) return false;
    if (legend === token) return true;
    if (legend.includes("|") && legend.split("|").some(part => part.trim() === token)) return true;
    const alts = ALIASES[token];
    if (alts && alts.includes(legend)) return true;
    if (token.length > 1) { const c = core(token); return c.length > 1 && c === core(legend); }
    return false;
  }

  const isLetter = t => /^[\p{L}]$/u.test(t);

  // Letter-producing combos on the base layer are commands (e.g. vim motions) rather than typing
  // when `extras.letter_combos_on` is set: outside those layers they are ignored, so a typed
  // letter missing from the base is attributed to the secondary alpha layer instead.
  function findOnLayer(layer, token, commandLayersActive) {
    const keys = state.data.layers[layer];
    if (!keys) return null;
    for (let i = 0; i < keys.length; i++) if (keys[i].type !== "trans" && legendMatches(keys[i].tap, token)) return [i];
    const gate = extras().letter_combos_on;
    for (const c of state.data.combos) {
      if (!c.layers.includes(layer) || !legendMatches(c.key.tap, token)) continue;
      if (gate && gate.length && isLetter(token) && layer === base() && !commandLayersActive) continue;
      return c.positions.slice();
    }
    return null;
  }

  function tokenFor(ev) {
    if (ev.name && NAMED[ev.name]) return NAMED[ev.name];
    if (ev.chars && ev.chars.length === 1) return ev.chars;
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
  function resolveOnStack(token, layers, commandLayersActive) {
    const shiftLayers = (extras().sticky || []).filter(l => /shift/i.test(l) && !layers.includes(l));
    const searchStack = /^\p{Lu}$/u.test(token) ? [...shiftLayers, ...layers] : layers;
    for (const layer of searchStack) {
      const hit = findOnLayer(layer, token, commandLayersActive);
      if (hit) return { hit, layer, viaShift: shiftLayers.includes(layer) };
    }
    return null;
  }

  // Recent keyDown tokens with the keys they lit, for multi-key legends ("->", "=>", "&&", "()").
  const recent = [];
  function remember(token, lit, pill) {
    const now = Date.now();
    while (recent.length && now - recent[0].t > T('sequence_ms')) recent.shift();
    recent.push({ token, t: now, lit: lit || [], pill: pill || null });
    if (recent.length > T('sequence_max')) recent.shift();
  }
  function matchSequence(token, layers, cmdActive) {
    const now = Date.now();
    const fresh = recent.filter(r => now - r.t <= T('sequence_ms'));
    for (let n = Math.min(fresh.length, T('sequence_max') - 1); n >= 1; n--) {
      const parts = fresh.slice(fresh.length - n);
      const seq = parts.map(p => p.token).join("") + token;
      const r = resolveOnStack(seq, layers, cmdActive);
      if (!r) continue;
      // The keys lit so far were the macro's steps (or a shorter legend that matched first, like
      // "()" inside "();"): unlight them, pill included, and light the longer match.
      for (const p of parts) unlight(p);
      flash(r.hit, r.hit.length > 1 ? "combo" : null);
      let pill = null;
      if (r.hit.length > 1) { const c = comboFor(r.layer, seq); if (c) pill = showCombo(r.hit, c.key); }
      setInferred(false);
      // Keep the tokens: a still longer legend may follow (";" after "()", "⏎" after "do {").
      remember(token, r.hit, pill);
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
    if (state.posAt && Date.now() - state.posAt < T('positions_fresh_ms')) return;
    const token = tokenFor(ev);
    if (!token) return;

    const layers = stack();
    const cmdActive = commandLayersActive(layers);

    // Macros type several keys back to back (-> is "-" then ">"): when the last few tokens
    // together spell a legend on the live stack, that key or combo is what was pressed.
    if (matchSequence(token, layers, cmdActive)) return;

    // Live: the keyboard told us the stack; the key must be on it (combos included). A chord
    // (⌘c, ⌃⇧a) first tries the legend spelled with its modifier glyphs.
    if (state.live) {
      const f = ev.flags || {};
      const modsGlyph = ["cmd", "ctrl", "alt", "shift"].filter(m => f[m] && (m !== "shift" || f.cmd || f.ctrl || f.alt)).map(m => MOD_GLYPH[m]).join("");
      const r = (modsGlyph && resolveOnStack(modsGlyph + token, layers, cmdActive)) || resolveOnStack(token, layers, cmdActive);
      if (r) {
        const extra = r.viaShift ? activatorsOf(r.layer) : [];
        flash(r.hit.concat(extra), r.hit.length > 1 ? "combo" : null);
        if (r.hit.length > 1) { const c = comboFor(r.layer, token); if (c) showCombo(r.hit, c.key); }
        remember(token, r.hit);
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

    const inferredCls = state.live ? "inferred" : "";
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
        remember(token, [direct]);
        afterKey();
        return;
      }
    }
    // 1. the active stack, top first
    const r = resolveOnStack(token, layers, cmdActive);
    if (r) {
      const extra = r.viaShift ? activatorsOf(r.layer) : [];
      flash(r.hit.concat(extra), [r.hit.length > 1 ? "combo" : "", inferredCls].join(" ").trim() || null);
      if (r.hit.length > 1) { const c = comboFor(r.layer, token); if (c) showCombo(r.hit, c.key); }
      remember(token, r.hit);
      touchLayer(r.layer);
      afterKey();
      return;
    }
    // 2. any other layer → a momentary or one-shot activation the host could not see. Letters
    //    most likely came from a sticky layer; anything else from a held one.
    const search = ex.search || state.data.layer_order.filter(l => l !== base());
    const order = isLetter(token)
      ? search
      : search.filter(l => !sticky.has(l)).concat(search.filter(l => sticky.has(l)));
    for (const layer of order) {
      if (layers.includes(layer)) continue;
      const hit = findOnLayer(layer, token, cmdActive);
      if (hit) {
        if (sticky.has(layer)) state.oneShot = layer; else armMomentary(layer);
        render();
        flash(hit.concat(activatorsOf(layer)), [hit.length > 1 ? "combo" : "", inferredCls].join(" ").trim() || null);
        if (hit.length > 1) { const c = comboFor(layer, token); if (c) showCombo(hit, c.key); }
        remember(token, hit);
        afterKey();
        return;
      }
    }
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

  const hud = {
    // The keymap message. Re-sent by the host when the drawer file changes: geometry and legends
    // are rebuilt, the live layer set and the daemon code are kept.
    load(data) {
      if (typeof data === "string") data = JSON.parse(data);
      if (!data || !data.layout || !data.layers) return;
      state.data = data;
      state.momentary = []; state.oneShot = null;
      state.baseLayers = [data.base];
      document.documentElement.style.setProperty("--panel-alpha", String(T("opacity") / 100));
      buildBoard();
      const t = $("title");
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
      clearTimeout(state.layersTimer);
      // A one-shot layer leaves right after the key it served (and may enter another, undrawn
      // one). Keep the board on the drawn layers while the key's flash is visible, otherwise the
      // flash appears under the wrong legends. Layers that only appear apply at once.
      const since = Date.now() - (state.lastKeyAt || 0);
      if (state.live && since < T('press_ms')) {
        const drawn = set => new Set(set.map(id => (zl(id) || {}).drawer).filter(Boolean));
        const before = drawn(state.live.ids), after = drawn(ids);
        const losesDrawn = [...before].some(name => !after.has(name));
        if (losesDrawn) {
          state.layersTimer = setTimeout(() => hud.setLayers(ids), T('press_ms') - since);
          return;
        }
      }
      // Which key brought each new drawn layer in: the position pressed just before (its own
      // activator among the candidates), so an alternate activator elsewhere stays dark.
      const now = Date.now();
      const wasDrawn = state.live ? new Set(state.live.ids.map(id => (zl(id) || {}).drawer).filter(Boolean)) : new Set();
      for (const id of ids) {
        const name = (zl(id) || {}).drawer;
        if (!name || wasDrawn.has(name) || state.activatorOf[name] !== undefined) continue;
        // Prefer a key the drawer marks as reaching this layer; else the key pressed right
        // before the layer appeared is the one holding it (a thumb whose legend says otherwise).
        const candidates = activatorsOf(name);
        const fresh = [...recentPos].reverse().filter(p => now - p.t < T('activator_ms'));
        const press = fresh.find(p => candidates.includes(p.idx)) || fresh[0];
        if (press) state.activatorOf[name] = press.idx;
      }
      const drawnNow = new Set(ids.map(id => (zl(id) || {}).drawer).filter(Boolean));
      for (const name of Object.keys(state.activatorOf)) if (!drawnNow.has(name)) delete state.activatorOf[name];
      state.live = { ids, at: now };
      state.momentary = []; state.oneShot = null; state.inferred = false;
      render();
    },
    // Firmware `positions;`: the physical key at ZMK position `pos` was pressed. The one exact
    // source for what to light, whatever the key produced (chords, combos, macros, modifiers,
    // layer keys). Two or more positions within a combo term that form a combo on the live
    // stack draw that combo's pill.
    pressAt(pos) {
      if (!state.data) return;
      const map = state.data.positions || {};
      const idx = map[String(pos)] !== undefined ? map[String(pos)] : Number(pos);
      const now = Date.now();
      state.posAt = now; state.lastKeyAt = now;
      if (!state.keyEls[idx]) return;
      flash([idx]);
      // Stay lit until the release arrives (a safety timeout covers a lost report).
      clearTimeout(state.timers.get(idx));
      state.timers.set(idx, setTimeout(() => hud.releaseAt(pos), T('held_timeout_ms')));
      // The keyboard's own combo term (config combo_term_ms) plus slack for the reports' travel.
      const term = ((state.data.combo_term || 50) + T('combo_slack_ms'));
      // Older presses stay in the list for the activator lookup (setLayers); the combo group is
      // the trailing run of presses that started within the term of this one.
      while (recentPos.length && now - recentPos[0].t > T('activator_ms')) recentPos.shift();
      recentPos.push({ idx, t: now });
      let start = recentPos.length - 1;
      while (start > 0 && now - recentPos[start - 1].t <= term) start--;
      if (start === recentPos.length - 1) state.comboShown = null; // a new group begins
      // Only presses within the keymap's combo term form a combo: ZMK's combo module releases the
      // captured positions together when a combo completes, so they arrive within the term. A key
      // pressed later while a layer is held is that layer's key, never a combo with the holder.
      const pressedSet = recentPos.slice(start).map(p => p.idx);
      if (pressedSet.length > 1) {
        // The topmost active layer that defines a combo on these keys wins: the base layer is
        // always in the stack and often has a different combo on the same keys.
        const samePositions = c => c.positions.length === pressedSet.length && c.positions.every(p => pressedSet.includes(p));
        let combo = null;
        for (const layer of stack()) {
          combo = state.data.combos.find(c => c.layers.includes(layer) && samePositions(c));
          if (combo) break;
        }
        if (!combo) {
          // The drawer may file a held-key combo under the layer it produces rather than the one
          // it is pressed on (thumb + key = "5" drawn on numbers): accept it when unambiguous.
          const any = state.data.combos.filter(samePositions);
          if (any.length === 1) combo = any[0];
        }
        if (combo) {
          // A third key within the term makes a bigger combo: take the smaller one's pill down.
          if (state.comboShown) state.comboShown.remove();
          flash(combo.positions, "combo");
          state.comboShown = showCombo(combo.positions, combo.key);
        }
      }
    },
    // Leave live mode (tests, or a host that lost the keyboard).
    clearLayers() { state.live = null; render(); },
    // The keyboard that was opened (its HID product name): the default title.
    // The key at ZMK position `pos` went up: the flash fades out from now.
    releaseAt(pos) {
      if (!state.data) return;
      const map = state.data.positions || {};
      const idx = map[String(pos)] !== undefined ? map[String(pos)] : Number(pos);
      const e = state.keyEls[idx];
      if (!e) return;
      clearTimeout(state.timers.get(idx));
      state.timers.set(idx, setTimeout(() => e.classList.remove("pressed", "combo", "inferred"), T('release_ms')));
    },
    setDevice(name) { if ((name || "") !== state.device) { state.device = name || ""; renderTitle(); } },
    key(ev) {
      if (typeof ev === "string") ev = JSON.parse(ev);
      handleKey(ev);
      if (window.keys) window.keys.key(ev);  // the typed-keys strip on the same page
    },
    press(indices) { flash(indices); },
    state,
  };
  window.hud = hud;

  // ✕: tell the host to close. Hammerspoon listens on a user-content controller; a
  // WebSocket host receives {"kind":"close"}.
  const closeBtn = $("close");
  if (closeBtn) closeBtn.addEventListener("click", () => {
    try { window.webkit.messageHandlers.zmkhud.postMessage("close"); } catch (e) { /* not WebKit */ }
    if (hud.socket && hud.socket.readyState === 1) hud.socket.send(JSON.stringify({ kind: "close" }));
  });

  // Rebuild the geometry when the panel is resized (zoom, moveTo another screen).
  window.addEventListener("resize", () => { if (state.data) { buildBoard(); render(); postSize(); } });

  // Generic host: index.html?ws=ws://127.0.0.1:8766 — messages are
  //   {"kind":"keymap",…}  {"kind":"key", ...event}  {"kind":"layers","ids":[…]}   (host/hudfeed.py speaks this).
  const params = new URLSearchParams(location.search);
  const wsUrl = params.get("ws");
  if (wsUrl) {
    const connect = () => {
      const s = new WebSocket(wsUrl);
      hud.socket = s;
      s.onmessage = e => {
        const m = JSON.parse(e.data);
        if (m.kind === "keymap") hud.load(m);
        else if (m.kind === "key") hud.key(m);
        else if (m.kind === "layers") hud.setLayers(m.ids);
        else if (m.kind === "device") hud.setDevice(m.name);
        else if (m.kind === "press") hud.pressAt(m.pos);
        else if (m.kind === "release") hud.releaseAt(m.pos);
        if (m.device) hud.setDevice(m.device);  // the keyboard that is typing names the panel
      };
      s.onclose = () => setTimeout(connect, 1000);
    };
    connect();
  }

  // Dev: index.html?keymap=keymap.json (python3 host/keymap.py --dump > hud/keymap.json) and real
  // key events, so the page can be exercised in a browser without a host.
  if (params.get("keymap")) fetch(params.get("keymap")).then(r => r.json()).then(hud.load).catch(e => console.error(e));
  if (location.protocol.startsWith("http")) window.addEventListener("keydown", e => {
    const name = e.key.length === 1 ? null : e.key.toLowerCase().replace("arrow", "").replace("backspace", "delete").replace("enter", "return");
    hud.key({ type: "keyDown", chars: e.key.length === 1 ? e.key : "", name, flags: {} });
    if (e.key !== "F5" && !e.metaKey) e.preventDefault();
  });
  render();
})();
