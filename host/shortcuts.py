#!/usr/bin/env python3
"""The HUD's two global shortcuts, the same on macOS and Omarchy:

  toggle   Ctrl+Alt+L       shows the HUD, or hides it (`zmk-layer-hud toggle`)
  power    Ctrl+Alt+Gui+L   starts the HUD, or stops it (`zmk-layer-hud power`)

`shortcuts: { toggle: ctrl+alt+l, power: ctrl+alt+gui+l }` in the config changes them, and null
turns one off. Nothing in a menu edits them; the menus only show them.

Who binds them differs. On macOS it is the menubar icon's process (host/macos/menubar.py), which
stays when the HUD quits and so can start it again. A Wayland app cannot take a key for itself, so
on Hyprland they are binds, written in the language its config is. A Lua config (Omarchy 4's
~/.config/hypr/hyprland.lua) gets ~/.config/hypr/zmk-layer-hud.lua, read by a line at its end
(`install_lua`); a hyprlang one gets ~/.config/hypr/zmk-layer-hud.conf, sourced at the end of
hyprland.conf (`install_hyprland`). Either way they come after Omarchy's own bindings, so they take
whatever else was on those keys. host/linux/hud.sh runs this file on every start, which does that
and has Hyprland read its config again when anything changed.

Stdlib only: the menubar runs it in the venv, but the CLI's `uninstall` may not.
"""

import contextlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile

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
HYPR_LUA = "zmk-layer-hud.lua"
# What a Lua config gets, once, at its end. In a pcall: a file gone missing, or one that fails,
# costs the shortcuts and nothing else of the config.
LUA_LOAD = 'pcall(dofile, (os.getenv("HOME") or "") .. "/.config/hypr/' + HYPR_LUA + '")'
LUA_MARK = "-- zmk-layer-hud's shortcuts (`shortcuts:` in its config)"


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


def _binds(sets, command, state, start_with=""):
    """(mods, key, what the bind is called, the command it runs) for each shortcut that is on."""
    out = []
    for name, sc in sets.items():
        if not sc:
            continue
        mods, key = sc
        # A HUD a bind starts would live in Hyprland's own cgroup without uwsm-app.
        run = (start_with + " " if start_with and name == "power" else "") + \
            f"env ZMKHUD_STATE={shlex.quote(state)} {shlex.quote(command)} {name}"
        out.append((mods, key, f"ZMK HUD {'show/hide' if name == 'toggle' else 'start/stop'}", run))
    return out


def hyprland_conf(sets, command, state, start_with=""):
    """The Hyprland file, in hyprlang: each shortcut taken from whatever had it, then bound to its verb."""
    lines = ["# Written by zmk-layer-hud on every start, from `shortcuts:` in its config; edits here are lost.",
             f"# {HYPR_SOURCE} at the end of hyprland.conf reads it. `zmk-layer-hud uninstall` takes both out.",
             ""]
    for mods, key, what, run in _binds(sets, command, state, start_with):
        combo = f"{' '.join(HYPR_NAMES[m] for m in mods)}, {key.upper()}"
        lines += [f"unbind = {combo}", f"bindd = {combo}, {what}, exec, {run}"]
    return "\n".join(lines) + "\n"


