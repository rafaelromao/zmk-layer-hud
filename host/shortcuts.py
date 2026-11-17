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

Under niri they are binds there too, in its config's KDL: ~/.config/niri/zmk-layer-hud.kdl holds
one `binds` block, read by an `include optional=true` line once appended to the end of config.kdl
(`install_niri`). niri merges binds from included files, the last for a key winning, so the include
being last takes these keys from whatever had them. niri watches its config files and reads them
again by itself when one changes; main() also asks (`niri msg action load-config-file`) so a change
lands without the wait.

Stdlib only: the menubar runs it in the venv, but the CLI's `uninstall` may not.
"""

import contextlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile

import panelstate  # noqa: E402  (host/panelstate.py: where $STATE is)

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
# niri's side of it: our KDL file, and the line that reads it from the end of config.kdl.
NIRI_DIR = os.path.expanduser("~/.config/niri")
NIRI_MAIN = "config.kdl"
NIRI_FILE = "zmk-layer-hud.kdl"
NIRI_INCLUDE = f'include optional=true "{NIRI_FILE}"'
NIRI_MARK = "// zmk-layer-hud's shortcuts (`shortcuts:` in its config)"
# The first niri that reads `include optional=true` (niri's docs: "Since: 26.04"; a plain `include`
# is 25.11). Older, the line is not a config niri can parse, so it is not written there.
NIRI_MIN = (26, 4)


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


def kdl_string(text):
    """Any text as a KDL string literal: quoted, with its backslashes, quotes and line ends escaped
    (the escapes KDL has: \", \\, \\n, \\r, \\t)."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r") + '"'


def niri_conf(sets, command, state, start_with=""):
    """The niri file, in KDL: each shortcut bound to its verb in one `binds` block. niri runs
    spawn's arguments directly, with no shell, so the verb's environment rides along as `env`'s
    own arguments; `start_with` (uwsm-app --) goes before it, for the power shortcut alone, as the
    Hyprland binds do. niri repeats a bind while its key is held unless told `repeat=false`, as
    Hyprland's `bind` does not: held, a toggle would flicker and the power shortcut would start
    and stop the HUD over and over."""
    lines = ["// Written by zmk-layer-hud on every start, from `shortcuts:` in its config; edits here are lost.",
             f"// `{NIRI_INCLUDE}` at the end of {NIRI_MAIN} reads it. `zmk-layer-hud uninstall` takes both out.",
             "",
             "binds {"]
    for name, sc in sets.items():
        if not sc:
            continue
        mods, key = sc
        argv = (start_with.split() if start_with and name == "power" else []) + \
            ["env", f"ZMKHUD_STATE={state}", command, name]
        combo = "+".join([LINUX_NAMES[m] for m in mods] + [key.upper()])
        what = f"ZMK HUD {'show/hide' if name == 'toggle' else 'start/stop'}"
        lines.append(f"    {combo} repeat=false {{ spawn {' '.join(kdl_string(a) for a in argv)}; }} // {what}")
    lines.append("}")
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


def _rewrite(path, text):
    """The user's own file written again whole, so that at any moment it is either as it was or as
    it is meant to be: the text goes to a temporary file beside it, which then takes its place
    (os.replace). Written straight into the file, a write cut short would leave the user's
    config with its end missing. A path that is a link stays one, pointing where it did: the
    file it leads to is the one replaced. The file keeps its mode, and one this user may not
    write is refused as it was when it was written in place, though a rename would not ask."""
    real = os.path.realpath(path)
    os.close(os.open(real, os.O_WRONLY))
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(real) + ".", suffix=".tmp", dir=os.path.dirname(real))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, stat.S_IMODE(os.stat(real).st_mode))
        os.replace(tmp, real)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


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
        _rewrite(main, out)
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
        _rewrite(main, out)
        had = True
    try:
        os.remove(os.path.join(hypr_dir, HYPR_LUA))
        had = True
    except OSError:
        pass
    return had


def install_niri(sets, command, state, niri_dir=NIRI_DIR, start_with=""):
    """Our KDL file written, and included once at the end of config.kdl; whether either changed,
    or None without a config.kdl (niri has nowhere to put the include, so nothing is bound).
    niri reads its config again by itself when a file of it changes; main() asks it to as well."""
    main = os.path.join(niri_dir, NIRI_MAIN)
    if not os.path.isfile(main):
        return None
    changed = _write_if_changed(os.path.join(niri_dir, NIRI_FILE),
                                niri_conf(sets, command, state, start_with))
    with open(main, encoding="utf-8") as f:
        text = f.read()
    if NIRI_INCLUDE not in text.splitlines():
        _backup_once(main)
        with open(main, "a", encoding="utf-8") as f:       # in place: a config.kdl that is a link stays one
            f.write(("" if text.endswith("\n") or not text else "\n") +
                    "\n" + NIRI_MARK + "\n" + NIRI_INCLUDE + "\n")
        changed = True
    write_labels(state, sets, "Linux")
    return changed


