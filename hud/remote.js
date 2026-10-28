/* zmk-layer-hud — the remote page: a tablet's browser types into the computer that served it
 * (host/remote.py, docs/touch.md).
 *
 * `zmk-layer-hud remote on` prints a link, http://<computer>:8768/#k=<key>. The key is in the
 * fragment, which a browser never sends; this page keeps it (localStorage) and takes it out of the
 * address bar. Over the socket it proves the key without sending it -- the server's nonce and its
 * own, MAC'd with it -- and checks the server's proof back; after that every report snapshot the
 * board sends is MAC'd with a key for that connection alone, its sequence number only going up.
 * The keymap arrives on the same socket once paired.
 *
 * HMAC-SHA256 is written here: crypto.subtle is only for secure contexts, and a page on a local
 * network's http is not one. crypto.getRandomValues is, and the nonce comes from it.
 *
 * The board is docked, as tall as the window allows, and types through hud.setTouch's sink. While
 * the socket is away the banner says so and nothing is sent; a dropped connection comes back by
 * itself.
 */
(function (root) {
  "use strict";
  const PROTOCOL = "zmkhud-remote/1";
  const WELCOME = PROTOCOL + " welcome";

  // ---------- SHA-256 and HMAC (FIPS 180-4, RFC 2104) ----------

  const K = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ];

  function sha256(bytes) {
    const n = bytes.length;
    const padded = new Uint8Array(((n + 9 + 63) >> 6) << 6);
    padded.set(bytes);
    padded[n] = 0x80;
    const bits = n * 8, end = padded.length;
    padded[end - 5] = (bits / 0x100000000) & 0xff;   // lengths past 2^32 bits never come here
    padded[end - 4] = bits >>> 24; padded[end - 3] = bits >>> 16; padded[end - 2] = bits >>> 8; padded[end - 1] = bits;
    const h = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
    const w = new Array(64);
    const rotr = (x, r) => (x >>> r) | (x << (32 - r));
    for (let off = 0; off < end; off += 64) {
      for (let i = 0; i < 16; i++) {
        const j = off + 4 * i;
        w[i] = (padded[j] << 24) | (padded[j + 1] << 16) | (padded[j + 2] << 8) | padded[j + 3];
      }
      for (let i = 16; i < 64; i++) {
        const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
        const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
      }
      let [a, b, c, d, e, f, g, hh] = h;
      for (let i = 0; i < 64; i++) {
        const t1 = (hh + (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + w[i]) | 0;
        const t2 = ((rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) | 0;
        hh = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
      }
      h[0] = (h[0] + a) | 0; h[1] = (h[1] + b) | 0; h[2] = (h[2] + c) | 0; h[3] = (h[3] + d) | 0;
      h[4] = (h[4] + e) | 0; h[5] = (h[5] + f) | 0; h[6] = (h[6] + g) | 0; h[7] = (h[7] + hh) | 0;
    }
    const out = new Uint8Array(32);
    h.forEach((x, i) => { out[4 * i] = x >>> 24; out[4 * i + 1] = x >>> 16; out[4 * i + 2] = x >>> 8; out[4 * i + 3] = x; });
    return out;
  }

  function hmac(key, bytes) {
    let k = key.length > 64 ? sha256(key) : key;
    const block = new Uint8Array(64);
    block.set(k);
    const inner = new Uint8Array(64 + bytes.length), outer = new Uint8Array(64 + 32);
    for (let i = 0; i < 64; i++) { inner[i] = block[i] ^ 0x36; outer[i] = block[i] ^ 0x5c; }
    inner.set(bytes, 64);
    outer.set(sha256(inner), 64);
    return sha256(outer);
  }

  // What is MAC'd is ASCII; UTF-8 all the same, so a key name never changes what is hashed.
  function utf8(text) {
    const s = unescape(encodeURIComponent(text));
    const out = new Uint8Array(s.length);
    for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
    return out;
  }

  const hex = (bytes) => Array.from(bytes, (b) => (b < 16 ? "0" : "") + b.toString(16)).join("");

  // The key as the link and the key file write it: 43 base64url characters, 32 bytes.
  function keyOf(text) {
    if (typeof text !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(text)) return null;
    const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    const out = new Uint8Array(32);
    let acc = 0, nbits = 0, at = 0;
    for (const ch of text) {
      acc = (acc << 6) | alphabet.indexOf(ch);
      nbits += 6;
      if (nbits >= 8) { nbits -= 8; out[at++] = (acc >> nbits) & 0xff; }
    }
    return at === 32 ? out : null;
  }

  // The protocol's texts, as host/remote.py writes them.
  const mac = (key, text) => hex(hmac(key, utf8(text)));
  const authText = (nonce, cnonce) => `${PROTOCOL} auth ${nonce} ${cnonce}`;
  const sessionKey = (key, nonce, cnonce) => hmac(key, utf8(`${PROTOCOL} session ${nonce} ${cnonce}`));
  const hidText = (m) => `${PROTOCOL} hid ${m.seq} ${m.mods || 0} ${(m.keys || []).join(",")} ${(m.consumer || []).join(",")}`;
  const kid = (key) => hex(sha256(key)).slice(0, 16);

  // ---------- the page ----------

  const STORE = "zmkhud.remote.key";
  const RETRY_MS = 1000, RETRY_MAX_MS = 10000;

  function start(w) {
    const hud = w.hud, doc = w.document;
    let text = null;
    const given = /(?:^#|&)k=([A-Za-z0-9_-]{43})(?=&|$)/.exec(w.location.hash || "");
    if (given) {
      text = given[1];
      try { w.localStorage.setItem(STORE, text); } catch (e) { /* kept for this visit only */ }
      try { w.history.replaceState(null, "", w.location.pathname + w.location.search); } catch (e) { /* stays in the bar */ }
    } else {
      try { text = w.localStorage.getItem(STORE); } catch (e) { text = null; }
    }
    const key = keyOf(text);

    let ws = null, skey = null, nonce = null, cnonce = null, retry = RETRY_MS, timer = null, done = false;

    // Before the keymap the status line says it; once docked, the banner's sub line.
    function say(words) {
      const feed = doc.getElementById("feed");
      if (feed && words) feed.textContent = words;
      hud.receive({ kind: "typing", state: words ? "remote" : "ok", fix: words });
    }

    function stop(words) {
      done = true;
      say(words);
      if (ws) try { ws.close(); } catch (e) { /* already */ }
    }

    const sink = {
      output: "hid", repeat: "os", pages: [7],
      send(msg) {
        if (!skey || !ws || ws.readyState !== 1 || msg.kind !== "hid") return;
        ws.send(JSON.stringify(Object.assign({}, msg, { mac: mac(skey, hidText(msg)) })));
      },
    };

    function received(m) {
      if (!m || typeof m !== "object") return;
      if (!skey) {
        if (m.kind === "challenge") {
          if (m.kid !== kid(key)) { stop("paired with another code: open the link `zmk-layer-hud remote on` prints now"); return; }
          nonce = String(m.nonce);
          const random = new Uint8Array(16);
          w.crypto.getRandomValues(random);
          cnonce = hex(random);
          ws.send(JSON.stringify({ kind: "auth", v: 1, cnonce, mac: mac(key, authText(nonce, cnonce)) }));
        } else if (m.kind === "welcome") {
          const sk = sessionKey(key, nonce, cnonce);
          if (m.mac !== mac(sk, WELCOME)) { stop("this is not the computer this tablet was paired with"); return; }
          skey = sk;
          retry = RETRY_MS;
          say("");
          hud.setTouch(sink);
        }
        return;
      }
      if (["keymap", "secure", "typing"].includes(m.kind)) hud.receive(m);
    }

    function connect() {
      clearTimeout(timer); timer = null;
      if (done) return;
      skey = null;
      const s = new w.WebSocket(`ws://${w.location.host}/ws`);
      ws = s;
      s.onmessage = (e) => { let m; try { m = JSON.parse(e.data); } catch (x) { return; } if (ws === s) received(m); };
      s.onclose = (e) => {
        if (ws !== s) return;
        const was = !!skey;
        skey = null;
        if (hud.touch) hud.touch.releaseAll();     // the computer let go of the keys when it went
        if (done) return;
        if (e && e.code === 4000) { stop("another tablet took over: reload to take it back"); return; }
        if (e && e.code === 4001) { stop("refused: pair again with `zmk-layer-hud remote on`"); return; }
        say(was ? "reconnecting to the computer…" : "cannot reach the computer: is `zmk-layer-hud remote on`?");
        timer = setTimeout(connect, retry);
        retry = Math.min(retry * 2, RETRY_MAX_MS);
      };
    }

    // Docked from the start: the board as wide as the window, and as tall as it allows.
    const dock = () => hud.setDock({ on: true, max_height: w.innerHeight, split: true });
    dock();
    w.addEventListener("resize", dock);
    if (!key) { stop("not paired: open on this tablet the link `zmk-layer-hud remote on` prints"); return; }
    // Back from sleep: the socket is likely gone, and waiting out the retry is time lost.
    doc.addEventListener("visibilitychange", () => {
      if (!doc.hidden && !done && (!ws || ws.readyState > 1)) { retry = RETRY_MS; connect(); }
    });
    connect();
  }

  const ZmkRemote = { sha256, hmac, hex, utf8, keyOf, mac, authText, sessionKey, hidText, kid, WELCOME, PROTOCOL, STORE, start };
  if (typeof module === "object" && module.exports) module.exports = ZmkRemote;
  else {
    root.ZmkRemote = ZmkRemote;
    if (root.document && root.document.documentElement.classList.contains("remote") && root.hud) start(root);
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