def lua_string(text):
    """Any text as a Lua string literal: quoted, with its backslashes, quotes and line ends escaped."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r") + '"'


def lua_conf(sets, command, state, start_with=""):
    """The same file in Lua, for a config written in it (Omarchy 4's): what Omarchy's own o.rebind
    does -- hl.unbind, in a pcall because nothing may have those keys, then hl.bind -- in
    Hyprland's own calls, so it needs nothing of Omarchy's."""
    lines = ["-- Written by zmk-layer-hud on every start, from `shortcuts:` in its config; edits here are lost.",
             "-- The last line of hyprland.lua reads it. `zmk-layer-hud uninstall` takes both out.",
             ""]
    for mods, key, what, run in _binds(sets, command, state, start_with):
        keys = lua_string(" + ".join([HYPR_NAMES[m] for m in mods] + [key.upper()]))
        lines += [f"pcall(hl.unbind, {keys})",
                  f"hl.bind({keys}, hl.dsp.exec_cmd({lua_string(run)}), {{ description = {lua_string(what)} }})"]
    return "\n".join(lines) + "\n"


def _write_if_changed(path, text):
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return False
    except OSError:
        pass
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=d)   # 0600, as the rest of ours
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return True


def _backup_once(path):
    """A copy of the user's file as it was before this ever touched it, kept beside it as
    `<file>.bak-zmk-layer-hud` (the name `menubar enable` uses for Omarchy's shell.json). Made
    once: a later run must not write over the one copy that predates us."""
    bak = path + ".bak-zmk-layer-hud"
    if not os.path.exists(bak):
        shutil.copy2(path, bak)      # follows a hyprland.conf that is a link, as the edit does


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
        _backup_once(main)
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
        _backup_once(main)
        with open(main, "w", encoding="utf-8") as f:
            f.write(out)
        had = True
    try:
        os.remove(os.path.join(hypr_dir, HYPR_FILE))
        had = True
    except OSError:
        pass
    return had


def install_lua(sets, command, state, hypr_dir=HYPR_DIR, start_with=""):
    """For a config written in Lua: our file written, and read once from the end of hyprland.lua;
    whether either changed, or None without a hyprland.lua."""
    main = os.path.join(hypr_dir, "hyprland.lua")
    if not os.path.isfile(main):
        return None
    changed = _write_if_changed(os.path.join(hypr_dir, HYPR_LUA), lua_conf(sets, command, state, start_with))
    with open(main, encoding="utf-8") as f:
        text = f.read()
    if LUA_LOAD not in text.splitlines():
        _backup_once(main)
        with open(main, "a", encoding="utf-8") as f:       # in place: a hyprland.lua that is a link stays one
            f.write(("" if text.endswith("\n") or not text else "\n") + "\n" + LUA_MARK + "\n" + LUA_LOAD + "\n")
        changed = True
    write_labels(state, sets, "Linux")
    return changed


def remove_lua(hypr_dir=HYPR_DIR):
    """Undo install_lua; whether there was anything to take."""
    had = False
    main = os.path.join(hypr_dir, "hyprland.lua")
    try:
        with open(main, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        text = None
    if text is not None and LUA_LOAD in text.splitlines():
        out = text.replace("\n" + LUA_MARK + "\n" + LUA_LOAD + "\n", "")
        out = "\n".join(line for line in out.split("\n") if line != LUA_LOAD)
        _backup_once(main)
        with open(main, "w", encoding="utf-8") as f:
            f.write(out)
        had = True
    try:
        os.remove(os.path.join(hypr_dir, HYPR_LUA))
        had = True
    except OSError:
        pass
    return had


def remove(hypr_dir=HYPR_DIR):
    """Both kinds undone, as `zmk-layer-hud uninstall` does; whether there was anything to take."""
    return remove_lua(hypr_dir) | remove_hyprland(hypr_dir)


def main():
    """host/linux/hud.sh's step: the binds written for the config the HUD is starting with."""
    root = os.environ.get("ZMKHUD_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    state = os.environ.get("ZMKHUD_STATE") or os.path.join(
        os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "zmk-layer-hud")
    try:
        sets = read()
    except ShortcutError as e:
        sys.exit(f"shortcuts: {e}")
    command = os.path.join(root, "bin", "zmk-layer-hud")
    start_with = "uwsm-app --" if shutil.which("uwsm-app") else ""
    # A Lua config wins: Hyprland reads hyprland.lua where there is one (Omarchy 4), and nothing
    # reads a hyprland.conf left beside it, so what an earlier version wrote there goes.
    if os.path.isfile(os.path.join(HYPR_DIR, "hyprland.lua")):
        remove_hyprland(hypr_dir=HYPR_DIR)
        done = install_lua(sets, command, state, hypr_dir=HYPR_DIR, start_with=start_with)
        path = os.path.join(HYPR_DIR, HYPR_LUA)
    else:
        done = install_hyprland(sets, command, state, hypr_dir=HYPR_DIR, start_with=start_with)
        path = os.path.join(HYPR_DIR, HYPR_FILE)
    if done is None:
        print(f"shortcuts: no {HYPR_DIR}/hyprland.lua or hyprland.conf, so none are bound", file=sys.stderr)
    elif done:
        # Hyprland told to read its config again, as Omarchy's own scripts do after editing it.
        subprocess.call(["hyprctl", "reload"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("shortcuts: " + ", ".join(f"{label(sc)} {WHAT[n]} the HUD" for n, sc in sets.items() if sc) +
              f" ({path})")


if __name__ == "__main__":
    main()
