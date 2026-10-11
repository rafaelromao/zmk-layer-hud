/* zmk-layer-hud — a browser-shaped sandbox for the tests.
 *
 * hud.js and keys.js ship as plain <script> files, so the only way to test what the page actually
 * draws is to give them a page. This is that page: the smallest DOM the two files touch, plus a
 * clock the test drives by hand, run through node:vm. Neither file is modified, built or imported
 * — the bytes under test are the bytes the panel loads.
 *
 * The tail of hud.js is inert here on purpose: location.search is empty, so `?keymap=` never
 * fetches (the loader at the end of hud.js), and location.protocol is not http, so no keydown listener is installed
 * (the one after it). What is left is window.hud and window.keys, which is all the tests want. A
 * test that wants the listener asks for `protocol: "http:"`; window listeners are recorded, and
 * the page's `dispatch(type, event)` calls them.
 *
 * Geometry is real, not stubbed: getBoundingClientRect() reports back the px that hud.js itself
 * wrote into element.style, so showCombo's midpoint arithmetic runs for real.
 * Layout that a browser would compute and nothing asserts — font fitting, clientWidth — is a
 * constant.
 */
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const HUD_DIR = path.join(__dirname, "..");
const BOARD_WIDTH = 570;            // hud.js's own fallback when a board has no layout width yet

// ---------- the clock ----------

/* Virtual time. hud.js schedules every teardown it owns (a key unlighting after press_ms, the
 * combo pill after combo_pill_ms, a one-shot layer after one_shot_ms) and reads Date.now() to
 * decide what counts as one combo, so a test that cannot move time cannot see any of it. Callbacks
 * fire in due order, ties in insertion order, which is what a browser does.
 *
 * Note this is the opposite of what demoFrame (hud.js) does for the GIF: it replaces
 * setTimeout with a no-op so nothing ever tears down. That is right for a screenshot and useless
 * here — a test has to watch the key go out.
 */
class Clock {
  constructor(start = 1_000_000) {
    this.now = start;
    this.seq = 0;
    this.timers = new Map();
  }
  setTimeout(fn, ms) {
    const id = ++this.seq;
    this.timers.set(id, { fn, at: this.now + (Number(ms) || 0), seq: id });
    return id;
  }
  clearTimeout(id) { this.timers.delete(id); }
  /* Again and again until cleared: touch.js says its keys again every half second while held. */
  setInterval(fn, ms) {
    const id = ++this.seq;
    const every = Math.max(1, Number(ms) || 0);
    const run = () => { this.timers.set(id, { fn: run, at: this.now + every, seq: id }); fn(); };
    this.timers.set(id, { fn: run, at: this.now + every, seq: id });
    return id;
  }
  /* Inline, not queued. Both files use rAF only to add the `show` class that fades a pill or a
   * chip in, so running it at once makes "it is on screen" observable without advancing time —
   * and advancing time is how the tests check teardown, which would otherwise be conflated. */
  requestAnimationFrame(fn) { fn(); return ++this.seq; }

  /* Move to now+ms, firing everything due on the way. Timers set by a callback are honoured, so a
   * chain of teardowns unwinds the way it would in a page. */
  advance(ms) {
    const until = this.now + (Number(ms) || 0);
    for (;;) {
      let next = null;
      for (const t of this.timers.values()) {
        if (t.at <= until && (!next || t.at < next.at || (t.at === next.at && t.seq < next.seq))) next = t;
      }
      if (!next) break;
      this.timers.delete(next.seq);
      this.now = Math.max(this.now, next.at);
      next.fn();
    }
    this.now = until;
  }
  /* Nothing still scheduled. A test resets by advancing past every timeout the page owns, so a
   * non-zero count here means the page leaked one — cheap to assert, and it catches a stuck key
   * or a pill that never comes down. */
  pending() { return this.timers.size; }
}

// ---------- the DOM ----------

