#!/usr/bin/env node
/* zmk-layer-hud — the remote page (hud/remote.js): its SHA-256 and HMAC against node's own, the
 * protocol's texts against host/remote.py's (fixtures/remote_vectors.json), and a tablet's visit on
 * the tests' clock -- the key taken from the link, the pairing, a tap MAC'd on its way, and what
 * the page says when the socket goes or the computer is not the one it was paired with.
 *
 *   node hud/tests/remote_test.js [--verbose]
 */
"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const R = require("../remote.js");
const { loadPage } = require("./dom.js");

const VERBOSE = process.argv.includes("--verbose");
const fail = [];
let checks = 0;
function check(ok, what) { checks++; if (!ok) fail.push(what); else if (VERBOSE) console.log("ok   " + what); }
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const V = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", "remote_vectors.json"), "utf8"));
const KEY = R.keyOf(V.key);

// ---------- the hashes ----------

(function sha256AndHmacAreNodes() {
  let bad = [];
  for (let n = 0; n <= 200; n++) {
    const data = crypto.randomBytes(n);
    if (R.hex(R.sha256(new Uint8Array(data))) !== crypto.createHash("sha256").update(data).digest("hex")) bad.push(`sha256 of ${n}`);
  }
  for (const klen of [0, 1, 20, 32, 63, 64, 65, 131]) {
    for (const dlen of [0, 1, 50, 55, 56, 64, 119, 120, 200]) {
      const key = crypto.randomBytes(klen), data = crypto.randomBytes(dlen);
      if (R.hex(R.hmac(new Uint8Array(key), new Uint8Array(data))) !== crypto.createHmac("sha256", key).update(data).digest("hex")) {
        bad.push(`hmac ${klen}/${dlen}`);
      }
    }
  }
  check(!bad.length, `SHA-256 and HMAC-SHA256 agree with node's at every length and block edge: ${bad.join(", ")}`);
  check(R.hex(R.utf8("ç✓")) === Buffer.from("ç✓", "utf8").toString("hex"), "text is hashed as UTF-8");
})();

// ---------- the protocol ----------

(function theTextsAreHostRemotePys() {
  check(eq(Array.from(KEY), Array.from({ length: 32 }, (_, i) => i)), "the link's key is 32 bytes");
  check([V.key.slice(1), V.key + "A", V.key.slice(0, -1) + "+", null].every((t) => R.keyOf(t) === null), "anything else is no key");
  check(R.kid(KEY) === V.kid, "the key's id");
  check(R.authText(V.nonce, V.cnonce) === V.auth_text && R.mac(KEY, V.auth_text) === V.auth_mac, "the pairing's proof");
  const sk = R.sessionKey(KEY, V.nonce, V.cnonce);
  check(R.hex(sk) === V.session_key, "the connection's key");
  check(R.mac(sk, R.WELCOME) === V.welcome_mac, "the server's proof back");
  for (const r of V.reports) {
    check(R.hidText(r) === r.text && R.mac(sk, r.text) === r.mac, `report ${r.seq} MAC'd as the server checks it`);
  }
})();

// ---------- a tablet's visit ----------

const K = (tap) => ({ tap, hold: "", shifted: "", type: "key" });
const kp = (id) => ({ op: "kp", key: { page: 7, id, mods: 0 } });
const MSG = {
  kind: "keymap", schema: 1, source: "remote-test", title: "", stats: {}, fingers: null,
  combo_term: 50, combo_idle: 0, altgr: false, glyphs: {}, positions: { 0: 0, 1: 1 },
  layout: { width: 200, height: 100, keys: [0, 1].map((i) => ({ x: 50 + 100 * i, y: 50, w: 100, h: 100, r: 0 })) },
  layers: { base: [K("a"), K("b")] }, layer_order: ["base"], combos: [], activators: [],
  zmk_layers: { 0: { id: 0, name: "BASE", drawer: "base", label: "Base", cls: "off" } },
  base: "base", extras: { base: "base", sticky: [], search: [] },
  actions: { v: 1, source: "keymap", positions: 2, default: 0, table: [kp(0x04), kp(0x05)],
             layers: { 0: [0, 1] }, combos: [], conditional: [] },
  touch: { enabled: 1, floating: 0, hold_ms: 280, flavor: "balanced", latch: 1, lock_ms: 350, sticky_ms: 0,
           repeat_delay_ms: 450, repeat_ms: 60, combos: "chips", heat: 1, strip: 1 },
};

// The computer's end of the socket: what the page sends, and the server's messages to it.
function visit(opts) {
  const sockets = [];
  class Socket {
    constructor(url) { this.url = url; this.sent = []; this.readyState = 1; sockets.push(this); }
    send(data) { this.sent.push(JSON.parse(data)); }
    close() { if (this.readyState === 3) return; this.readyState = 3; this.onclose({ code: 1000 }); }
    say(m) { this.onmessage({ data: JSON.stringify(m) }); }
    drop(code) { this.readyState = 3; this.onclose({ code }); }
  }
  const p = loadPage(Object.assign({ remote: true, protocol: "http:", hostname: "192.168.1.5", host: "192.168.1.5:8768",
                                     WebSocket: Socket }, opts));
  p.socketsOpen = sockets;
  p.feed = () => p.document.getElementById("feed").textContent;
  p.note = () => (p.hud.state.touch && p.hud.state.touch.note) || "";
  // The server's half of the pairing, with the key it holds.
  p.pair = (key) => {
    const ws = sockets[sockets.length - 1];
    const nonce = "ab".repeat(16);
    ws.say({ kind: "challenge", v: 1, kid: R.kid(key || KEY), nonce });
    const auth = ws.sent.find((m) => m.kind === "auth");
    if (!auth) return { ws };
    const sk = R.sessionKey(key || KEY, nonce, auth.cnonce);
    ws.say({ kind: "welcome", v: 1, mac: R.mac(sk, R.WELCOME) });
    return { ws, auth, nonce, sk };
  };
  return p;
}