def remove_niri(niri_dir=NIRI_DIR):
    """Undo install_niri; whether there was anything to take."""
    had = False
    main = os.path.join(niri_dir, NIRI_MAIN)
    try:
        with open(main, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        text = None
    if text is not None and NIRI_INCLUDE in text.splitlines():
        out = text.replace("\n" + NIRI_MARK + "\n" + NIRI_INCLUDE + "\n", "")
        out = "\n".join(line for line in out.split("\n") if line != NIRI_INCLUDE)
        _backup_once(main)
        _rewrite(main, out)
        had = True
    try:
        os.remove(os.path.join(niri_dir, NIRI_FILE))
        had = True
    except OSError:
        pass
    return had


def remove(hypr_dir=HYPR_DIR, niri_dir=NIRI_DIR):
    """Everything of ours undone, as `zmk-layer-hud uninstall` does; whether there was anything
    to take. Every compositor's files, not only the running one's: uninstall takes all of ours
    out, and what is left behind would bind keys again on the next start there."""
    return remove_lua(hypr_dir) | remove_hyprland(hypr_dir) | remove_niri(niri_dir)


def on_niri(environ=os.environ):
    """Whether this session is niri's: its socket says so at once (NIRI_SOCKET, in the environment
    of everything niri starts). Without one, a niri config beside no Hyprland says it, for the
    scripts run where neither is set -- when niri is installed at all, or a config left behind
    on a machine that never had it would take the binds from the compositor that is there; a
    Hyprland session is never niri's however its config looks."""
    if environ.get("NIRI_SOCKET"):
        return True
    if environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return False
    return shutil.which("niri") is not None and os.path.isfile(os.path.join(NIRI_DIR, NIRI_MAIN))


def niri_version():
    """niri's (major, minor) from `niri --version`, or None where that cannot be told: niri not on
    PATH, or an answer with no year.month in it. niri spells its version MAJOR.MINOR[.PATCH]
    (commit), the minor with two digits, but a distribution may build it with a string of its own,
    and what `--version` puts before that is not relied on: the first `digits.digits` is the version."""
    try:
        out = subprocess.check_output(["niri", "--version"], text=True, stderr=subprocess.DEVNULL, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    found = re.search(r"(\d+)\.(\d+)", out)
    return (int(found.group(1)), int(found.group(2))) if found else None


def main():
    """host/linux/hud.sh's step: the binds written for the config the HUD is starting with."""
    root = os.environ.get("ZMKHUD_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    state = panelstate.default_dir()
    try:
        sets = read()
    except ShortcutError as e:
        sys.exit(f"shortcuts: {e}")
    command = os.path.join(root, "bin", "zmk-layer-hud")
    start_with = "uwsm-app --" if shutil.which("uwsm-app") else ""
    if on_niri():
        version = niri_version()
        if version is not None and version < NIRI_MIN:
            # Not written: the include line would make config.kdl one this niri cannot parse.
            print(f"shortcuts: niri {version[0]}.{version[1]:02d} is older than {NIRI_MIN[0]}.{NIRI_MIN[1]:02d}, "
                  f"which `{NIRI_INCLUDE}` needs, so none are bound and {NIRI_DIR}/{NIRI_MAIN} is left as it is",
                  file=sys.stderr)
            return
        done = install_niri(sets, command, state, niri_dir=NIRI_DIR, start_with=start_with)
        path = os.path.join(NIRI_DIR, NIRI_FILE)
        reload_what = ["niri", "msg", "action", "load-config-file"]
        absent = f"shortcuts: no {NIRI_DIR}/{NIRI_MAIN}, so none are bound"
    else:
        # A Lua config wins: Hyprland reads hyprland.lua where there is one (Omarchy 4), and nothing
        # reads a hyprland.conf left beside it, so what an earlier version wrote there goes.
        if os.path.isfile(os.path.join(HYPR_DIR, "hyprland.lua")):
            remove_hyprland(hypr_dir=HYPR_DIR)
            done = install_lua(sets, command, state, hypr_dir=HYPR_DIR, start_with=start_with)
            path = os.path.join(HYPR_DIR, HYPR_LUA)
        else:
            done = install_hyprland(sets, command, state, hypr_dir=HYPR_DIR, start_with=start_with)
            path = os.path.join(HYPR_DIR, HYPR_FILE)
        reload_what = ["hyprctl", "reload"]
        absent = f"shortcuts: no {HYPR_DIR}/hyprland.lua or hyprland.conf, so none are bound"
    if done is None:
        print(absent, file=sys.stderr)
    elif done:
        # The compositor told to read its config again, so the binds are there without waiting for
        # its watcher to notice: niri's load-config-file, Hyprland's reload (as Omarchy's own
        # scripts do after editing its config). Its client not being on PATH is not a failure of
        # the HUD's start (hud.sh stops on one): there is nothing to ask, and the compositor's own
        # watcher reads the files when it can.
        try:
            code = subprocess.call(reload_what, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            code = None
        print("shortcuts: " + ", ".join(f"{label(sc)} {WHAT[n]} the HUD" for n, sc in sets.items() if sc) +
              f" ({path})")
        if code not in (0, None):
            print(f"shortcuts: `{' '.join(reload_what)}` exited {code}: the compositor may not have taken the new "
                  "config (its own message says why)", file=sys.stderr)


if __name__ == "__main__":
    main()
