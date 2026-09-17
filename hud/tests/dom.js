/* zmk-layer-hud — a browser-shaped sandbox for the tests.
 *
 * hud.js and keys.js ship as plain <script> files, so the only way to test what the page actually
 * draws is to give them a page. This is that page: the smallest DOM the two files touch, plus a
 * clock the test drives by hand, run through node:vm. Neither file is modified, built or imported
 * — the bytes under test are the bytes the panel loads.
 *
 * The tail of hud.js is inert here on purpose: location.search is empty, so `?keymap=` never
 * fetches (hud.js:809), and location.protocol is not http, so no keydown listener is installed
 * (hud.js:817). What is left is window.hud and window.keys, which is all the tests want.
 *
 * Geometry is real, not stubbed: getBoundingClientRect() reports back the px that hud.js itself
 * wrote into element.style, so showCombo's midpoint arithmetic (hud.js:301) runs for real.
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
 * Note this is the opposite of what demoFrame (hud.js:771) does for the GIF: it replaces
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
  set innerHTML(v) { this._html = v == null ? "" : String(v); this._text = ""; this.childNodes = []; }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    if (name === "class") this.className = value;    // the combo links are built this way (hud.js:310)
  }
  getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }

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
  addEventListener() {}
  removeEventListener() {}

  matches(sel) {
    const m = SIMPLE.exec(sel.trim());
    if (!m) return false;
    const [, kind, name] = m;
    if (kind === ".") return this._classes.has(name);
    if (kind === "#") return this.attributes.get("id") === name;
    return this.tagName === name.toUpperCase();
  }
  // Only what hud.js and keys.js ask for: a single class or id, and comma-separated groups whose
  // last simple selector decides (".combo-pill, #keys .chip" at hud.js:806).
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
   * (hud.js:95-98) and the board's own height, so the numbers a real browser would lay out are
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
const PAGE_IDS = ["hud", "layer", "layerName", "layerSub", "close", "board", "status", "feed", "title", "keys"];

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
  doc.body.scrollHeight = 0;          // postSize reads it outside its own try/catch (hud.js:290)
  for (const id of PAGE_IDS) {
    const e = make("div");
    e.attributes.set("id", id);
    doc.body.appendChild(e);
    byId.set(id, e);
  }
  doc.getElementById = id => byId.get(id) || null;
  doc.querySelector = sel => doc.body.querySelector(sel);
  doc.querySelectorAll = sel => doc.body.querySelectorAll(sel);
  doc.addEventListener = () => {};
  return doc;
}

// ---------- the page ----------

/* Load hud.js and keys.js into one sandbox, exactly as index.html loads them (keys.js first, so
 * window.keys exists by the time hud.key forwards to it). Returns the handles a test needs. */
function loadPage() {
  const clock = new Clock();
  const document = makeDocument();
  const board = document.getElementById("board");
  board.ownerBoard = board;

  const sandbox = {
    document,
    location: { search: "", protocol: "file:", href: "file:///hud/index.html" },
    navigator: { userAgent: "node" },
    console,
    URLSearchParams,
    setTimeout: (fn, ms) => clock.setTimeout(fn, ms),
    clearTimeout: id => clock.clearTimeout(id),
    setInterval: () => 0,
    clearInterval: () => {},
    requestAnimationFrame: fn => clock.requestAnimationFrame(fn),
    Date: makeDate(clock),
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.addEventListener = () => {};
  sandbox.removeEventListener = () => {};

  vm.createContext(sandbox);
  for (const file of ["keys.js", "hud.js"]) {
    const src = fs.readFileSync(path.join(HUD_DIR, file), "utf8");
    vm.runInContext(src, sandbox, { filename: path.join(HUD_DIR, file) });
  }
  return { hud: sandbox.hud, keys: sandbox.keys, document, board, clock, window: sandbox };
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
