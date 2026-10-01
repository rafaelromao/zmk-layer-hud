#!/usr/bin/env python3
"""The HUD's two global shortcuts, the same on macOS and Omarchy:

  toggle   Ctrl+Alt+L       shows the HUD, or hides it (`zmk-layer-hud toggle`)
  power    Ctrl+Alt+Gui+L   starts the HUD, or stops it (`zmk-layer-hud power`)

`shortcuts: { toggle: ctrl+alt+l, power: ctrl+alt+gui+l }` in the config changes them, and null
turns one off. Nothing in a menu edits them; the menus only show them.

Who binds them differs. On macOS it is the menubar icon's process (host/macos/menubar.py), which
stays when the HUD quits and so can start it again. A Wayland app cannot take a key for itself, so
on Hyprland they are binds: `install_hyprland` writes ~/.config/hypr/zmk-layer-hud.conf and sources
it at the end of hyprland.conf, after Omarchy's own bindings, so that its `unbind`s take whatever
else was on those keys. host/linux/hud.sh runs this file on every start, which does that.

Stdlib only: the menubar runs it in the venv, but the CLI's `uninstall` may not.
"""

import json
import os
import shlex
import shutil
import sys

DEFAULTS = {"toggle": "ctrl+alt+l", "power": "ctrl+alt+gui+l"}
WHAT = {"toggle": "shows or hides", "power": "starts or stops"}

# The order they are written in, whichever order they were typed in.
MODS = ("ctrl", "alt", "shift", "gui")
ALIASES = {"control": "ctrl", "opt": "alt", "option": "alt", "cmd": "gui", "command": "gui",
           "super": "gui", "win": "gui", "meta": "gui"}
KEYS = set("abcdefghijklmnopqrstuvwxyz0123456789")

MAC_GLYPHS = {"ctrl": "⌃", "alt": "⌥", "shift": "⇧", "gui": "⌘"}
LINUX_NAMES = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "gui": "Super"}
HYPR_NAMES = {"ctrl": "CTRL", "alt": "ALT", "shift": "SHIFT", "gui": "SUPER"}

HYPR_DIR = os.path.expanduser("~/.config/hypr")
HYPR_FILE = "zmk-layer-hud.conf"
HYPR_SOURCE = f"source = ~/.config/hypr/{HYPR_FILE}"
JSON_FILE = "shortcuts.json"


class ShortcutError(ValueError):
    """A shortcut in the config that cannot be bound, said so it can be fixed."""


def parse(text):
    """'ctrl+alt+l' as (mods, key): mods a tuple in MODS order, key one letter or digit."""
    if not isinstance(text, str):
        raise ShortcutError(f"{text!r}: a shortcut is written like ctrl+alt+l")
    words = [w.strip().lower() for w in text.split("+")]
    if not words or not words[-1]:
        raise ShortcutError(f"{text!r}: a shortcut ends in its key, like ctrl+alt+l")
    key, mods = words[-1], set()
    for w in words[:-1]:
        mod = ALIASES.get(w, w)
        if mod not in MODS:
            raise ShortcutError(f"{text!r}: {w!r} is not a modifier (ctrl, alt, shift, gui)")
        mods.add(mod)
    if key not in KEYS:
        raise ShortcutError(f"{text!r}: the key must be a letter or a digit")
    if not mods & {"ctrl", "alt", "gui"}:
        raise ShortcutError(f"{text!r}: a global shortcut needs ctrl, alt or gui, or it takes the key from typing")
    return tuple(m for m in MODS if m in mods), key


def settings(cfg):
    """{"toggle": (mods, key) or None, "power": …} from a loaded config, the defaults filled in."""
    given = (cfg or {}).get("shortcuts") if isinstance(cfg, dict) else None
    if given is None:
        given = {}
    if not isinstance(given, dict):
        raise ShortcutError("shortcuts: expected { toggle: ctrl+alt+l, power: ctrl+alt+gui+l }")
    unknown = set(given) - set(DEFAULTS)
    if unknown:
        raise ShortcutError(f"shortcuts: {', '.join(sorted(unknown))} is not one (toggle, power)")
    out = {}
    for name, default in DEFAULTS.items():
        text = given.get(name, default)
        out[name] = None if text in (None, False) else parse(text)
    taken = [sc for sc in out.values() if sc]
    if len(taken) != len(set(taken)):
        raise ShortcutError("shortcuts: toggle and power are the same keys")
    return out


def _keymap():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import keymap
    return keymap


def keymap_config(config_path=None):
    """The config the HUD reads (host/keymap.py finds it), or None when there is none."""
    keymap = _keymap()
    try:
        return keymap.find_config(config_path)
    except keymap.KeymapError:
        return None


