#!/usr/bin/env node
/* zmk-layer-hud — the answers the tablets' keyboards are held to: a script of fingers on
 * kbd-keymap.json, and what hud/kbd.js answers each step with (text ops, refused keys, whether the
 * board changed, when to tick) and the board after it. The iPad's JSKbd replays the steps in
 * JavaScriptCore and must answer the same (apps/ios/Core); the Android sink reads the ops as the
 * page sends them (apps/android/sink).
 *
 *   node apps/fixtures/golden.js           write kbd-golden.json   (make golden)
 *   node apps/fixtures/golden.js --check   fail when it is behind hud/ (make test-apps)
 */
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const Kbd = require("../../hud/kbd.js");

const HERE = __dirname;
const OUT = path.join(HERE, "kbd-golden.json");
const KEYMAP = JSON.parse(fs.readFileSync(path.join(HERE, "kbd-keymap.json"), "utf8"));

// [what, key or time, time]: the keymap's base is a ' ␣/nav ⇧ e ✕(mouse), nav puts 1 on a.
const STEPS = [
  ["down", 0, 1000], ["up", 0, 1040],                                           // a
  ["down", 3, 2000], ["down", 0, 2100], ["up", 0, 2140], ["up", 3, 2200],       // ⇧ held, a: A
  ["down", 2, 3000], ["tick", 3300], ["down", 0, 3400], ["up", 0, 3440], ["up", 2, 3500],   // nav held, a: 1
  ["down", 2, 4000], ["up", 2, 4050],                                           // ␣ tapped: a space
  ["down", 1, 5000], ["up", 1, 5040], ["down", 4, 5050], ["up", 4, 5090],       // ' then e: é
  ["down", 1, 6000], ["up", 1, 6040], ["tick", 6200],                           // ' alone, when its time is up
  ["down", 5, 7000], ["up", 5, 7040],                                           // a mouse button: refused
  ["down", 0, 8000], ["tick", 8450], ["tick", 8510], ["up", 0, 8520],           // a held: repeated
  ["down", 3, 9000], ["releaseAll", 9100],                                      // the keyboard goes away
];

function make() {
  const kbd = Kbd.create(JSON.stringify(KEYMAP));
  const steps = STEPS.map(([what, a, b]) => {
    const answer = what === "down" ? kbd.down(a, b) : what === "up" ? kbd.up(a, b) : what === "tick" ? kbd.tick(a) : kbd.releaseAll(a);
    return { do: what, key: what === "down" || what === "up" ? a : null, t: what === "down" || what === "up" ? b : a,
             answer, board: kbd.board() };
  });
  return { _: "written by apps/fixtures/golden.js from hud/kbd.js; do not edit by hand", keymap: "kbd-keymap.json",
           geometry: { width: 1024, maxHeight: 300, split: true, answer: kbd.geometry(1024, 300, true) }, steps };
}

const text = JSON.stringify(make(), null, 1) + "\n";
if (process.argv.includes("--check")) {
  const now = fs.existsSync(OUT) ? fs.readFileSync(OUT, "utf8") : "";
  if (now !== text) {
    console.log("apps/fixtures/kbd-golden.json is behind hud/: make golden, then commit it");
    process.exit(1);
  }
  console.log("the tablets' golden answers: current");
} else {
  fs.writeFileSync(OUT, text);
  console.log(`wrote ${path.relative(process.cwd(), OUT)}: ${STEPS.length} steps`);
}