const SIMPLE = /^([.#]?)([A-Za-z0-9_-]+)$/;

class El {
  constructor(tag, ns) {
    this.tagName = String(tag).toUpperCase();
    this.namespaceURI = ns || null;
    this.parentNode = null;
    this.childNodes = [];
    this.attributes = new Map();
    this.title = "";
    this.dataset = {};
    this._text = "";
    this._html = "";
    const classes = new Set();
    this.classList = {
      add: (...names) => { for (const n of names) if (n) classes.add(n); },
      remove: (...names) => { for (const n of names) classes.delete(n); },
      contains: n => classes.has(n),
      toggle: (n, on) => { if (on === undefined ? classes.has(n) : !on) classes.delete(n); else classes.add(n); },
      get length() { return classes.size; },
      values: () => [...classes],
    };
    this._classes = classes;
    this.style = makeStyle();
  }
  get className() { return [...this._classes].join(" "); }
  set className(v) {
    this._classes.clear();
    for (const n of String(v || "").split(/\s+/)) if (n) this._classes.add(n);
  }
  get children() { return this.childNodes; }
  get firstChild() { return this.childNodes[0] || null; }

  // hud.js writes a legend either as text or as a glyph's markup; both are read back in the tests.
  get textContent() { return this._text; }
  set textContent(v) { this._text = v == null ? "" : String(v); this._html = ""; this.childNodes = []; }
  get innerHTML() { return this._html; }
  /* The markup is stored, not parsed — nothing here needs to walk inside a glyph's SVG. What does
   * matter is that an element holding markup reports children, the way a browser's would: that is
   * how a caller tells "this legend is a glyph" from "this legend is text", and getting it wrong
   * made the two runners disagree about every legend containing < or &. */
  set innerHTML(v) {
    this._html = v == null ? "" : String(v);
    this._text = "";
    this.childNodes = this._html ? [new El("#raw")] : [];
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    if (name === "class") this.className = value;    // the combo links are built this way (hud.js showCombo)
  }
  getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
  removeAttribute(name) { this.attributes.delete(name); }

  appendChild(child) {
    if (child.parentNode) child.parentNode.removeChild(child);
    child.parentNode = this;
    this.childNodes.push(child);
    return child;
  }
  removeChild(child) {
    const i = this.childNodes.indexOf(child);
    if (i >= 0) this.childNodes.splice(i, 1);
    child.parentNode = null;
    return child;
  }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  addEventListener(type, fn) {
    if (!this.listeners) this.listeners = new Map();
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(fn);
  }
  removeEventListener(type, fn) {
    const fns = (this.listeners && this.listeners.get(type)) || [];
    if (fns.indexOf(fn) >= 0) fns.splice(fns.indexOf(fn), 1);
  }
  // What a press on a button does: its click listeners, in the order added. The event is returned,
  // so a test can tell whether a link's own action was kept from happening.
  click() { return this.fire("click"); }
  /* An event of any type on this element, its listeners called in the order added -- then its
   * parents', as a bubbling event would reach them (touch.js listens on the board for the keys'
   * pointer events). `props` is what the event carries (pointerId, clientX, ...). */
  fire(type, props) {
    const ev = Object.assign({ type, target: this, defaultPrevented: false, button: 0 }, props || {});
    ev.preventDefault = () => { ev.defaultPrevented = true; };
    for (let node = this; node; node = node.parentNode) {
      for (const fn of ((node.listeners && node.listeners.get(type)) || []).slice()) fn(ev);
    }
    return ev;
  }
  closest(sel) {
    for (let node = this; node; node = node.parentNode) if (node.matches && node.matches(sel)) return node;
    return null;
  }
  setPointerCapture() {}
  releasePointerCapture() {}

  matches(sel) {
    const m = SIMPLE.exec(sel.trim());
    if (!m) return false;
    const [, kind, name] = m;
    if (kind === ".") return this._classes.has(name);
    if (kind === "#") return this.attributes.get("id") === name;
    return this.tagName === name.toUpperCase();
  }
  // Only what hud.js and keys.js ask for: a single class or id, and comma-separated groups whose
  // last simple selector decides (".combo-pill, #keys .chip" in hud.js demoFrame).
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  querySelectorAll(sel) {
    const wanted = String(sel).split(",").map(s => s.trim().split(/\s+/).pop()).filter(Boolean);
    const out = [];
    const walk = node => {
      for (const c of node.childNodes) {
        if (wanted.some(w => c.matches(w))) out.push(c);
        walk(c);
      }
    };
    walk(this);
    out.forEach = Array.prototype.forEach.bind(out);
    return out;
  }

  /* The box hud.js gave this element. buildBoard writes every key's left/top/width/height in px
   * (hud.js buildBoard) and the board's own height, so the numbers a real browser would lay out are
   * already here — showCombo's centres come out right instead of being faked. */
  getBoundingClientRect() {
    const px = v => { const n = parseFloat(v); return Number.isFinite(n) ? n : 0; };
    let left = px(this.style.left), top = px(this.style.top);
    for (let p = this.parentNode; p; p = p.parentNode) { left += px(p.style.left); top += px(p.style.top); }
    const width = this.style.width ? px(this.style.width) : (this === this.ownerBoard ? BOARD_WIDTH : 0);
    const height = px(this.style.height);
    return { left, top, width, height, right: left + width, bottom: top + height, x: left, y: top };
  }
  get clientWidth() {
    const n = parseFloat(this.style.width);
    return Number.isFinite(n) ? n : BOARD_WIDTH;
  }

  /* A browser lays a combo pill out from what it holds; this has no layout, so it gives the pill
   * the box the stylesheet would (hud.css .combo-pill): at least 44px of content, about 13px per
   * character of the 22px legend, 8px of padding a side and a 2px border; an 8px label over the
   * legend, the sub-line's 11px when there is one, 4px of padding top and bottom. hud.js finds the
   * pill's spot from this and the sweep checks that spot, so here the two agree by construction;
   * the browser runner is what holds the estimate to a real layout. Nothing else has a box. */
  get offsetWidth() {
    if (!this._classes.has("combo-pill")) return undefined;
    const tap = this.childNodes.find(c => c._classes && c._classes.has("combo-tap"));
    const chars = tap ? (tap._text || (tap._html ? "##" : "")).length : 0;   // a glyph: two characters' width
    return Math.max(44, 13 * chars) + 2 * 8 + 2 * 2;
  }
  get offsetHeight() {
    if (!this._classes.has("combo-pill")) return undefined;
    const sub = this.childNodes.some(c => c._classes && c._classes.has("combo-sub"));
    return 8 + 1 + 22 + (sub ? 2 + 9 : 0) + 2 * 4 + 2 * 2;
  }
}

function makeStyle() {
  const style = {};
  Object.defineProperty(style, "setProperty", {
    value: (name, value) => { style[name] = value == null ? "" : String(value); },
    enumerable: false,
  });
  Object.defineProperty(style, "getPropertyValue", {
    value: name => (style[name] === undefined ? "" : style[name]),
    enumerable: false,
  });
  return style;
}

// The ids index.html defines. Everything hud.js looks up with $() has to be here, or it silently
// renders into nothing and every assertion passes for the wrong reason.
const PAGE_IDS = ["frame", "hud", "layer", "layerName", "layerSub", "side", "hide", "close", "board", "stats", "statsHead", "status", "feed", "title", "keys"];
// ...each with the attributes index.html gives it: the heading's href, which ?embed takes away.
const PAGE_HTML = fs.readFileSync(path.join(HUD_DIR, "index.html"), "utf8");
const SCRIPTS = [...PAGE_HTML.matchAll(/<script src="([^"]+)"/g)].map(m => m[1]);
function pageAttributes(id) {
  const tag = new RegExp(`<\\w+\\s[^>]*\\bid="${id}"[^>]*>`).exec(PAGE_HTML);
  return tag ? [...tag[0].matchAll(/([\w-]+)="([^"]*)"/g)].map(m => [m[1], m[2]]) : [["id", id]];
}

function makeDocument() {
  const doc = {};
  const byId = new Map();
  const make = (tag, ns) => { const e = new El(tag, ns); e.ownerDocument = doc; return e; };

  doc.createElement = tag => make(tag);
  doc.createElementNS = (ns, tag) => make(tag, ns);
  doc.documentElement = make("html");
  doc.head = make("head");
  doc.body = make("body");
  doc.documentElement.appendChild(doc.head);
  doc.documentElement.appendChild(doc.body);
  doc.body.scrollHeight = 0;          // postSize reads it outside its own try/catch (hud.js postSize)
  for (const id of PAGE_IDS) {
    const e = make("div");
    for (const [name, value] of pageAttributes(id)) e.setAttribute(name, value);
    doc.body.appendChild(e);
    byId.set(id, e);
  }
  doc.getElementById = id => byId.get(id) || null;
  doc.querySelector = sel => doc.body.querySelector(sel);
  doc.querySelectorAll = sel => doc.body.querySelectorAll(sel);
  // The document's own listeners (touch.js: visibilitychange, gesturestart), fired by a test.
  const docListeners = new Map();
  doc.addEventListener = (type, fn) => {
    if (!docListeners.has(type)) docListeners.set(type, []);
    docListeners.get(type).push(fn);
  };
  doc.fire = (type, props) => {
    const ev = Object.assign({ type, defaultPrevented: false }, props || {});
    ev.preventDefault = () => { ev.defaultPrevented = true; };
    for (const fn of (docListeners.get(type) || []).slice()) fn(ev);
    return ev;
  };
  doc.hidden = false;
  return doc;
}

// ---------- the page ----------

/* Load the page's scripts into one sandbox, exactly as index.html loads them (keys.js first, so
 * window.keys exists by the time hud.key forwards to it; the touch keyboard's after hud.js).
 * Returns the handles a test needs.
 *
 * `search` is the page's query string, for what hud.js decides from it at load (a GIF still's
 * &demo=N, ?embed). A query that names a keymap makes the page fetch it; `fetch` here never settles,
 * so the page stays exactly as the test loads it rather than racing a second load in, and what it
 * asked for is in `fetched`; a `?ws=` opens a WebSocket, which here only records its URL in
 * `sockets`. `protocol` is "file:" unless given: "http:" is a page in a browser, which installs the
 * dev keydown listener. `hostname` is "" for file: and "localhost" otherwise, unless given: a
 * published copy on some other host honours no URL parameter. `remote` loads the page as a tablet
 * gets it from host/remote.py, at `host` with the link's `hash`, its socket the test's `WebSocket`;
 * what it keeps is in `stored`, and the addresses it put in the bar in `replaced`. */
function loadPage(opts = {}) {
  const clock = new Clock();
  const document = makeDocument();
  const board = document.getElementById("board");
  board.ownerBoard = board;
  const protocol = opts.protocol || "file:";
  const hostname = opts.hostname !== undefined ? opts.hostname : (protocol === "file:" ? "" : "localhost");
  const listeners = new Map();   // window's, by event type
  const fetched = [], sockets = [], replaced = [];
  // The remote page (host/remote.py): index.html marked remote, with remote.js after the rest.
  if (opts.remote) document.documentElement.classList.add("remote");
  const stored = new Map();

  const sandbox = {
    document,
    location: { search: opts.search || "", protocol, hostname, hash: opts.hash || "", pathname: "/",
                host: opts.host || hostname,
                href: (protocol === "file:" ? "file:///" : protocol + "//" + hostname + "/") + "hud/index.html" + (opts.search || "") },
    fetch: url => { fetched.push(String(url)); return new Promise(() => {}); },
    WebSocket: opts.WebSocket || class { constructor(url) { sockets.push(String(url)); } },
    localStorage: { getItem: (k) => (stored.has(k) ? stored.get(k) : null), setItem: (k, v) => stored.set(k, String(v)) },
    history: { replaceState: (s, t, url) => { replaced.push(url); sandbox.location.hash = ""; } },
    crypto: { getRandomValues: (a) => require("node:crypto").webcrypto.getRandomValues(a) },
    // A test's own navigator.vibrate and AudioContext, for the touch keyboard's feedback.
    navigator: Object.assign({ userAgent: "node" }, opts.navigator || {}),
    AudioContext: opts.AudioContext,
    console: opts.quiet ? { ...console, warn: () => {} } : console,   // a test that expects the page's warning
    URLSearchParams,
    setTimeout: (fn, ms) => clock.setTimeout(fn, ms),
    clearTimeout: id => clock.clearTimeout(id),
    setInterval: (fn, ms) => clock.setInterval(fn, ms),
    clearInterval: id => clock.clearTimeout(id),
    requestAnimationFrame: fn => clock.requestAnimationFrame(fn),
    Date: makeDate(clock),
    innerHeight: opts.innerHeight || 800,
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.addEventListener = (type, fn) => {
    if (!listeners.has(type)) listeners.set(type, []);
    listeners.get(type).push(fn);
  };
  sandbox.removeEventListener = (type, fn) => {
    const fns = listeners.get(type) || [];
    if (fns.indexOf(fn) >= 0) fns.splice(fns.indexOf(fn), 1);
  };
  // What a browser does with an event on window: every listener for its type, in the order added.
  const dispatch = (type, event) => { for (const fn of (listeners.get(type) || []).slice()) fn(event); };

  vm.createContext(sandbox);
  // Every script index.html loads, in its order, as site/build.py reads them (hud_files).
  for (const file of opts.remote ? SCRIPTS.concat(["remote.js"]) : SCRIPTS) {
    const src = fs.readFileSync(path.join(HUD_DIR, file), "utf8");
    vm.runInContext(src, sandbox, { filename: path.join(HUD_DIR, file) });
  }
  return { hud: sandbox.hud, keys: sandbox.keys, document, board, clock, window: sandbox, dispatch, fetched, sockets,
           stored, replaced };
}

// Date with a movable now(). hud.js only ever calls Date.now(), but keep the rest of the class
// working so nothing else surprises us.
function makeDate(clock) {
  const Fake = class extends Date {
    constructor(...args) { if (args.length === 0) super(clock.now); else super(...args); }
    static now() { return clock.now; }
  };
  return Fake;
}

module.exports = { loadPage, Clock, El, BOARD_WIDTH };
