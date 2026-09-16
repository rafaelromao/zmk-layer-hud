/* zmk-layer-hud — typed-keys strip. Attaches to #keys and exposes window.keys.key(event).
 * Used below the board in index.html and standalone in keys.html. Events are the feed's key
 * messages: {type, name, chars, flags}. Consecutive plain characters merge into one chip; named
 * keys and chords get their own chip. Nothing is shown for key releases or modifier changes. */
(function () {
  "use strict";
  const host = document.getElementById("keys");
  if (!host) return;
  const NAMED = { space: "␣", return: "⏎", escape: "⎋", delete: "⌫", forwarddelete: "⌦", tab: "⇥",
    left: "←", right: "→", up: "↑", down: "↓", home: "⇱", end: "⇲", pageup: "⇞", pagedown: "⇟",
    insert: "⎀", capslock: "⇪" };
  for (let i = 1; i <= 24; i++) NAMED["f" + i] = "F" + i;
  const MODS = [["cmd", "⌘"], ["ctrl", "⌃"], ["alt", "⌥"], ["shift", "⇧"]];
  const IDLE_MS = 1800, MAX_CHIPS = 5;
  let current = null, lastAt = 0, timer = null;

  function newChip(text, cls) {
    const c = document.createElement("div");
    c.className = "chip " + (cls || "");
    c.textContent = text;
    host.appendChild(c);
    requestAnimationFrame(() => c.classList.add("show"));
    while (host.children.length > MAX_CHIPS) host.firstChild.remove();
    return c;
  }
  function armFade() {
    clearTimeout(timer);
    timer = setTimeout(() => {
      for (const c of host.children) c.classList.add("fade");
      setTimeout(() => { host.innerHTML = ""; current = null; }, 520);
    }, IDLE_MS);
  }
  window.keys = {
    key(ev) {
      if (typeof ev === "string") ev = JSON.parse(ev);
      if (ev.type !== "keyDown" || ev.repeat) return;
      const flags = (ev.flags && !Array.isArray(ev.flags)) ? ev.flags : {};
      const mods = MODS.filter(([k]) => flags[k]).map(([, sym]) => sym).join("");
      const chord = flags.cmd || flags.ctrl || flags.alt;
      const named = ev.name && NAMED[ev.name];
      const now = Date.now();
      if (!chord && !named && ev.chars && ev.chars.length === 1 && ev.chars >= " ") {
        if (current && now - lastAt < 1200 && current.textContent.length < 22) {
          current.textContent += ev.chars;
        } else {
          current = newChip(ev.chars);
        }
      } else {
        const label = mods + (named || (ev.chars && ev.chars >= " " ? ev.chars.toUpperCase() : (ev.name || "?").toUpperCase()));
        newChip(label, chord ? "chord" : "special");
        current = null;
      }
      lastAt = now;
      armFade();
    },
  };
})();
