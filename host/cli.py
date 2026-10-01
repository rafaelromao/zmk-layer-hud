#!/usr/bin/env python3
"""zmk-layer-hud — one command for everything the HUD does.

`bin/zmk-layer-hud` finds an interpreter and hands off here. The verbs split in two:

  the HUD          start, stop, restart, power, show, hide, toggle, status, log
  the typing       session, heatmap
  the keymap       keymap, import, sync, config
  this machine     setup, doctor, menubar, autostart, update, uninstall, version
  without a board  demo, poke, feed

Nothing above the stdlib is imported at module level, and that is deliberate: `doctor` and
`setup` have to run on a machine where the venv does not exist yet -- that is when they are most
needed -- and Apple's /usr/bin/python3 is 3.9, where keymap-drawer will not even install. The
verbs that do need the venv re-exec into it first; see `NEEDS_VENV` and `needs_venv`.
"""

import argparse
import datetime
import os
import platform
import re
import shutil
import subprocess
import sys
import time

# Ours, and stdlib-only like this file: what the running panel says of itself, and how it is asked
# to show or hide.
import panelstate

# The shim exports this; computed here too so `python3 host/cli.py` works from a clone.
ROOT = os.environ.get("ZMKHUD_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PYTHON = os.path.join(ROOT, ".venv", "bin", "python3")

# Logs live outside the tree because `update` replaces the tree wholesale, and the directory the
# logs are in cannot be the directory being swapped. The host scripts read the same variable.
STATE = os.environ.get("ZMKHUD_STATE") or os.path.join(
    os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "zmk-layer-hud")

CONFIG_DIR = os.path.expanduser("~/.config/zmk-layer-hud")
CONFIG = os.path.join(CONFIG_DIR, "config.yaml")
BIN_DIR = os.path.expanduser(os.environ.get("ZMKHUD_BIN_DIR") or "~/.local/bin")
BIN_LINK = os.path.join(BIN_DIR, "zmk-layer-hud")

REPO = "rafaelromao/zmk-layer-hud"
# The macOS login item's bundle id and label.
APP_ID = "io.github.rafaelromao.zmk-layer-hud"
OMARCHY_PLUGINS = os.path.expanduser("~/.config/omarchy/plugins")
# Starting at login: a LaunchAgent and the small app it runs on macOS, an XDG autostart entry on Linux.
LAUNCH_AGENT = os.path.expanduser(f"~/Library/LaunchAgents/{APP_ID}.plist")
LOGIN_APP = os.path.expanduser("~/Library/Application Support/zmk-layer-hud/ZMK Layer HUD.app")
AUTOSTART_DESKTOP = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                                 "autostart", "zmk-layer-hud.desktop")
PYTHON_MIN = (3, 10)

# The one definition of what the venv holds. hidapi is macOS only: Linux reads /dev/hidrawN
# itself, and the wheel there bundles the libusb backend, which wants an access no udev rule
# grants and detaches the kernel HID driver.
VENV_PKGS = ["pyserial", "keymap-drawer", "websockets", "bleak"]
if platform.system() == "Darwin":
    VENV_PKGS += ["hidapi", "pyobjc-framework-Cocoa", "pyobjc-framework-WebKit"]

# Verbs that need keymap-drawer, pyserial, websockets or pyobjc. Everything else must keep
# working on a half-installed machine.
NEEDS_VENV = {"start", "restart", "power", "keymap", "import", "sync", "poke", "feed", "demo"}


class Fail(Exception):
    """A message for the user, not a traceback."""


def warn(msg):
    print(msg, file=sys.stderr)


# ---------- the machine ----------

def host_script():
    """The platform's start/stop script. The work stays in these two; this file only picks one
    and gives them the same verbs, because remembering which host spells it `hud.sh` is not worth
    anyone's time."""
    system = platform.system()
    if system == "Darwin":
        return os.path.join(ROOT, "host", "macos", "start.sh")
    if system == "Linux":
        return os.path.join(ROOT, "host", "linux", "hud.sh")
    raise Fail(f"no HUD host for {system}; macOS and Linux (Hyprland) only")


def in_clone():
    return os.path.isdir(os.path.join(ROOT, ".git"))


def env_for_host(reserve=False, hidden=False):
    env = dict(os.environ)
    env["ZMKHUD_RESERVE"] = "1" if reserve else "0"
    env["ZMKHUD_HIDDEN"] = "1" if hidden else "0"
    env["ZMKHUD_STATE"] = STATE
    env["ZMKHUD_ROOT"] = ROOT
    # The host scripts prefer $ZMKHUD_PYTHON over their own search, so pointing them at the venv
    # here means neither has to learn where an installed tree keeps it.
    if os.path.exists(VENV_PYTHON) and not os.environ.get("ZMKHUD_PYTHON"):
        env["ZMKHUD_PYTHON"] = VENV_PYTHON
    return env


def run_host(verb, reserve=False, hidden=False, foreground=False):
    os.makedirs(STATE, exist_ok=True)
    env = env_for_host(reserve, hidden)
    if foreground:
        # The host script's `run` execs the panel, so this process becomes the HUD: a login item's
        # child is then the HUD itself, alive as long as it is, and launchd or systemd end with it.
        sys.stdout.flush()
        sys.stderr.flush()
        try:
            os.execvpe("bash", ["bash", host_script(), "run"], env)
        except OSError as e:
            raise Fail(f"cannot run {host_script()}: {e.strerror or e}")
    return subprocess.call(["bash", host_script(), verb], env=env)


def venv_ok():
    return os.path.exists(VENV_PYTHON)


def reexec_into_venv():
    """Re-enter under the venv's interpreter. The shim picks the venv when it exists, so this
    only fires when it was created after the shell resolved the command, or when cli.py was run
    directly with a system python."""
    if not venv_ok():
        raise Fail("no venv yet -- run `zmk-layer-hud setup` first")
    if os.path.realpath(sys.executable) != os.path.realpath(VENV_PYTHON):
        os.execv(VENV_PYTHON, [VENV_PYTHON, os.path.abspath(__file__)] + sys.argv[1:])