def read(config_path=None):
    """The shortcuts the config says, or the defaults when there is no config."""
    path = keymap_config(config_path)
    return settings(_keymap().load_yaml(path) if path else {})


def label(sc, system="Linux"):
    """How a menu shows it: ⌃⌥L on macOS, Ctrl+Alt+L elsewhere."""
    if not sc:
        return ""
    mods, key = sc
    if system == "Darwin":
        return "".join(MAC_GLYPHS[m] for m in mods) + key.upper()
    return "+".join([LINUX_NAMES[m] for m in mods] + [key.upper()])


def hyprland_conf(sets, command, state, start_with=""):
    """The Hyprland file: each shortcut taken from whatever had it, then bound to its verb."""
    lines = ["# Written by zmk-layer-hud on every start, from `shortcuts:` in its config; edits here are lost.",
             f"# {HYPR_SOURCE} at the end of hyprland.conf reads it. `zmk-layer-hud uninstall` takes both out.",
             ""]
    for name, sc in sets.items():
        if not sc:
            continue
        mods, key = sc
        combo = f"{' '.join(HYPR_NAMES[m] for m in mods)}, {key.upper()}"
        # A HUD a bind starts would live in Hyprland's own cgroup without uwsm-app.
        run = (start_with + " " if start_with and name == "power" else "") + \
            f"env ZMKHUD_STATE={shlex.quote(state)} {shlex.quote(command)} {name}"
        lines += [f"unbind = {combo}", f"bindd = {combo}, ZMK HUD {'show/hide' if name == 'toggle' else 'start/stop'}, exec, {run}"]
    return "\n".join(lines) + "\n"


def _write_if_changed(path, text):
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
    return True


def write_labels(state, sets, system):
    """$STATE/shortcuts.json, which the Omarchy bar's menu reads its labels from."""
    _write_if_changed(os.path.join(state, JSON_FILE),
                      json.dumps({name: label(sc, system) for name, sc in sets.items()}) + "\n")


def install_hyprland(sets, command, state, hypr_dir=HYPR_DIR, start_with=""):
    """Our file written, and sourced once at the end of hyprland.conf; whether either changed.
    Hyprland reads its config again by itself when a file of it changes."""
    if not os.path.isfile(os.path.join(hypr_dir, "hyprland.conf")):
        return None
    changed = _write_if_changed(os.path.join(hypr_dir, HYPR_FILE), hyprland_conf(sets, command, state, start_with))
    main = os.path.join(hypr_dir, "hyprland.conf")
    with open(main, encoding="utf-8") as f:
        text = f.read()
    if HYPR_SOURCE not in text.splitlines():
        with open(main, "a", encoding="utf-8") as f:       # in place: a hyprland.conf that is a link stays one
            f.write(("" if text.endswith("\n") or not text else "\n") +
                    "\n# zmk-layer-hud's shortcuts (`shortcuts:` in its config)\n" + HYPR_SOURCE + "\n")
        changed = True
    write_labels(state, sets, "Linux")
    return changed


def remove_hyprland(hypr_dir=HYPR_DIR):
    """Undo install_hyprland; whether there was anything to take."""
    had = False
    main = os.path.join(hypr_dir, "hyprland.conf")
    try:
        with open(main, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        text = None
    if text is not None and HYPR_SOURCE in text.splitlines():
        out = text.replace("\n# zmk-layer-hud's shortcuts (`shortcuts:` in its config)\n" + HYPR_SOURCE + "\n", "")
        out = "\n".join(line for line in out.split("\n") if line != HYPR_SOURCE)
        with open(main, "w", encoding="utf-8") as f:
            f.write(out)
        had = True
    try:
        os.remove(os.path.join(hypr_dir, HYPR_FILE))
        had = True
    except OSError:
        pass
    return had


def main():
    """host/linux/hud.sh's step: the binds written for the config the HUD is starting with."""
    root = os.environ.get("ZMKHUD_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    state = os.environ.get("ZMKHUD_STATE") or os.path.join(
        os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "zmk-layer-hud")
    try:
        sets = read()
    except ShortcutError as e:
        sys.exit(f"shortcuts: {e}")
    done = install_hyprland(sets, os.path.join(root, "bin", "zmk-layer-hud"), state,
                            start_with="uwsm-app --" if shutil.which("uwsm-app") else "")
    if done is None:
        print(f"shortcuts: no {HYPR_DIR}/hyprland.conf, so none are bound", file=sys.stderr)
    elif done:
        print("shortcuts: " + ", ".join(f"{label(sc)} {WHAT[n]} the HUD" for n, sc in sets.items() if sc) +
              f" ({os.path.join(HYPR_DIR, HYPR_FILE)})")


if __name__ == "__main__":
    main()
