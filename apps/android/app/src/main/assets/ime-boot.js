/* zmk-layer-hud on Android: the HUD's page as the system keyboard (apps/android, HudIme.kt).
 *
 * Run by the keyboard once the page has loaded, after window.ZMKHUD_IME = {maxHeight, canSwitch} and
 * before the keymap. The board types text into the field (a "text" sink: hud/touch.js turns the
 * virtual firmware's reports into text, keys and shortcuts), the engine repeats a held key, and it
 * is docked as tall as the keyboard's share of the screen allows. What it types and how tall it came
 * out go to the keyboard through its bridge, zmkhudIme.
 */
(function () {
  "use strict";
  const hud = window.hud, conf = window.ZMKHUD_IME || {};
  if (!hud || window.zmkhudBooted) return;
  window.zmkhudBooted = true;
  const post = (m) => { try { window.zmkhudIme.postMessage(JSON.stringify(m)); } catch (e) { /* not in the keyboard */ } };

  hud.setTouch({ output: "text", repeat: "synthetic", shortcuts: true, pages: [7], send: post });
  hud.setDock({ on: true, max_height: conf.maxHeight || window.innerHeight, split: true });

  // The next keyboard, where Android says this one should offer it.
  const bar = document.getElementById("layer");
  if (bar && conf.canSwitch) {
    const b = document.createElement("button");
    b.id = "nextKeyboard";
    b.title = "the next keyboard";
    b.setAttribute("aria-label", "the next keyboard");
    b.textContent = "🌐";
    // As the banner's own buttons are drawn (hud.css #close), at the far end and big enough for a finger.
    b.style.cssText = "margin-left: auto; width: 28px; height: 22px; border-radius: 4px; padding: 0; cursor: pointer; " +
      "border: 1px solid rgba(255,255,255,0.12); background: rgba(255,255,255,0.06); font: 13px/1 var(--font);";
    b.addEventListener("click", () => post({ kind: "switch" }));
    bar.appendChild(b);
  }

  // How tall the board came out: the keyboard's window follows it.
  const say = () => post({ kind: "size", height: Math.ceil(document.body.scrollHeight) });
  if (window.ResizeObserver) new ResizeObserver(say).observe(document.body);
  say();

  // Until a keymap is imported the banner says where one comes from.
  setTimeout(() => {
    if (hud.state.data) return;
    const name = document.getElementById("layerName");
    if (name) name.textContent = "no keymap yet: import one in the ZMK Layer HUD app";
  }, 0);
})();