(function theKeyIsTakenFromTheLinkAndKept() {
  const p = visit({ hash: "#k=" + V.key });
  check(p.stored.get(R.STORE) === V.key, "the link's key is kept for the next visit");
  check(eq(p.replaced, ["/"]), `and taken out of the address bar: ${JSON.stringify(p.replaced)}`);
  check(p.socketsOpen.length === 1 && p.socketsOpen[0].url === "ws://192.168.1.5:8768/ws", "the socket is the page's own address's");
  check(p.document.documentElement.classList.contains("dock"), "the board is docked from the start");
})();

(function aPairingThenATapMacd() {
  const p = visit({ hash: "#k=" + V.key });
  const { ws, auth, nonce, sk } = p.pair();
  check(auth && /^[0-9a-f]{32}$/.test(auth.cnonce) && auth.mac === R.mac(KEY, R.authText(nonce, auth.cnonce)),
        "the page proves the key, and sends only the proof");
  check(!JSON.stringify(ws.sent).includes(V.key), "the key itself never goes on the socket");
  check(p.document.documentElement.classList.contains("touch"), "paired, the board types");
  ws.say(MSG);
  check(p.hud.state.keyEls.length === 2, "the keymap comes on the socket once paired");
  p.hud.state.keyEls[0].fire("pointerdown", { pointerId: 1 });
  p.clock.advance(40);
  p.hud.state.keyEls[0].fire("pointerup", { pointerId: 1 });
  const hid = ws.sent.filter((m) => m.kind === "hid");
  check(eq(hid.map((m) => m.keys), [[4], []]), `a tap is its key down, then up: ${JSON.stringify(hid)}`);
  check(hid.every((m) => m.mac === R.mac(sk, R.hidText(m))) && hid[1].seq > hid[0].seq,
        "each report MAC'd with the connection's key, its number going up");
})();

(function nothingIsSentBeforeThePairingNorAnythingTakenFromAnUnpairedServer() {
  const p = visit({ hash: "#k=" + V.key });
  const ws = p.socketsOpen[0];
  ws.say(MSG);
  check(!p.hud.state.data, "a keymap before the pairing is not drawn");
  check(ws.sent.length === 0 && !p.document.documentElement.classList.contains("touch"), "and the board does not type");
})();

(function aServerWithAnotherKeyIsNeitherAnsweredNorBelieved() {
  const other = new Uint8Array(32).fill(7);
  const p = visit({ hash: "#k=" + V.key });
  const { ws } = p.pair(other);
  check(!ws.sent.some((m) => m.kind === "auth"), "a server holding another key is never sent a proof");
  check(/another code/.test(p.feed()), `and the page says to pair again: ${p.feed()}`);
  p.clock.advance(20000);
  check(p.socketsOpen.length === 1, "and does not try again by itself");

  const q = visit({ hash: "#k=" + V.key });
  const s = q.socketsOpen[0];
  s.say({ kind: "challenge", v: 1, kid: R.kid(KEY), nonce: "cd".repeat(16) });
  s.say({ kind: "welcome", v: 1, mac: "0".repeat(64) });
  check(!q.document.documentElement.classList.contains("touch"), "a welcome without the proof back does not type");
  check(/not the computer/.test(q.feed()), `and says so: ${q.feed()}`);
})();

(function aDroppedSocketComesBackAndSaysSoMeanwhile() {
  const p = visit({ hash: "#k=" + V.key });
  const { ws } = p.pair();
  ws.say(MSG);
  p.hud.state.keyEls[0].fire("pointerdown", { pointerId: 1 });
  ws.drop(1006);
  check(/reconnecting/.test(p.note()), `the banner says the computer is away: ${p.note()}`);
  check(!p.hud.state.keyEls[0].classList.contains("pressed"), "and lets go of the key a finger held");
  const before = ws.sent.length;
  p.hud.state.keyEls[1].fire("pointerdown", { pointerId: 2 });
  p.hud.state.keyEls[1].fire("pointerup", { pointerId: 2 });
  check(ws.sent.length === before, "a tap while it is away goes nowhere");
  p.clock.advance(1000);
  check(p.socketsOpen.length === 2, "and a new socket a second later");
  p.pair();
  check(p.note() === "", `paired again, the banner is clear: ${p.note()}`);
  p.socketsOpen[1].drop(4000);
  p.clock.advance(20000);
  check(p.socketsOpen.length === 2 && /another tablet/.test(p.note()), "another tablet taking over is not fought");
})();

(function aStoredKeyIsUsedAndNoKeyIsASentence() {
  const p = visit({});
  check(p.socketsOpen.length === 0 && /not paired/.test(p.feed()), `no key, no socket: ${p.feed()}`);
  const q = loadPage({ remote: true, protocol: "http:", hostname: "192.168.1.5", host: "192.168.1.5:8768" });
  check(q.sockets.length === 0, "(and nothing stored in a fresh page)");
})();

console.log(`the remote page: ${checks} checks, ${fail.length} failures`);
if (fail.length) {
  for (const f of fail) console.log("FAIL " + f);
  process.exit(1);
}
