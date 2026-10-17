#!/usr/bin/env node
/* zmk-layer-hud — hud/hidtext.js, the page's port of the feed's decoder, held to the decoder itself.
 *
 *   node hud/tests/hidtext_test.js
 *
 * For every legend a fixture keymap draws, host/uskeys.py gives the report snapshots a US-layout
 * keyboard sends to type it and the key events the feed's own SignalDecoder (host/hiddecode.py) makes of them; the port
 * must make the same events of the same snapshots, with and without AltGr. Then what a text-only
 * keyboard does with them (textEvents). Needs python3 (PYTHON=... to choose one); skips without it.
 */
"use strict";

const path = require("node:path");
const { execFileSync } = require("node:child_process");
const ZmkHidText = require("../hidtext.js");

const REPO = path.join(__dirname, "..", "..");
const FIXTURES = ["example-3x5.json", "romak-34.json", "diamond.json"];
const fail = [];
let checks = 0;
function check(ok, what) { checks++; if (!ok) fail.push(what); }

function python(args, input) {
  const tried = [process.env.PYTHON, path.join(REPO, ".venv/bin/python3"), "python3"].filter(Boolean);
  for (const py of tried) {
    try { return execFileSync(py, args, { input, encoding: "utf8", cwd: REPO, stdio: ["pipe", "pipe", "ignore"] }); } catch (e) { /* the next */ }
  }
  return null;
}

function decode(snapshots, altgr) {
  const d = ZmkHidText.decoder({ altgr });
  const out = [];
  let now = 0;
  for (const s of snapshots) { out.push(...d.feed(s, now)); now += 5; }
  out.push(...d.flush(now + 1000));
  return out.filter((e) => e.type === "keyDown");
}

const strip = (e) => ({ type: e.type, name: e.name, chars: e.chars, code: e.code, flags: e.flags, composed: e.composed });

let legends = 0;
for (const fixture of FIXTURES) {
  const corpus = python([path.join(REPO, "host/uskeys.py"), "--corpus", path.join(__dirname, "fixtures", fixture)]);
  if (corpus === null) { console.log("hidtext_test: no python3, skipping"); process.exit(0); }
  for (const altgr of [false, true]) {
    const out = python([path.join(REPO, "host/uskeys.py"), "--events"].concat(altgr ? ["--altgr"] : []), corpus);
    for (const line of out.trim().split("\n").filter(Boolean)) {
      const c = JSON.parse(line);
      if (!c.snapshots) continue;
      legends++;
      const got = decode(c.snapshots, altgr).map(strip);
      const want = c.events.map(strip);
      check(JSON.stringify(got) === JSON.stringify(want),
            `${fixture}${altgr ? " (AltGr)" : ""} ${JSON.stringify(c.legend)}: got ${JSON.stringify(got)}, hudfeed ${JSON.stringify(want)}`);
    }
  }
}

// What a text sink does with them.
(function textOps() {
  const d = ZmkHidText.decoder({});
  const typed = [];
  const at = (report, t) => typed.push(...d.feed(report, t));
  at({ mods: 0, keys: [0x04] }, 0); at({ mods: 0, keys: [] }, 10);          // a
  at({ mods: 2, keys: [0x04] }, 20); at({ mods: 0, keys: [] }, 30);         // A
  at({ mods: 0, keys: [0x2A] }, 40); at({ mods: 0, keys: [] }, 50);         // backspace
  at({ mods: 0, keys: [0x28] }, 60); at({ mods: 0, keys: [] }, 70);         // return
  at({ mods: 8, keys: [0x06] }, 80); at({ mods: 0, keys: [] }, 90);         // cmd-c
  at({ mods: 0, keys: [0x34] }, 100); at({ mods: 0, keys: [0x04] }, 110); at({ mods: 0, keys: [] }, 120);  // ' a: á
  at({ mods: 0, keys: [0x2C] }, 300); at({ mods: 0, keys: [] }, 310);       // space
  const ops = ZmkHidText.textEvents(typed);
  const want = [
    { kind: "text", text: "a", usage: 4, mods: 0 }, { kind: "text", text: "A", usage: 4, mods: 2 },
    { kind: "key", key: "backspace", usage: 0x2A, mods: 0 }, { kind: "key", key: "enter", usage: 0x28, mods: 0 },
    { kind: "chord", chars: "c", name: "c", usage: 6, mods: 8 }, { kind: "text", text: "á", usage: 4, mods: 0 },
    { kind: "text", text: " ", usage: 0x2C, mods: 0 }];
  check(JSON.stringify(ops) === JSON.stringify(want), `text ops: ${JSON.stringify(ops)}`);
  check(!ZmkHidText.textEvents(typed, { shortcuts: false }).some((o) => o.kind === "chord"), "no chords for a sink without shortcuts");
  const pending = ZmkHidText.decoder({});
  pending.feed({ mods: 0, keys: [0x34] }, 0);
  check(pending.pending() === "'", "a dead key waiting for its letter is said");
})();

for (const f of fail.slice(0, 30)) console.log("FAIL " + f);
console.log(`${legends} legends decoded as the feed decodes them, ${checks} checks, ${fail.length} failures`);
process.exit(fail.length ? 1 : 0);