def python_for_setup():
    """An interpreter new enough to build the venv from. Apple's 3.9 is not one."""
    candidates = [os.environ.get("ZMKHUD_PYTHON"), "/opt/homebrew/bin/python3",
                  "python3.13", "python3.12", "python3.11", "python3.10", "python3"]
    for name in candidates:
        if not name:
            continue
        path = shutil.which(name)
        if not path:
            continue
        try:
            out = subprocess.check_output(
                [path, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
                text=True, stderr=subprocess.DEVNULL).strip()
            if tuple(int(n) for n in out.split(".")) >= PYTHON_MIN:
                return path, out
        except (subprocess.SubprocessError, ValueError):
            continue
    return None, None


# ---------- start / stop / status / log ----------

def definitions_missing():
    """Why the HUD cannot start for want of its definitions, or None: it draws from nothing
    else, and they are written by `zmk-layer-hud import`, never at start."""
    sys.path.insert(0, os.path.join(ROOT, "host"))
    import keymap as keymap_mod
    try:
        path = keymap_mod.find_config()
    except keymap_mod.KeymapError:
        return None                     # no config at all: the host script says so
    defs = keymap_mod.definitions_path(path)
    if os.path.isfile(defs):
        return None
    head = f"{path} has no definitions yet ({os.path.basename(defs)}), and the HUD draws from nothing else: "
    # A config that already says where to import from needs the bare verb, not a lecture on
    # sources. Read with a regex rather than a YAML parser: start runs before the venv.
    with open(path, encoding="utf-8") as f:
        names_keymap = re.search(r"^keymap:\s*\S", f.read(), re.M)
    if names_keymap or os.path.isfile(keymap_mod.imported_path(path)):
        return head + "run `zmk-layer-hud import`"
    return head + ("run `zmk-layer-hud import github.com/you/zmk-config` -- or, to draw from a keymap-drawer "
                   "YAML, name it as `keymap:` in the config and run `zmk-layer-hud import`")


def cmd_start(args):
    missing = definitions_missing()
    if missing:
        raise Fail(missing)
    return run_host("start", args.reserve, args.hidden, args.foreground)


def cmd_stop(args):
    return run_host("stop")


def cmd_restart(args):
    missing = definitions_missing()     # before the running HUD is stopped, not after
    if missing:
        raise Fail(missing)
    run_host("stop")
    return run_host("start", args.reserve, args.hidden)


def cmd_power(args):
    """Ctrl+Alt+Gui+L's verb: the HUD started, shown, when it is not running, and stopped when it is."""
    if panel_pids():
        return cmd_stop(args)
    args.reserve = args.hidden = args.foreground = False
    return cmd_start(args)


def pgrep(pattern):
    return subprocess.call(["pgrep", "-f", pattern],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0


# Either host's panel, run from this tree.
PANEL = re.escape(os.path.join(ROOT, "host")) + r"/.*/panel\.py"


def panel_pids():
    try:
        out = subprocess.run(["pgrep", "-f", PANEL], capture_output=True, text=True).stdout
    except OSError:
        return []
    return [int(p) for p in out.split() if p.isdigit()]


# ---------- show / hide ----------

def running_panel():
    """The live panel's own word on itself, {"pid", "shown"}, or a Fail that says why there is no
    panel to ask. Its pid has to be one of this tree's panels as well as alive: a pid outlives its
    process, and is handed out again."""
    st = panelstate.read(STATE)
    pids = panel_pids()
    if st and st["pid"] in pids:
        return st
    panelstate.discard(STATE)       # its panel is gone: left there, it says something untrue
    if pids:
        # A panel too old to know the signal would take it the default way, which is to end.
        raise Fail("the HUD that is running is older than show and hide, or still starting: "
                   "`zmk-layer-hud restart` once, and this works from then on")
    raise Fail("the HUD is not running: `zmk-layer-hud start`, or `start --hidden` to have it count off screen")


def ask_panel(shown, st=None):
    """What it is asked goes in panel.want, the signal says to read it, and the panel's own
    panel.json says when it has done it."""
    st = st or running_panel()
    panelstate.ask(STATE, shown)
    try:
        os.kill(st["pid"], panelstate.SIGNAL)
    except ProcessLookupError:
        raise Fail("the HUD went away just now: `zmk-layer-hud start`")
    deadline = time.monotonic() + 1.0
    while True:
        now = panelstate.read(STATE)
        if now and now["shown"] == shown:
            print("HUD shown" if shown else "HUD hidden -- it is counting; `zmk-layer-hud show` brings it back")
            return 0
        if time.monotonic() > deadline:
            raise Fail("the HUD did not answer; `zmk-layer-hud log` says what it is doing")
        time.sleep(0.05)


def cmd_show(args):
    return ask_panel(True)


def cmd_hide(args):
    return ask_panel(False)


def cmd_toggle(args):
    st = running_panel()
    return ask_panel(not st["shown"], st)


def feed_log_text():
    """Both logs, because where the feed writes depends on the host: the Linux panel starts it as
    a subprocess with its own hudfeed.log, the macOS one runs it in-process and it lands in
    panel.log with everything else."""
    text = ""
    for name in ("hudfeed.log", "panel.log"):
        try:
            with open(os.path.join(STATE, name), encoding="utf-8", errors="replace") as f:
                text += f.read()
        except OSError:
            pass
    return text


def last_matching(text, needle):
    hits = [line for line in text.splitlines() if needle in line]
    return hits[-1] if hits else ""


def hypr_surfaces():
    """The Linux panel draws three layer-shell surfaces, and an empty typed-keys strip is
    entirely transparent: "mapped with nothing on it" and "never mapped" look the same on screen.
    Only the compositor can tell them apart, so ask it."""
    import json
    want = ("zmkhud-layer", "zmkhud-reserved", "zmkhud-keys")
    try:
        out = subprocess.check_output(["hyprctl", "layers", "-j"], text=True,
                                      stderr=subprocess.DEVNULL)
        monitors = json.loads(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        return
    found = {}
    for monitor in monitors.values():
        for entries in (monitor.get("levels") or {}).values():
            for e in entries:
                if e.get("namespace") in want:
                    found[e["namespace"]] = e
    print("--- layer-shell surfaces ---")
    for ns in want:
        e = found.get(ns)
        if e:
            print("  %-16s %d,%d %dx%d" % (ns, e["x"], e["y"], e["w"], e["h"]))
        elif ns == "zmkhud-reserved":
            # Absent is the normal state: the HUD only takes room from other windows when asked.
            print("  %-16s overlay, nothing reserved (start with --reserve to tile beside it)" % ns)
        else:
            print("  %-16s missing" % ns)


def cmd_status(args):
    host_dir = re.escape(os.path.join(ROOT, "host"))
    pids = panel_pids()
    st = panelstate.read(STATE)
    said = st if st and st["pid"] in pids else None
    print("panel: " + ("stopped" if not pids else
                       "running, shown" if said and said["shown"] else
                       "running, hidden (zmk-layer-hud show)" if said else "running"))
    print("feed:  " + ("running" if pgrep(host_dir + r"/hudfeed\.py")
                       else "stopped (the macOS panel runs it in-process)"))
    # What it last managed to open says more than whether it is alive: the layer signal and the
    # typed-keys strip are separate grants and either can be the one that is missing.
    text = feed_log_text()
    if text:
        typed = last_matching(text, "reading what is typed on")
        refused = last_matching(text, "cannot read what is typed on")
        absent = last_matching(text, "no HID keyboard")
        if typed:
            print("keys:  reading " + typed.split("reading what is typed on ", 1)[-1])
        elif refused:
            print("keys:  refused -- " + refused.split("cannot read what is typed on ", 1)[-1])
        elif absent:
            print("keys:  " + absent.split("hudfeed: ", 1)[-1])
        else:
            print("keys:  nothing said yet (--no-hid-keys, or the feed has not scanned)")
        interesting = [l for l in text.splitlines() if re.search(
            r"reading|cannot open|cannot read|is not the layer signal|no HID keyboard", l)]
        if interesting:
            print("--- last from the feed ---")
            for line in interesting[-5:]:
                print(line)
    else:
        print(f"keys:  nothing has run yet (no logs in {STATE})")
    mod = sessions_mod()
    st, s = mod.peek()
    print("session: " + (f"{s['name']} · {session_line(s, mod)} · heatmap {st['heatmap']}" if s
                         else "none yet (the HUD starts one)"))
    if platform.system() in ("Darwin", "Linux"):
        print("login: " + login_line())
    if platform.system() == "Linux":
        hypr_surfaces()
    return 0


def cmd_log(args):
    logs = [os.path.join(STATE, n) for n in ("panel.log", "hudfeed.log")]
    if not any(os.path.exists(p) for p in logs):
        raise Fail(f"nothing has run yet (no logs in {STATE})")
    cmd = ["tail", "-n", str(args.lines)]
    if not args.no_follow:
        cmd.append("-F")
    os.execvp("tail", cmd + logs)


# ---------- the keymap ----------

def cmd_keymap(args):
    sys.path.insert(0, os.path.join(ROOT, "host"))
    import keymap as keymap_mod
    argv = []
    if args.config:
        argv += ["--config", args.config]
    if args.dump:
        argv.append("--dump")
    return keymap_mod.main(argv)


def cmd_sync(args, verb):
    """`import` and `sync` keep their own parser: its messages already say `zmk-layer-hud import`
    and they have been right all along, so the argv is reassembled rather than re-declared."""
    sys.path.insert(0, os.path.join(ROOT, "host"))
    import sync as sync_mod
    argv = [verb]
    if verb == "import":
        if args.source:
            argv.append(args.source)
        if args.keyboard:
            argv += ["--keyboard", args.keyboard]
        for flag in ("pristine", "drop_custom"):
            if getattr(args, flag):
                argv.append("--" + flag.replace("_", "-"))
        if args.keep_custom is True:
            argv.append("--keep-custom")
        elif args.keep_custom:
            if not re.fullmatch(r"[\d,\s]+", args.keep_custom):
                raise Fail(f"--keep-custom takes the numbers of the items to keep, like 1,3, not {args.keep_custom!r} "
                           "(a repo goes before it: `import REPO --pristine --keep-custom`)")
            argv.append(f"--keep-custom={args.keep_custom}")
    if args.config:
        argv += ["--config", args.config]
    if args.quiet:
        argv.append("--quiet")
    if args.no_fetch:
        argv.append("--no-fetch")
    if getattr(args, "watch", False):
        argv.append("--watch")
    return sync_mod.main(argv)


# ---------- passthrough ----------

PASSTHROUGH = {"poke": "hudpoke", "feed": "hudfeed"}


def passthrough(module_name, verb, rest):
    """`poke` and `feed` have nine and sixteen flags between them. Restating those here would
    only give them somewhere to drift apart, so each module keeps its own parser and is handed
    the rest of the line; argv[0] is borrowed so its usage says `zmk-layer-hud poke`."""
    import asyncio
    import importlib
    sys.path.insert(0, os.path.join(ROOT, "host"))
    mod = importlib.import_module(module_name)
    argv0, sys.argv[0] = sys.argv[0], f"zmk-layer-hud {verb}"
    try:
        parsed = mod.parse_args(rest)
    finally:
        sys.argv[0] = argv0
    try:
        asyncio.run(mod.main(parsed))
    except KeyboardInterrupt:
        pass
    return 0


def cmd_poke(args):
    return passthrough("hudpoke", "poke", args.rest)


def cmd_feed(args):
    return passthrough("hudfeed", "feed", args.rest)


# ---------- the typing ----------

def sessions_mod():
    """host/session.py: the standard library only, so these verbs need no venv."""
    sys.path.insert(0, os.path.join(ROOT, "host"))
    import session
    return session


def duration(ms):
    s = int(ms // 1000)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


def numbers_line(t):
    """A day's numbers (session.history), on one line."""
    parts = [f"{t['presses']:,} keys"]
    if t["combo_share"] is not None:
        parts.append(f"{round(t['combo_share'] * 100)}% combos")
    parts.append(f"{t['chars']:,} typed")
    if t["accuracy"] is not None:
        parts.append(f"{round(t['accuracy'] * 100)}% accurate")
    parts.append(duration(t["active_ms"]) + " of typing")
    if t["wpm"] is not None:
        parts.append(f"{t['wpm']} wpm")
    if t["peak_wpm"]:
        parts.append(f"top {t['peak_wpm']}")
    if t["sfb"] is not None:
        parts.append(f"{t['sfb'] * 100:.1f}% same-finger")
    return " · ".join(parts)


def layers_line(t):
    """Each layer's share of the keys, the most used first, on one line (`session history`)."""
    def share(v):
        p = v["share"] * 100
        return "<1%" if p < 1 else f"{round(p)}%"
    return " · ".join(f"{layer} {share(v)}" for layer, v in t["layers"].items())


def session_report(s, mod):
    """`session status`: every number the HUD's stats bar shows, from the session's counts, and
    every layer's share of the keys -- the layer tile's, for all of them at once."""
    t = mod.summary(s)
    first = [f"{t['presses']:,} keys"]
    first.append(f"{t['combos']:,} combos" +
                 (f" ({round(t['combo_share'] * 100)}% of keystrokes)" if t["combo_share"] is not None else ""))
    first += [f"{t['chars']:,} typed", f"{t['deleted']:,} deleted"]
    if t["accuracy"] is not None:
        first.append(f"{round(t['accuracy'] * 100)}% accurate")
    second = [duration(t["active_ms"]) + " of typing"]
    if t["wpm"] is not None:
        second.append(f"{t['wpm']} wpm")
    if t["peak_wpm"]:
        second.append(f"top {t['peak_wpm']}")
    if t["sfb"] is not None:
        second.append(f"{t['sfb'] * 100:.1f}% same-finger")
    lines = [" · ".join(first), " · ".join(second)]
    third = []
    if t["hands"]:
        third.append(f"hands {round(t['hands']['left'] * 100)}% left, {round(t['hands']['right'] * 100)}% right")
    if t["slowest"]:
        w = t["slowest"]
        third.append(f"slowest key {w['key'] or 'at position ' + w['pos']} on {w['layer']}, {w['ms']} ms")
    if third:
        lines.append(" · ".join(third))
    if t["layers"]:
        lines.append("layers, of the keys:")
        name_w = max(len(layer) for layer in t["layers"])
        keys_w = max(len(f"{v['keys']:,}") for v in t["layers"].values())
        # A layer barely used still was: it reads <0.1%, not 0.0%.
        share = lambda x: f"{x * 100:5.1f}%" if x * 100 >= 0.05 else "<0.1%"     # noqa: E731
        lines += [f"  {layer:<{name_w}}  {v['keys']:>{keys_w},} keys  {share(v['share']):>6}"
                  for layer, v in t["layers"].items()]
    return lines


def compare_rows(a, b, mod):
    """`session compare`: (what, A, B, the change from A to B) for two sessions side by side."""
    ta, tb = mod.summary(a), mod.summary(b)

    def count(x, y):
        return f"{(y - x) / x * 100:+.0f}%" if x else ""

    def share(x, y, digits=0):
        if x is None or y is None:
            return ""
        points = (y - x) * 100
        return f"{points:+.{digits}f} pt" + ("" if abs(round(points, digits)) == 1 else "s")

    pct = lambda v, digits=0: "—" if v is None else f"{v * 100:.{digits}f}%"   # noqa: E731
    num = lambda v: "—" if v is None else f"{v:,}"                               # noqa: E731
    rows = [
        ("keys", num(ta["presses"]), num(tb["presses"]), count(ta["presses"], tb["presses"])),
        ("combos", pct(ta["combo_share"]), pct(tb["combo_share"]), share(ta["combo_share"], tb["combo_share"])),
        ("typed", num(ta["chars"]), num(tb["chars"]), count(ta["chars"], tb["chars"])),
        ("accurate", pct(ta["accuracy"]), pct(tb["accuracy"]), share(ta["accuracy"], tb["accuracy"])),
        ("typing time", duration(ta["active_ms"]), duration(tb["active_ms"]), count(ta["active_ms"], tb["active_ms"])),
        ("wpm", num(ta["wpm"]), num(tb["wpm"]),
         f"{tb['wpm'] - ta['wpm']:+d}" if ta["wpm"] is not None and tb["wpm"] is not None else ""),
        ("top wpm", num(ta["peak_wpm"]), num(tb["peak_wpm"]),
         f"{tb['peak_wpm'] - ta['peak_wpm']:+d}" if ta["peak_wpm"] and tb["peak_wpm"] else ""),
        ("same finger", pct(ta["sfb"], 1), pct(tb["sfb"], 1), share(ta["sfb"], tb["sfb"], 1)),
    ]
    if ta["hands"] or tb["hands"]:
        left = [t["hands"]["left"] if t["hands"] else None for t in (ta, tb)]
        rows.append(("left hand", pct(left[0]), pct(left[1]), share(left[0], left[1])))
    if ta["slowest"] or tb["slowest"]:
        slow = ["—" if not t["slowest"] else f"{t['slowest']['key'] or t['slowest']['pos']} {t['slowest']['ms']} ms"
                for t in (ta, tb)]
        rows.append(("slowest key", slow[0], slow[1], ""))
    # Where the typing went: each layer's share of the keys, the layers B uses most first.
    shares = [{layer: v["share"] for layer, v in t["layers"].items()} for t in (ta, tb)]
    layers = sorted(set(shares[0]) | set(shares[1]), key=lambda l: (-shares[1].get(l, 0), -shares[0].get(l, 0), l))
    if layers:
        rows.append(("layers, of the keys", "", "", ""))
        of = lambda i, layer: shares[i].get(layer, 0) if shares[i] else None     # noqa: E731  (no keys: no share)
        rows += [(f"  {layer}", pct(of(0, layer)), pct(of(1, layer)), share(of(0, layer), of(1, layer)))
                 for layer in layers]
    return rows


def session_line(s, mod):
    t = mod.summary(s)
    parts = [f"{t['presses']:,} keys", f"{t['combos']:,} combos", f"{t['chars']:,} typed",
             f"{t['deleted']:,} deleted", duration(t["active_ms"]) + " of typing"]
    if t["wpm"] is not None:
        parts.append(f"{t['wpm']} wpm")
    if t["peak_wpm"]:
        parts.append(f"top {t['peak_wpm']}")
    if t["sfb"] is not None:
        parts.append(f"{t['sfb'] * 100:.1f}% same-finger")
    return " · ".join(parts)


def cmd_session(args):
    mod = sessions_mod()
    d = mod.default_dir()
    act, name = args.action, args.name
    if act in ("save", "load", "delete", "compare") and not name:
        raise Fail(f"`session {act}` needs the session's name")
    if act == "rename-layer" and not (name and args.other):
        raise Fail("`session rename-layer` needs the layer's old name and its new one")
    if args.other and act not in ("rename-layer", "compare"):
        raise Fail(f"`session {act}` takes {'one name' if act in ('new', 'save', 'load', 'delete', 'export', 'history') else 'no name'}")
    try:
        if act == "status":
            st, s = mod.status(d)
            print(f"{s['name']}{'' if s.get('named') else ' (not named yet: session save NAME)'}")
            for line in session_report(s, mod):
                print("  " + line)
            print(f"  heatmap {st['heatmap']} · {mod.path_of(d, s['name'])}")
            lost = mod.orphans(s)
            if lost:
                keymap = s.get("keymap") or "the keymap"
                print(f"  counts on layers {keymap} no longer has, which the HUD cannot show:")
                for layer, (presses, combos) in lost.items():
                    print(f"    {layer}: {presses:,} keys" + (f", {combos:,} combos" if combos else ""))
                print(f"  a layer that was renamed takes them along: "
                      f"zmk-layer-hud session rename-layer {next(iter(lost))} NEW")
                print(f"  ({keymap}'s layers: {', '.join(s['layers'])})")
        elif act == "rename-layer":
            for n, presses, combos in mod.rename_layer(d, name, args.other, every=args.all):
                print(f"{n}: {presses:,} keys" + (f" and {combos:,} combos" if combos else "") +
                      f" moved from {name} to {args.other}")
        elif act == "export":
            return session_export(args, mod, d)
        elif act == "compare":
            every = mod.sessions(d)
            _, current = mod.status(d)
            second = args.other or current["name"]
            for n in (name, second):
                if n not in every:
                    raise Fail(f"there is no session called {n}; `zmk-layer-hud session list` shows them")
            if name == second:
                raise Fail(f"{name} is the active session; name the one to compare it with")
            rows = [("", name, second, "")] + compare_rows(every[name], every[second], mod)
            widths = [max(len(r[i]) for r in rows) for i in range(3)]
            for what, x, y, change in rows:
                print(f"{what:<{widths[0]}}  {x:>{widths[1]}}  {y:>{widths[2]}}  {change}".rstrip())
        elif act == "history":
            every = mod.sessions(d)
            if args.all:
                title, s = "every session", mod.all_days(every.values())
            elif name:
                if name not in every:
                    raise Fail(f"there is no session called {name}; `zmk-layer-hud session list` shows them")
                title, s = name, every[name]
            else:
                _, s = mod.status(d)
                title = s["name"]
            days = mod.history(s)
            if not days:
                print(f"{title}: no day recorded yet (a session keeps its days from this version on)")
                return 0
            print(f"{title}, by day:")
            for day, t in days:
                weekday = datetime.date.fromisoformat(day).strftime("%a")
                print(f"  {day} {weekday}  {numbers_line(t)}")
                if t["layers"]:
                    print(f"  {' ' * len(day)}      layers: {layers_line(t)}")
        elif act == "list":
            st, _ = mod.status(d)
            every = mod.sessions(d)
            for n in sorted(every, key=lambda n: every[n].get("updated", ""), reverse=True):
                mark = "*" if n == st.get("active") else " "
                print(f"{mark} {n:<24} {session_line(every[n], mod)}   (updated {every[n].get('updated', '?')})")
        elif act == "new":
            s = mod.new(d, name)
            print(f"started {s['name']}; typing counts there now")
        elif act == "save":
            s = mod.save(d, name)
            print(f"the active session is {s['name']}")
        elif act == "load":
            s = mod.load(d, name)
            print(f"loaded {s['name']}; typing adds to it from now on")
        elif act == "reset":
            _, s = mod.status(d)
            if not confirm(f"zero every count in {s['name']}?", args):
                print("nothing was reset")
                return 1
            mod.reset(d)
            print(f"{s['name']} is empty again")
        elif act == "delete":
            if not confirm(f"delete the session {name}?", args):
                print("nothing was deleted")
                return 1
            mod.delete(d, name)
            print(f"deleted {name}")
    except mod.SessionError as e:
        raise Fail(str(e))
    return 0


def session_export(args, mod, d):
    """`session export`: the session's heatmap, drawn by keymap-drawer from the HUD's definitions
    (host/export.py). Runs in the venv (needs_venv)."""
    sys.path.insert(0, os.path.join(ROOT, "host"))
    import export as export_mod
    import keymap as keymap_mod
    st, s = mod.status(d)
    if args.name:
        s = mod.sessions(d).get(args.name)
        if s is None:
            raise Fail(f"there is no session called {args.name}; `zmk-layer-hud session list` shows them")
    try:
        src = keymap_mod.KeymapSource(args.config)
        msg = src.load()
    except keymap_mod.KeymapError as e:
        raise Fail(str(e))
    # In the keys the HUD is in: the theme chosen on this machine, else the config's hud.dark.
    dark = st["theme"] == "dark" if st.get("theme") else bool((msg.get("hud") or {}).get("dark"))
    t = mod.summary(s)
    footer = f"{s['name']} · {args.mode} · {t['presses']:,} keys" + (f" · {t['wpm']} wpm" if t["wpm"] else "")
    layers = [x.strip() for x in args.layers.split(",") if x.strip()] if args.layers else None
    try:
        text = export_mod.svg(s, msg, src.definitions, mode=args.mode, layers=layers, footer=footer, dark=dark)
    except ValueError as e:
        raise Fail(str(e))
    except Exception as e:   # keymap-drawer's own checks: a layout it cannot build, a glyph it cannot fetch
        raise Fail(f"keymap-drawer could not draw it: {type(e).__name__}: {e}")
    out = args.output or f"{s['name']}-{args.mode}.svg"
    if out == "-":
        sys.stdout.write(text)
        return 0
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"wrote {out}: {s['name']}'s {args.mode} heatmap")
    return 0


def needs_venv(args):
    """A verb that runs in the venv; `session export` does, where the rest of `session` must not."""
    return args.cmd in NEEDS_VENV or (args.cmd == "session" and args.action == "export")


def cmd_heatmap(args):
    mod = sessions_mod()
    d = mod.default_dir()
    try:
        if args.mode is None:
            st, _ = mod.status(d)
            print(st["heatmap"])
        else:
            mod.set_heatmap(d, args.mode)
            print(f"heatmap {args.mode}")
    except mod.SessionError as e:
        raise Fail(str(e))
    return 0


# ---------- the config ----------

def cmd_config(args):
    if args.action == "path":
        print(CONFIG)
        return 0
    if args.action == "show":
        if not os.path.isfile(CONFIG):
            raise Fail(f"no config at {CONFIG}; run `zmk-layer-hud setup`")
        with open(CONFIG, encoding="utf-8") as f:
            sys.stdout.write(f.read())
        return 0
    if args.action == "edit":
        if not os.path.isfile(CONFIG):
            raise Fail(f"no config at {CONFIG}; run `zmk-layer-hud setup`")
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
        return subprocess.call([editor, CONFIG])
    return config_link(args)


def config_link(args):
    """For a config kept in a repo rather than one started from example.yaml: link it instead of
    copying it, so `git pull` is the whole of syncing a machine.

    Its definitions are linked with it, and its imported record, and that is not a convenience:
    `<config>.definitions.json` is found beside the config *path*, not beside whatever that path
    points at, so linking only config.yaml would leave the machine's stale definitions in play --
    a drawing of another keymap, under a config written for this one."""
    src = os.path.abspath(os.path.expanduser(args.file))
    if not os.path.isfile(src):
        raise Fail(f"no such file: {args.file}")
    os.makedirs(CONFIG_DIR, exist_ok=True)
    stem = os.path.splitext(src)[0]
    for source, dest in ((src, CONFIG),
                         (stem + ".definitions.json", os.path.join(CONFIG_DIR, "config.definitions.json")),
                         (stem + ".imported.yaml", os.path.join(CONFIG_DIR, "config.imported.yaml"))):
        if not os.path.exists(source):
            print(f"    no {source}, skipped")
            continue
        if os.path.exists(dest) and not os.path.islink(dest):
            os.rename(dest, dest + ".bak")
            print(f"    kept yours as {dest}.bak")
        if os.path.islink(dest) or os.path.exists(dest):
            os.unlink(dest)
        os.symlink(source, dest)
        print(f"    {dest} -> {source}")
    return 0


# ---------- the sample board ----------

def demo_urls(port, ws_port):
    """The page the demo opens, and the socket that feeds it (which `poke --url` takes)."""
    ws = f"ws://127.0.0.1:{ws_port}"
    return f"http://127.0.0.1:{port}/index.html?ws={ws}", ws


def cmd_demo(args):
    """The HUD's pages against a sample keymap, with no keyboard: what `examples/` is for. They are
    served over http and fed by a socket of their own, the way the Linux panel's pages are, so
    `zmk-layer-hud poke --url` drives them, the page's own API (`hud.setLayers([1])`,
    `hud.pressAt(13)`) does from the browser console, and with --play a demo script is typed on
    them in real time (host/play.py). There is no session behind the demo: the bar and the
    session heatmap count what the page is shown, for as long as it is open."""
    import asyncio
    import functools
    import http.server
    import io
    import json
    import threading

    sys.path.insert(0, os.path.join(ROOT, "host"))
    import keymap as keymap_mod
    import play as play_mod

    if args.keymap:
        with open(args.keymap, encoding="utf-8") as f:
            message = json.load(f)
    else:
        config = args.config or os.path.join(ROOT, "config", "example-3x5.yaml")
        print(f"==> keymap from {config}")
        buf = io.StringIO()
        stdout, sys.stdout = sys.stdout, buf
        try:
            rc = keymap_mod.main(["--config", config, "--dump"])
        finally:
            sys.stdout = stdout
        if rc:
            return rc
        message = json.loads(buf.getvalue())

    compiled = None
    if args.play:
        try:
            script = play_mod.load(args.play)
            compiled = play_mod.compile(script, message, speed=args.speed)
        except play_mod.PlayError as e:
            raise Fail(str(e))
        for problem in compiled.problems:
            warn(play_mod.describe(problem, args.play))
        if args.strict and any(p["level"] == "skip" for p in compiled.problems):
            raise Fail(f"{args.play} types what this keymap cannot (--strict)")
        if "opacity" in script:   # the keymap cannot be sent in, so the demo's own carries it
            message = dict(message, hud=dict(message.get("hud") or {}, opacity=int(script["opacity"])))

    class Quiet(http.server.SimpleHTTPRequestHandler):
        """No request log; and never cached, or a page edited since opens as it was."""
        def log_message(self, *a):
            pass

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

    try:
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", args.port),
                                                functools.partial(Quiet, directory=os.path.join(ROOT, "hud")))
    except OSError as e:
        raise Fail(f"cannot serve on port {args.port}: {e}. Something else is on it -- pass --port to pick another.")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        asyncio.run(demo_socket(args, message, compiled, play_mod))
    except KeyboardInterrupt:
        print()
    finally:
        httpd.shutdown()
    return 0


async def demo_socket(args, message, compiled, play_mod):
    import asyncio
    import webbrowser
    import hudfeed
    try:
        import websockets
    except ImportError:
        raise Fail("python-websockets is not in the venv: zmk-layer-hud setup")
    page, ws_url = demo_urls(args.port, args.ws_port)
    hub = hudfeed.Hub()
    await hub.send(message)   # kept, and replayed to the page when it connects
    try:
        server = await websockets.serve(hub.handler, "127.0.0.1", args.ws_port, max_size=None)
    except OSError as e:
        raise Fail(f"cannot open the demo's socket on port {args.ws_port}: {e}. Pass --ws-port to pick another.")
    async with server:
        print(f"==> {page}")
        print(f"    zmk-layer-hud poke --url {ws_url} --type hello   types on it; ^C to stop")
        if not args.no_browser:
            asyncio.get_running_loop().run_in_executor(None, webbrowser.open, page)
        if compiled is not None:
            # Keys are not kept for a page that is not there yet: wait for it, and for its keymap.
            while not hub.clients:
                await asyncio.sleep(0.1)
            await asyncio.sleep(1.0)
            again = args.loop or compiled.loop
            print(f"==> playing {args.play}" + (", again and again" if again else ""))
            await play_mod.play(compiled.timeline, hub.send_in, loop=again, duration_ms=compiled.duration_ms)
            print("    played; the page stays up until ^C")
        await asyncio.Event().wait()


# ---------- this machine ----------

def confirm(prompt, args):
    if getattr(args, "yes", False):
        return True
    if not sys.stdin.isatty():
        return False
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def privileged(cmd, args):
    """Print it, then ask. A piped `curl | sh` must never acquire root without being asked, so no
    tty means print-only -- install.sh reopens /dev/tty where there is one."""
    print("    sudo " + " ".join(cmd))
    if getattr(args, "no_sudo", False):
        return False
    if not sys.stdin.isatty():
        print("    not a terminal, so nothing privileged was run.")
        print("    run the line above, or re-run `zmk-layer-hud setup` from a terminal.")
        return False
    if not confirm("    run it?", args):
        print("    skipped")
        return False
    return subprocess.call(["sudo"] + cmd) == 0


def make_venv(args):
    python, version = python_for_setup()
    if not python:
        raise Fail(f"need Python >= {PYTHON_MIN[0]}.{PYTHON_MIN[1]} for keymap-drawer, and none "
                   f"was found. macOS: brew install python. Arch: sudo pacman -S python. "
                   f"Or set ZMKHUD_PYTHON to one.")
    venv_dir = os.path.join(ROOT, ".venv")
    print(f"==> venv ({python}, Python {version})")
    subprocess.check_call([python, "-m", "venv", "--clear", venv_dir])
    subprocess.check_call([VENV_PYTHON, "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
    subprocess.check_call([VENV_PYTHON, "-m", "pip", "install", "--quiet"] + VENV_PKGS)
    print(f"    ready: {VENV_PYTHON}")


def setup_darwin(args):
    print("==> hidapi (the Python wheel links against it)")
    if not shutil.which("brew"):
        raise Fail("Homebrew is needed for hidapi: https://brew.sh")
    if subprocess.call(["brew", "list", "hidapi"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) != 0:
        subprocess.check_call(["brew", "install", "hidapi"])
    else:
        print("    already installed")
    make_venv(args)
    print()
    print("==> one thing this cannot do for you")
    print("    The typed-keys strip reads the keyboard's HID reports, which macOS gates behind")
    print("    Input Monitoring. Grant it to whatever you start the HUD from -- your terminal, or")
    print("    Hammerspoon -- in System Settings > Privacy & Security > Input Monitoring, and")
    print("    untick the keyboard under Karabiner-Elements > Devices if you run it, because a")
    print("    keyboard whose events it modifies is seized and we get no reports.")
    print("    Layers and positions need none of that; --no-hid-keys drops the strip and the grant.")


def setup_linux(args):
    print("==> system packages (Arch/Omarchy; other distros: the same three by their own names)")
    # python-cairo: a hidden HUD takes no clicks through an empty cairo input region.
    pkgs = ["python-gobject", "python-cairo", "webkit2gtk-4.1", "gtk-layer-shell"]
    if shutil.which("pacman"):
        privileged(["pacman", "-S", "--needed"] + pkgs, args)
    else:
        print("    no pacman here: install " + ", ".join(pkgs) + " yourself")
    make_venv(args)
    print()
    print("==> udev rule: the tty for the layer signal, hidraw for the typed-keys strip")
    rule = os.path.join(ROOT, "contrib", "udev", "60-zmk-layer-hud.rules")
    if privileged(["cp", rule, "/etc/udev/rules.d/"], args):
        privileged(["udevadm", "control", "--reload-rules"], args)
        privileged(["udevadm", "trigger"], args)
        print("    installed; replug the keyboard (a rule applies to nodes created after it)")


def setup_config():
    print()
    print("==> config")
    if os.path.isfile(CONFIG):
        print(f"    {CONFIG} is yours already, left alone")
        return
    os.makedirs(CONFIG_DIR, exist_ok=True)
    shutil.copy(os.path.join(ROOT, "config", "example.yaml"), CONFIG)
    print(f"    wrote {CONFIG} from config/example.yaml")
    print("    then: zmk-layer-hud import github.com/you/zmk-config   (the HUD draws from what import writes)")


def link_command():
    target = os.path.join(ROOT, "bin", "zmk-layer-hud")
    # Installing is the first thing anyone runs, so a directory that cannot be written or a link
    # that cannot be replaced has to say so and say what to do, not raise.
    try:
        os.makedirs(BIN_DIR, exist_ok=True)
        # A tarball carries whatever mode the archive held, and a file written by hand may carry
        # none; the command is useless without the bit, so set it rather than assume it.
        os.chmod(target, 0o755)
        if os.path.islink(BIN_LINK) or os.path.exists(BIN_LINK):
            os.unlink(BIN_LINK)
        os.symlink(target, BIN_LINK)
    except OSError as e:
        raise Fail(f"cannot put the command in {BIN_DIR} ({e.strerror}). Point it somewhere you "
                   f"can write with ZMKHUD_BIN_DIR, or link it yourself:\n"
                   f"    ln -sf {target} <a directory on your PATH>/zmk-layer-hud")
    print(f"    {BIN_LINK} -> {target}")
    if BIN_DIR not in os.environ.get("PATH", "").split(os.pathsep):
        shell = os.path.basename(os.environ.get("SHELL", "") or "")
        rc = {"zsh": "~/.zshrc", "bash": "~/.bashrc", "fish": "~/.config/fish/config.fish"}.get(
            shell, "your shell's startup file")
        print()
        print(f"    {BIN_DIR} is not on your PATH. Add it to {rc}:")
        if shell == "fish":
            print(f"        fish_add_path {BIN_DIR}")
        else:
            print(f'        export PATH="{BIN_DIR}:$PATH"')


def cmd_setup(args):
    if args.venv_only:
        make_venv(args)
        return 0
    if args.link_only:
        args.link = True
    if args.link:
        print("==> command")
        link_command()
        if args.link_only:
            return 0
    system = platform.system()
    if system == "Darwin":
        setup_darwin(args)
    elif system == "Linux":
        setup_linux(args)
    else:
        raise Fail(f"no HUD host for {system}; macOS and Linux (Hyprland) only")
    setup_config()
    print()
    print("ready: zmk-layer-hud start   (and `zmk-layer-hud doctor` if it does not come up)")
    return 0


# ---------- the menubar icon ----------

# The Omarchy bar plugin. Omarchy wants a third-party id as <author>.<name>, with the plugin's
# directory named after it; the first version used APP_ID, and is taken out again on the way in.
OMARCHY_ID = "rafaelromao.zmk-layer-hud"
OLD_OMARCHY_IDS = (APP_ID,)
SHELL_JSON = os.path.expanduser("~/.config/omarchy/shell.json")


def plugin_dir(pid=OMARCHY_ID):
    return os.path.join(OMARCHY_PLUGINS, pid)


def omarchy(*argv):
    """One of Omarchy's own commands, and whether it worked. With OMARCHY_PATH, which omarchy-shell
    needs and a terminal outside the desktop's session may not have."""
    env = dict(os.environ)
    env.setdefault("OMARCHY_PATH", os.path.expanduser("~/.local/share/omarchy"))
    try:
        return subprocess.call(list(argv), env=env, timeout=15) == 0
    except (OSError, subprocess.SubprocessError):
        return False


def rescan_plugins():
    """The shell finds a new plugin folder only on a rescan. Through omarchy-shell, else through
    Quickshell's own IPC to the instance Omarchy runs, which is all omarchy-shell does."""
    if shutil.which("omarchy-shell") and omarchy("omarchy-shell", "shell", "rescanPlugins"):
        return True
    shell = os.path.join(os.environ.get("OMARCHY_PATH") or os.path.expanduser("~/.local/share/omarchy"), "shell")
    return bool(shutil.which("qs")) and omarchy("qs", "ipc", "-n", "-p", shell, "call", "--", "shell", "rescanPlugins")


def install_plugin():
    """host/linux/omarchy copied where Omarchy looks for plugins, as regular files (its validator
    refuses links), with the command and the state directory written into the widget: the shell
    does not start commands through a login shell, so neither PATH nor XDG_STATE_HOME can be
    counted on."""
    dest = plugin_dir()
    staged = dest + ".new"
    shutil.rmtree(staged, ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, "host", "linux", "omarchy"), staged)
    widget = os.path.join(staged, "BarWidget.qml")
    with open(widget, encoding="utf-8") as f:
        qml = f.read()
    for mark, value in (("__ZMK_LAYER_HUD_COMMAND__", os.path.join(ROOT, "bin", "zmk-layer-hud")),
                        ("__ZMK_LAYER_HUD_STATE__", STATE),
                        # A HUD the bar starts would live in the shell's cgroup, and go when it restarts.
                        ("__ZMK_LAYER_HUD_START_WITH__", "uwsm-app --" if shutil.which("uwsm-app") else "")):
        qml = qml.replace(mark, value.replace("\\", "\\\\").replace('"', '\\"'))
    with open(widget, "w", encoding="utf-8") as f:
        f.write(qml)
    shutil.rmtree(dest, ignore_errors=True)
    os.rename(staged, dest)
    return dest


def bar_has(cfg, pid):
    """Whether a shell.json puts the widget in the bar (or lists it among its plugins)."""
    is_it = lambda e: e == pid or (isinstance(e, dict) and e.get("id") == pid)
    layout = ((cfg.get("bar") or {}).get("layout") or {}) if isinstance(cfg, dict) else {}
    return any(is_it(e) for section in layout.values() if isinstance(section, list) for e in section) or \
        any(is_it(e) for e in (cfg.get("plugins") or []) if isinstance(cfg, dict))


def right_array_start(text):
    """The offset just past the '[' that opens bar.layout.right, by walking the JSON's tokens with
    the key that leads to each nested value; None when there is none."""
    import json
    stack = []

    def value_done():
        if stack and stack[-1]["obj"]:
            stack[-1]["want_key"] = True
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            word = json.loads(text[i:j + 1])
            i = j + 1
            if stack and stack[-1]["obj"] and stack[-1]["want_key"]:
                stack[-1]["key"], stack[-1]["want_key"] = word, False
            else:
                value_done()
            continue
        if c == "[":
            if len(stack) == 3 and [f["key"] for f in stack] == ["bar", "layout", "right"]:
                return i + 1
            stack.append({"obj": False, "want_key": False, "key": None})
        elif c == "{":
            stack.append({"obj": True, "want_key": True, "key": None})
        elif c in "]}":
            if not stack:
                return None
            stack.pop()
            value_done()
        elif c not in ",:" and not c.isspace():
            while i < n and text[i] not in ",]} \t\r\n":
                i += 1
            value_done()
            continue
        i += 1
    return None


def add_to_bar(path, pid):
    """List {"id": pid} first in bar.layout.right of shell.json -- being in the layout is what
    enables a third-party widget -- and say whether it had to. Edited as text, in the indentation
    the file already has, so the rest of a file people edit by hand stays as it was; the original
    is kept beside it."""
    import json
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    cfg = json.loads(raw)
    if bar_has(cfg, pid):
        return False
    out = None
    at = right_array_start(raw)
    if at is not None:
        rest = raw[at:]
        nxt = rest.lstrip()
        ws = rest[:len(rest) - len(nxt)]
        if "\n" in ws and nxt and nxt[0] != "]":
            ind = ws[ws.rfind("\n") + 1:]
            line = raw[raw.rfind("\n", 0, at) + 1:at]
            lead = line[:len(line) - len(line.lstrip(" \t"))]
            unit = ind[len(lead):] if ind.startswith(lead) and len(ind) > len(lead) else "  "
            out = raw[:at] + ws + "{\n" + ind + unit + f'"id": {json.dumps(pid)}\n' + ind + "}," + rest
    if out is None or not bar_has(json.loads(out), pid):
        # An empty or one-line right section: written again whole, the widget first in it.
        cfg.setdefault("bar", {}).setdefault("layout", {}).setdefault("right", []).insert(0, {"id": pid})
        out = json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"
    with open(path + ".bak-zmk-layer-hud", "w", encoding="utf-8") as f:
        f.write(raw)
    with open(path, "w", encoding="utf-8") as f:      # in place: a shell.json that is a link stays one
        f.write(out)
    return True


def take_out_of_bar(path, pid):
    """Undo add_to_bar, and whatever `omarchy plugin enable` put in for it: say whether it had to."""
    import json
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        cfg = json.loads(raw)
    except (OSError, ValueError):
        return False
    if not bar_has(cfg, pid):
        return False
    entry = r'\{\s*"id"\s*:\s*' + re.escape(json.dumps(pid)) + r'\s*\}'
    out = re.sub(entry + r"\s*,\s*", "", raw, count=1)
    if out == raw:
        out = re.sub(r",\s*" + entry, "", raw, count=1)
    try:
        still = bar_has(json.loads(out), pid)
    except ValueError:
        still = True
    if still:
        keep = lambda e: not (e == pid or (isinstance(e, dict) and e.get("id") == pid))
        for name, section in ((cfg.get("bar") or {}).get("layout") or {}).items():
            if isinstance(section, list):
                cfg["bar"]["layout"][name] = [e for e in section if keep(e)]
        if isinstance(cfg.get("plugins"), list):
            cfg["plugins"] = [e for e in cfg["plugins"] if keep(e)]
        out = json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
    return True


def remove_plugin(pid=OMARCHY_ID):
    """Out of the bar and off the disk; whether there was anything to take."""
    had = take_out_of_bar(SHELL_JSON, pid)
    if os.path.isdir(plugin_dir(pid)):
        shutil.rmtree(plugin_dir(pid), ignore_errors=True)
        had = True
    return had


MENUBAR_OFF = os.path.join(CONFIG_DIR, "menubar-off")
MENUBAR = os.path.join(ROOT, "host", "macos", "menubar.py")


def macos_menubar(action):
    """macOS's icon is host/macos/menubar.py, a process of its own that the host script starts with
    the HUD and that stays when the HUD quits. `disable` stops it and keeps it from coming back."""
    running = pgrep(re.escape(MENUBAR))
    if action == "status":
        print("the menubar icon is " + ("off (zmk-layer-hud menubar enable)" if os.path.exists(MENUBAR_OFF) else
                                        "on" + ("" if running else ", and comes up with the next `zmk-layer-hud start`")))
        return 0
    if action == "disable":
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(MENUBAR_OFF, "w", encoding="utf-8") as f:
            f.write("zmk-layer-hud menubar enable brings the icon back\n")
        subprocess.call(["pkill", "-f", re.escape(MENUBAR)], stderr=subprocess.DEVNULL)
        print("the menubar icon is off, and stays off; a hidden HUD comes back with `zmk-layer-hud show`")
        return 0
    if os.path.exists(MENUBAR_OFF):
        os.remove(MENUBAR_OFF)
    if not running:
        if not venv_ok():
            raise Fail("no venv yet -- run `zmk-layer-hud setup` first")
        os.makedirs(STATE, exist_ok=True)
        with open(os.path.join(STATE, "menubar.log"), "w") as log:
            subprocess.Popen([VENV_PYTHON, "-u", MENUBAR], stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                             env=dict(os.environ, ZMKHUD_STATE=STATE), start_new_session=True)
    print("the menubar icon is on; it stays when the HUD quits, and a click starts it again")
    return 0


def cmd_menubar(args):
    import json
    if platform.system() == "Darwin":
        return macos_menubar(args.action)
    installed = os.path.isfile(os.path.join(plugin_dir(), "manifest.json"))
    if args.action == "status":
        try:
            with open(SHELL_JSON, encoding="utf-8") as f:
                placed = bar_has(json.load(f), OMARCHY_ID)
        except (OSError, ValueError):
            placed = False
        print("the Omarchy bar plugin is " + ("not installed (zmk-layer-hud menubar enable)" if not installed else
                                               f"in {plugin_dir()}" + ("" if placed else f", but not in the bar of {SHELL_JSON}")))
        return 0
    if args.action == "disable":
        removed = remove_plugin()
        rescan_plugins()
        print("the HUD's icon is out of Omarchy's bar" if removed else "the Omarchy bar plugin was not installed")
        return 0
    if not (os.path.isfile(SHELL_JSON) or shutil.which("omarchy-shell")):
        raise Fail("the icon is a plugin for Omarchy's own bar (Omarchy 4), and there is none here; "
                   "any other bar can run `zmk-layer-hud toggle` on a click")
    for old in OLD_OMARCHY_IDS:
        if remove_plugin(old):
            print(f"    took out the plugin's old id, {old}")
    dest = install_plugin()
    print(f"    {dest}")
    if shutil.which("omarchy"):
        print("    omarchy plugin validate: " + ("accepts it" if omarchy("omarchy", "plugin", "validate", dest)
                                                 else "refuses it (it says why above)"))
    rescanned = rescan_plugins()
    if not os.path.isfile(SHELL_JSON):
        # Without a shell.json of its own the shell runs Omarchy's default, which it does not merge a
        # partial file into: writing one with only this widget would take every other one away.
        print(f"no {SHELL_JSON} yet; put the icon in the bar with:")
        print(f"    omarchy plugin enable {OMARCHY_ID}")
        return 0
    added = add_to_bar(SHELL_JSON, OMARCHY_ID)
    print(("the HUD's icon is first in bar.layout.right of " if added else "the bar already has it, in ") + SHELL_JSON +
          (f" (the old one is {SHELL_JSON}.bak-zmk-layer-hud)" if added else ""))
    if not rescanned:
        print("the shell could not be asked to look for it; `omarchy-restart-shell` makes it")
    else:
        print("if the bar does not show it, `omarchy-restart-shell`: the shell caches the QML it has loaded")
    return 0


# ---------- starting at login ----------

def login_command():
    return [os.path.join(ROOT, "bin", "zmk-layer-hud"), "start", "--hidden", "--foreground"]


def login_env():
    """What the login item is started with that this shell knows and launchd or systemd may not:
    where the state is (so `show` finds the HUD it started) and which config, if not the default."""
    env = {"ZMKHUD_STATE": STATE}
    if os.environ.get("ZMKHUD_CONFIG"):
        env["ZMKHUD_CONFIG"] = os.path.abspath(os.path.expanduser(os.environ["ZMKHUD_CONFIG"]))
    return env


def launcher_path():
    return os.path.join(LOGIN_APP, "Contents", "MacOS", "zmk-layer-hud")


def launch_agent_plist():
    import plistlib
    return plistlib.dumps({
        "Label": APP_ID,
        "ProgramArguments": [launcher_path()] + login_command(),
        "EnvironmentVariables": login_env(),
        "RunAtLoad": True,
        "LimitLoadToSessionType": "Aqua",       # a login with a screen, not an ssh session
        "ProcessType": "Interactive",
        # The menubar icon it starts (host/macos/menubar.py) stays when the HUD quits; launchd would
        # otherwise end it with the job.
        "AbandonProcessGroup": True,
        "AssociatedBundleIdentifiers": [APP_ID],   # System Settings shows it as the app's
        "StandardOutPath": os.path.join(STATE, "autostart.log"),
        "StandardErrorPath": os.path.join(STATE, "autostart.log"),
    })


def desktop_arg(arg):
    """One argument of a desktop entry's Exec, quoted the way the spec says: % doubled, quoted when
    it has a reserved character, and every backslash escaped once more as a string value's."""
    arg = arg.replace("%", "%%")
    if re.search(r"[\s\"'\\><~|&;$*?#()`]", arg):
        arg = '"' + re.sub(r'(["`$\\])', r"\\\1", arg) + '"'
    return arg.replace("\\", "\\\\")


def autostart_desktop():
    argv = ["env"] + [f"{k}={v}" for k, v in login_env().items()] + login_command()
    return ("[Desktop Entry]\n"
            "Type=Application\n"
            "Name=ZMK layer HUD\n"
            "Comment=Starts the HUD hidden at login, so it counts from the first keystroke\n"
            f"Exec={' '.join(desktop_arg(a) for a in argv)}\n"
            "Terminal=false\n")


def build_launcher():
    """ZMK Layer HUD.app, with host/macos/launcher.c as its executable (why: that file says).
    Built again only when that source has changed: a new binary is a new identity to macOS, which
    forgets the Input Monitoring and Bluetooth grants the old one had."""
    import hashlib
    import plistlib
    src = os.path.join(ROOT, "host", "macos", "launcher.c")
    with open(src, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    info = os.path.join(LOGIN_APP, "Contents", "Info.plist")
    try:
        with open(info, "rb") as f:
            if plistlib.load(f).get("ZMKHUDLauncherSource") == digest and os.access(launcher_path(), os.X_OK):
                return False
    except (OSError, ValueError, plistlib.InvalidFileException):
        pass
    # /usr/bin/cc is there without the Command Line Tools too, and only offers to install them.
    if subprocess.call(["xcode-select", "-p"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) != 0:
        raise Fail("the login item's launcher is compiled here, and there are no Command Line Tools: "
                   "xcode-select --install, then this again")
    os.makedirs(os.path.dirname(launcher_path()), exist_ok=True)
    if subprocess.call(["cc", "-O2", "-Wall", "-o", launcher_path(), src]) != 0:
        raise Fail(f"could not compile {src}")
    with open(info, "wb") as f:
        plistlib.dump({
            "CFBundleIdentifier": APP_ID,
            "CFBundleName": "ZMK Layer HUD",
            "CFBundleDisplayName": "ZMK Layer HUD",
            "CFBundleExecutable": "zmk-layer-hud",
            "CFBundlePackageType": "APPL",
            "CFBundleVersion": "1",
            "CFBundleShortVersionString": "1.0",
            "LSUIElement": True,
            "NSBluetoothAlwaysUsageDescription": "The HUD reads which layer your Bluetooth keyboard is on.",
            "ZMKHUDLauncherSource": digest,
        }, f)
    if subprocess.call(["codesign", "--force", "-s", "-", "--identifier", APP_ID, LOGIN_APP],
                       stdout=subprocess.DEVNULL) != 0:
        raise Fail(f"could not sign {LOGIN_APP}")
    return True


def login_entry():
    return LAUNCH_AGENT if platform.system() == "Darwin" else AUTOSTART_DESKTOP


def login_line():
    """Whether the HUD starts at login, and from which tree."""
    path = login_entry()
    if not os.path.isfile(path):
        return "off (zmk-layer-hud autostart enable starts it hidden at login)"
    with open(path, encoding="utf-8", errors="replace") as f:
        here = os.path.join(ROOT, "bin", "zmk-layer-hud") in f.read()
    return "starts it hidden" + ("" if here else f" -- from another tree; `zmk-layer-hud autostart enable` points it here") \
        + f" ({path})"


def cmd_autostart(args):
    system = platform.system()
    if system not in ("Darwin", "Linux"):
        raise Fail(f"no HUD host for {system}; macOS and Linux (Hyprland) only")
    if args.action == "status":
        print("login: " + login_line())
        return 0
    path = login_entry()
    if args.action == "disable":
        existed = os.path.isfile(path)
        if existed:
            os.remove(path)
        print("the HUD will not start at login" + ("" if existed else " (it was not set to)") +
              "; one running now goes on running")
        if system == "Darwin" and os.path.isdir(LOGIN_APP):
            print(f"    {LOGIN_APP} stays, and with it what macOS lets it do; `uninstall` removes it")
        return 0
    missing = definitions_missing()
    if missing:
        warn("note: it cannot start yet -- " + missing)
    os.makedirs(STATE, exist_ok=True)       # launchd will not make the log's directory
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if system == "Darwin":
        if build_launcher():
            print(f"    built {LOGIN_APP}")
        with open(path, "wb") as f:
            f.write(launch_agent_plist())
        print(f"    {path}")
        print("the HUD starts hidden at your next login. The first time, macOS asks for Input Monitoring and")
        print("Bluetooth for \"ZMK Layer HUD\" (System Settings > Privacy & Security). To start it that way now:")
        print(f"    launchctl bootstrap gui/{os.getuid()} {path}")
        print(f"and to start it again once those are granted:  launchctl kickstart -k gui/{os.getuid()}/{APP_ID}")
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(autostart_desktop())
        print(f"    {path}")
        print("the HUD starts hidden at your next login (its output is in `zmk-layer-hud log`; "
              "journalctl --user says only if it failed to start)")
    return 0


# ---------- doctor ----------

OK, WARN, BAD = "ok  ", "warn", "fail"


def report(state, label, detail, fix=None):
    print(f"[{state}] {label}: {detail}")
    if fix and state != OK:
        for line in fix.splitlines():
            print(f"       {line}")
    return state


def check_python(results):
    if venv_ok():
        try:
            version = subprocess.check_output(
                [VENV_PYTHON, "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
                text=True, stderr=subprocess.DEVNULL).strip()
        except subprocess.SubprocessError:
            version = "?"
        results.append(report(OK, "python", f"venv at {VENV_PYTHON} (Python {version})"))
        mods = ["serial", "websockets"] + (
            ["hid", "objc", "WebKit"] if platform.system() == "Darwin" else [])
        missing = [m for m in mods if subprocess.call(
            [VENV_PYTHON, "-c", f"import {m}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) != 0]
        if missing:
            results.append(report(BAD, "venv", "missing " + ", ".join(missing),
                                  "zmk-layer-hud setup"))
        else:
            results.append(report(OK, "venv", "every package the host needs imports"))
    else:
        python, version = python_for_setup()
        if python:
            results.append(report(BAD, "python", f"no venv yet (would build it from {python}, "
                                                 f"Python {version})", "zmk-layer-hud setup"))
        else:
            # The likeliest first failure on a stock macOS: /usr/bin/python3 is 3.9 and
            # keymap-drawer will not install on it.
            results.append(report(BAD, "python",
                                  f"no Python >= {PYTHON_MIN[0]}.{PYTHON_MIN[1]} found",
                                  "macOS: brew install python\nArch:  sudo pacman -S python"))


def check_platform(results):
    system = platform.system()
    if system == "Darwin":
        results.append(report(OK, "platform", "macOS, native overlay panel"))
        return
    if system != "Linux":
        results.append(report(BAD, "platform", f"{system} has no HUD host",
                              "macOS and Linux (Hyprland) only"))
        return
    if shutil.which("hyprctl"):
        results.append(report(OK, "platform", "Linux with Hyprland"))
    else:
        results.append(report(WARN, "platform", "Linux, but no hyprctl on PATH",
                              "the panel is a Hyprland layer-shell surface"))
    gi = subprocess.call(
        ["python3", "-c", "import gi; gi.require_version('Gtk', '3.0'); "
                          "gi.require_version('WebKit2', '4.1'); "
                          "gi.require_version('GtkLayerShell', '0.1'); gi.require_foreign('cairo')"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if gi == 0:
        results.append(report(OK, "gtk", "the system python has the panel's bindings"))
    else:
        results.append(report(BAD, "gtk", "python-gobject / python-cairo / webkit2gtk-4.1 / gtk-layer-shell "
                                          "missing from the system python", "zmk-layer-hud setup"))


def check_config(results):
    if not os.path.isfile(CONFIG) and not os.environ.get("ZMKHUD_CONFIG"):
        results.append(report(BAD, "config", f"no {CONFIG}", "zmk-layer-hud setup"))
        return
    if not venv_ok():
        results.append(report(WARN, "keymap", "cannot check without the venv"))
        return
    rc = subprocess.call([VENV_PYTHON, os.path.join(ROOT, "host", "keymap.py")],
                         stdout=subprocess.DEVNULL)
    if rc == 0:
        results.append(report(OK, "keymap", "the config and its definitions load"))
    else:
        results.append(report(BAD, "keymap", "the config and its definitions do not load",
                              "zmk-layer-hud keymap   (it says why; `zmk-layer-hud import` writes the definitions)"))
        return
    due = sync_due()
    if due:
        results.append(report(WARN, "definitions", f"{due} changed after the last sync", "zmk-layer-hud sync"))


def sync_due():
    """A source a sync reads that is newer than the definitions it wrote, or None. A look at file
    times, for doctor alone: the HUD never reads these files."""
    sys.path.insert(0, os.path.join(ROOT, "host"))
    try:
        import json
        import keymap as keymap_mod
        path = keymap_mod.find_config()
        defs = keymap_mod.definitions_path(path)
        cfg = keymap_mod.load_yaml(path)
        base = os.path.dirname(os.path.abspath(path))
        sources = [keymap_mod.expand(cfg[k], base) for k in ("keymap", "drawer_config") if cfg.get(k)]
        with open(defs, encoding="utf-8") as f:
            repo = (json.load(f).get("sources") or {}).get("repo")
        repo = repo and os.path.expanduser(repo)
        if repo and os.path.isdir(repo):
            for b, dirs, files in os.walk(repo):
                dirs[:] = [d for d in dirs if d not in (".git", "build", "modules", "zmk", "zephyr")]
                sources += [os.path.join(b, n) for n in files if n.endswith(".keymap")]
        made = os.stat(defs).st_mtime
        newer = [p for p in sources if os.path.isfile(p) and os.stat(p).st_mtime > made]
        return os.path.basename(newer[0]) if newer else None
    except Exception:
        return None


def check_udev(results):
    if platform.system() != "Linux":
        return
    if os.path.exists("/etc/udev/rules.d/60-zmk-layer-hud.rules") or \
            os.path.exists("/etc/udev/rules.d/60-zmk-vim-mode.rules"):
        results.append(report(OK, "udev", "the rule is installed"))
    else:
        results.append(report(WARN, "udev", "no rule in /etc/udev/rules.d",
                              "zmk-layer-hud setup   (the tty and hidraw grants come from it)"))


def check_path(results):
    if shutil.which("zmk-layer-hud"):
        results.append(report(OK, "path", f"{shutil.which('zmk-layer-hud')}"))
    else:
        results.append(report(WARN, "path", "zmk-layer-hud is not on PATH",
                              "zmk-layer-hud setup --link"))


def cmd_doctor(args):
    print(f"tree:  {ROOT}{'  (a git clone)' if in_clone() else ''}")
    print(f"state: {STATE}")
    print()
    results = []
    check_python(results)
    check_platform(results)
    check_config(results)
    check_udev(results)
    check_path(results)
    print()
    cmd_status(args)
    return 1 if BAD in results else 0


# ---------- update / uninstall ----------

def fetch_tree(ref, dest):
    """The tarball, into `dest`. urllib and tarfile rather than curl and tar: this runs from the
    installed tree, where the only thing guaranteed to be around is the interpreter."""
    import tarfile
    import tempfile
    import urllib.request
    url = f"https://codeload.github.com/{REPO}/tar.gz/refs/heads/{ref}"
    print(f"==> {url}")
    with tempfile.TemporaryDirectory() as tmp:
        archive = os.path.join(tmp, "tree.tar.gz")
        try:
            urllib.request.urlretrieve(url, archive)
        except OSError as e:
            raise Fail(f"could not download {url}: {e}")
        with tarfile.open(archive) as tar:
            members = []
            for m in tar.getmembers():
                parts = m.name.split("/", 1)
                if len(parts) != 2 or not parts[1]:
                    continue
                # Refuse anything that would land outside dest.
                if parts[1].startswith("/") or ".." in parts[1].split("/"):
                    raise Fail(f"refusing a tarball entry that escapes the tree: {m.name}")
                m.name = parts[1]
                members.append(m)
            tar.extractall(dest, members=members)


def cmd_update(args):
    if in_clone():
        print(f"{ROOT} is a git clone -- update it with `git -C {ROOT} pull`")
        return 0
    if panel_pids():
        raise Fail("the HUD is running, perhaps hidden; `zmk-layer-hud stop` first")
    import tempfile
    # Staging sits beside the tree so the swap is two renames within one directory. Everything
    # below is ordered so that a failure at any point leaves the working tree where it was: this
    # is the one verb that can destroy someone's install.
    staging = tempfile.mkdtemp(prefix="zmk-layer-hud.", dir=os.path.dirname(ROOT))
    try:
        fetch_tree(args.ref, staging)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    venv_dir = os.path.join(ROOT, ".venv")
    staged_venv = os.path.join(staging, ".venv")
    old = ROOT + ".old"
    shutil.rmtree(old, ignore_errors=True)
    moved_venv = False
    if os.path.isdir(venv_dir):
        os.rename(venv_dir, staged_venv)
        moved_venv = True
    try:
        os.rename(ROOT, old)
    except OSError as e:
        if moved_venv:
            os.rename(staged_venv, venv_dir)
        shutil.rmtree(staging, ignore_errors=True)
        raise Fail(f"could not move {ROOT} aside: {e}; nothing was changed")
    try:
        os.rename(staging, ROOT)
    except OSError as e:
        os.rename(old, ROOT)
        if moved_venv:
            os.rename(staged_venv, venv_dir)
        shutil.rmtree(staging, ignore_errors=True)
        raise Fail(f"could not put the new tree in place: {e}; the old one is back")
    shutil.rmtree(old, ignore_errors=True)
    os.chmod(os.path.join(ROOT, "bin", "zmk-layer-hud"), 0o755)
    print("==> tree updated")
    if venv_ok():
        # A dependency may have been added since; pip is quiet and quick when nothing changed.
        subprocess.call([VENV_PYTHON, "-m", "pip", "install", "--quiet", "--upgrade"] + VENV_PKGS)
        print("    venv refreshed")
    if os.path.isdir(plugin_dir()):
        # The new tree's own command: this process is still the old code.
        subprocess.call([os.path.join(ROOT, "bin", "zmk-layer-hud"), "menubar", "enable"])
    print("ready: zmk-layer-hud doctor")
    return 0


def cmd_uninstall(args):
    if in_clone():
        raise Fail(f"{ROOT} is a git clone -- delete it yourself if that is what you want")
    print(f"this will remove {BIN_LINK} and {ROOT}")
    if args.purge:
        print(f"      and, because of --purge, {CONFIG_DIR}, {STATE} (the logs and every session) and "
              f"{os.path.expanduser('~/.cache/zmk-layer-hud')}")
    if not confirm("proceed?", args):
        print("nothing was removed")
        return 1
    run_host("stop")
    if platform.system() == "Darwin":
        subprocess.call(["pkill", "-f", re.escape(MENUBAR)], stderr=subprocess.DEVNULL)
        if os.path.isfile(LAUNCH_AGENT):
            subprocess.call(["launchctl", "bootout", f"gui/{os.getuid()}/{APP_ID}"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            os.remove(LAUNCH_AGENT)
            print(f"    removed {LAUNCH_AGENT}")
        if os.path.isdir(LOGIN_APP):
            shutil.rmtree(os.path.dirname(LOGIN_APP), ignore_errors=True)
            print(f"    removed {LOGIN_APP}; what macOS let it do goes with:")
            print(f"        tccutil reset ListenEvent {APP_ID}; tccutil reset BluetoothAlways {APP_ID}")
    elif os.path.isfile(AUTOSTART_DESKTOP):
        os.remove(AUTOSTART_DESKTOP)
        print(f"    removed {AUTOSTART_DESKTOP}")
    if platform.system() == "Linux":
        import shortcuts
        if shortcuts.remove_hyprland():
            print(f"    took the shortcuts out of {shortcuts.HYPR_DIR}")
        for pid in (OMARCHY_ID,) + OLD_OMARCHY_IDS:
            if remove_plugin(pid):
                print(f"    took the {pid} bar plugin out of Omarchy")
    if os.path.islink(BIN_LINK) and os.path.realpath(BIN_LINK).startswith(os.path.realpath(ROOT)):
        os.unlink(BIN_LINK)
        print(f"    removed {BIN_LINK}")
    shutil.rmtree(ROOT, ignore_errors=True)
    print(f"    removed {ROOT}")
    if args.purge:
        for path in (CONFIG_DIR, STATE, os.path.expanduser("~/.cache/zmk-layer-hud")):
            shutil.rmtree(path, ignore_errors=True)
            print(f"    removed {path}")
    else:
        print(f"    left {CONFIG_DIR} and the sessions in {STATE} alone (--purge removes them too)")
    if platform.system() == "Linux":
        print("    the udev rule is still installed; remove it with:")
        print("        sudo rm /etc/udev/rules.d/60-zmk-layer-hud.rules")
    return 0


def cmd_version(args):
    print(f"zmk-layer-hud {describe_version()}")
    print(f"  tree   {ROOT}")
    print(f"  python {sys.executable} ({platform.python_version()})")
    print(f"  state  {STATE}")
    print(f"  config {CONFIG}")
    return 0


def describe_version():
    if in_clone() and shutil.which("git"):
        try:
            return subprocess.check_output(
                ["git", "-C", ROOT, "describe", "--always", "--dirty"],
                text=True, stderr=subprocess.DEVNULL).strip()
        except subprocess.SubprocessError:
            pass
    return "(installed tree)"


# ---------- the parser ----------

def build_parser():
    p = argparse.ArgumentParser(
        prog="zmk-layer-hud",
        description="An on-screen HUD for ZMK keyboards.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="`zmk-layer-hud <command> --help` explains any one of them.")
    sub = p.add_subparsers(dest="cmd", metavar="<command>")

    def add(name, help_text, **kw):
        s = sub.add_parser(name, help=help_text, description=help_text, **kw)
        return s

    s = add("start", "start the HUD")
    s.add_argument("--reserve", action="store_true",
                   help="(Linux) give the HUD an exclusive zone so windows tile beside it "
                        "rather than under it; for recording")
    s.add_argument("--hidden", action="store_true",
                   help="start it off screen, counting; `zmk-layer-hud show` or its icon brings it up")
    s.add_argument("--foreground", action="store_true",
                   help="stay until the HUD stops, as the HUD itself (its output still goes to the log); "
                        "what a login item runs")
    s.set_defaults(func=cmd_start)

    add("stop", "stop the HUD").set_defaults(func=cmd_stop)

    s = add("restart", "stop the HUD, then start it")
    s.add_argument("--reserve", action="store_true", help="as for `start`")
    s.add_argument("--hidden", action="store_true", help="as for `start`")
    s.set_defaults(func=cmd_restart)

    add("power", "start the HUD if it is not running, stop it if it is").set_defaults(func=cmd_power)

    add("show", "bring the HUD back on screen").set_defaults(func=cmd_show)
    add("hide", "take the HUD off screen; it goes on running and counting").set_defaults(func=cmd_hide)
    add("toggle", "show the HUD if it is hidden, hide it if it is shown").set_defaults(func=cmd_toggle)

    add("status", "is it running, and what is it reading").set_defaults(func=cmd_status)

    s = add("session", "the typing sessions: the active one, naming it, starting or loading another")
    s.add_argument("action", nargs="?", default="status",
                   choices=("status", "list", "new", "save", "load", "reset", "delete", "rename-layer", "export",
                            "history", "compare"),
                   help="status (default), list, new [NAME], save NAME, load NAME, reset, delete NAME, "
                        "rename-layer OLD NEW, export [NAME], history [NAME], compare NAME [OTHER]")
    s.add_argument("name", nargs="?", help="the session, for new, save, load, delete, export, history and compare "
                                           "(default: the active one); the layer, for rename-layer")
    s.add_argument("other", nargs="?", help="the layer's new name, for rename-layer; the session to compare with "
                                            "(default: the active one)")
    s.add_argument("--all", action="store_true", help="rename-layer in every session, not only the active one; "
                                                      "history: every session's days added up")
    s.add_argument("--yes", action="store_true", help="do not ask before reset or delete")
    s.add_argument("--mode", choices=("session", "physical", "speed"), default="session",
                   help="export: the presses on each layer (default), every layer's together, or each key's time")
    s.add_argument("--layers", help="export: the layers to draw, comma-separated (default: every one with heat)")
    s.add_argument("-o", "--output", help="export: the SVG file to write (default: NAME-MODE.svg here; - for stdout)")
    s.add_argument("--config", help="export: the config whose keymap to draw (default: the one the HUD reads)")
    s.set_defaults(func=cmd_session)

    s = add("heatmap", "what the keys glow with: the live heatmap, the session's, or none")
    s.add_argument("mode", nargs="?", choices=("live", "session", "physical", "speed", "off"),
                   help="live (what was just typed), session (every press counted, on the layer on screen), "
                        "physical (every press, all layers together), speed (the time each key takes), off; "
                        "none: say which")
    s.set_defaults(func=cmd_heatmap)

    s = add("log", "follow the panel and feed logs")
    s.add_argument("-n", "--lines", type=int, default=40, help="lines of history (default 40)")
    s.add_argument("--no-follow", action="store_true", help="print and exit instead of following")
    s.set_defaults(func=cmd_log)

    add("doctor", "check this machine and say what is missing").set_defaults(func=cmd_doctor)

    s = add("autostart", "start the HUD hidden at login, counting from the first keystroke, or stop doing so")
    s.add_argument("action", nargs="?", default="status", choices=("status", "enable", "disable"),
                   help="status (default), enable, disable; disable leaves a running HUD running")
    s.set_defaults(func=cmd_autostart)

    s = add("menubar", "the icon that shows, hides or starts the HUD, with its live WPM: macOS's menubar, Omarchy's bar")
    s.add_argument("action", nargs="?", default="status", choices=("status", "enable", "disable"),
                   help="status (default), enable, disable")
    s.set_defaults(func=cmd_menubar)

    s = add("setup", "set this machine up (packages, venv, config, permissions)")
    s.add_argument("--no-sudo", action="store_true",
                   help="print the privileged steps instead of running them")
    s.add_argument("--yes", action="store_true", help="do not ask before a privileged step")
    s.add_argument("--link", action="store_true",
                   help="also put zmk-layer-hud on PATH from this tree")
    s.add_argument("--link-only", action="store_true",
                   help="with --link, do nothing else")
    s.add_argument("--venv-only", action="store_true",
                   help="build the virtualenv and do nothing else")
    s.set_defaults(func=cmd_setup)

    s = add("update", "fetch a newer tree over this one")
    s.add_argument("--ref", default=os.environ.get("ZMKHUD_REF", "main"),
                   help="branch to fetch (default: main, or $ZMKHUD_REF)")
    s.set_defaults(func=cmd_update)

    s = add("uninstall", "remove the tree and the command")
    s.add_argument("--purge", action="store_true", help="also remove the config, cache and logs")
    s.add_argument("--yes", action="store_true", help="do not ask")
    s.set_defaults(func=cmd_uninstall)

    s = add("import", "write the HUD's definitions: the drawing, and what a ZMK repo's keymap says of it")
    s.add_argument("source", metavar="REPO", nargs="?",
                   help="a GitHub URL, a path to a working copy, or the keymap-drawer YAML the config names "
                        "(none: draw from `keymap:` alone)")
    s.add_argument("--keyboard", help="which keyboard in that repo")
    s.add_argument("--config", help="config file (default: $ZMKHUD_CONFIG or ~/.config/...)")
    s.add_argument("--quiet", action="store_true", help="say nothing but errors")
    s.add_argument("--no-fetch", action="store_true", help="fetch no glyphs: use keymap-drawer's cache only")
    s.add_argument("--pristine", action="store_true",
                   help="from scratch: ignore what earlier imports left (the repo they recorded, drafted "
                        "layer mappings, a cached clone) and read only what is given now")
    s.add_argument("--keep-custom", nargs="?", const=True, default=None, metavar="N,M",
                   help="with --pristine: keep what only an earlier import had and redo the rest -- all of "
                        "it, or just the items numbered N,M in the list it prints")
    s.add_argument("--drop-custom", action="store_true",
                   help="with --pristine: drop that too, without asking")
    s.set_defaults(func=lambda a: cmd_sync(a, "import"))

    s = add("sync", "write the definitions again from the same sources, and say what changed")
    s.add_argument("--config", help="config file")
    s.add_argument("--quiet", action="store_true", help="say nothing but errors")
    s.add_argument("--no-fetch", action="store_true", help="fetch no glyphs: use keymap-drawer's cache only")
    s.add_argument("--watch", action="store_true",
                   help="stay, and sync again each time the config, the keymap-drawer files or a working copy's "
                        "keymap is edited; the running HUD redraws (Ctrl-C stops)")
    s.set_defaults(func=lambda a: cmd_sync(a, "sync"))

    s = add("keymap", "check the HUD's own files load: the config and the definitions import wrote")
    s.add_argument("--config", help="config file")
    s.add_argument("--dump", action="store_true", help="print the full JSON message")
    s.set_defaults(func=cmd_keymap)

    s = add("config", "where the config is, and what is in it")
    s.add_argument("action", nargs="?", default="path", choices=("path", "show", "edit", "link"),
                   help="path (default), show, edit, or link a config kept in a repo")
    s.add_argument("file", nargs="?", help="with `link`: the config to link at")
    s.set_defaults(func=cmd_config)

    s = add("demo", "serve the pages against a sample keymap, with no keyboard; --play types a script on them")
    s.add_argument("--config", help="a config to draw (default: config/example-3x5.yaml)")
    s.add_argument("--keymap", metavar="FILE", help="a keymap message to draw instead (zmk-layer-hud keymap --dump)")
    s.add_argument("--play", metavar="SCRIPT", help="type a demo script on it, in real time (docs/demo-scripts.md)")
    s.add_argument("--loop", action="store_true", help="with --play: again and again")
    s.add_argument("--speed", type=float, default=1.0, help="with --play: this many times as fast (default 1)")
    s.add_argument("--strict", action="store_true", help="with --play: refuse a script this keymap cannot type all of")
    s.add_argument("--port", type=int, default=8765, help="the pages' port (default 8765)")
    s.add_argument("--ws-port", type=int, default=8767,
                   help="the socket that feeds them (default 8767; a running HUD's feed has 8766)")
    s.add_argument("--no-browser", action="store_true", help="do not open a browser")
    s.set_defaults(func=cmd_demo)

    # add_help=False on these two: their flags belong to hudpoke and hudfeed, and argparse would
    # otherwise answer `--help` here with this wrapper's four lines instead of passing it down.
    s = add("poke", "drive the HUD by sending the feed what a keyboard would have sent",
            add_help=False)
    s.add_argument("rest", nargs=argparse.REMAINDER, metavar="...")
    s.set_defaults(func=cmd_poke)

    s = add("feed", "run the feed alone, serving its WebSocket", add_help=False)
    s.add_argument("rest", nargs=argparse.REMAINDER, metavar="...")
    s.set_defaults(func=cmd_feed)

    add("version", "what this is and where it lives").set_defaults(func=cmd_version)
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Split these off before argparse sees them. Their whole line belongs to hudpoke/hudfeed, and
    # argparse.REMAINDER declines to swallow a leading `--help`, which is exactly the word someone
    # types first when they want their flags.
    if argv and argv[0] in PASSTHROUGH:
        try:
            reexec_into_venv()
            return passthrough(PASSTHROUGH[argv[0]], argv[0], argv[1:])
        except Fail as e:
            warn(f"zmk-layer-hud {argv[0]}: {e}")
            return 1
    # `help` is the word people type, as a verb and after one: `zmk-layer-hud import help` would
    # otherwise reach import as the repository to clone.
    if argv == ["help"]:
        argv = ["--help"]
    elif len(argv) == 2 and argv[1] == "help":
        argv = [argv[0], "--help"]
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 0
    try:
        # Inside the try: no venv is a sentence, not a traceback -- and at login, with no one
        # watching, a traceback is all the log would have.
        if needs_venv(args):
            reexec_into_venv()
        return args.func(args) or 0
    except Fail as e:
        warn(f"zmk-layer-hud {args.cmd}: {e}")
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
