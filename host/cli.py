#!/usr/bin/env python3
"""zmk-layer-hud — one command for everything the HUD does.

`bin/zmk-layer-hud` finds an interpreter and hands off here. The verbs split in two:

  the HUD          start, stop, restart, status, log
  the keymap       keymap, import, sync, config
  this machine     setup, doctor, update, uninstall, version
  without a board  demo, poke, feed

Nothing above the stdlib is imported at module level, and that is deliberate: `doctor` and
`setup` have to run on a machine where the venv does not exist yet -- that is when they are most
needed -- and Apple's /usr/bin/python3 is 3.9, where keymap-drawer will not even install. The
verbs that do need the venv re-exec into it first; see `NEEDS_VENV`.
"""

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys

# The shim exports this; computed here too so `python3 host/cli.py` works from a clone.
ROOT = os.environ.get("ZMKHUD_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PYTHON = os.path.join(ROOT, ".venv", "bin", "python3")

# Logs live outside the tree because `update` replaces the tree wholesale, and the directory the
# logs are in cannot be the directory being swapped. The host scripts read the same variable.
STATE = os.environ.get("ZMKHUD_STATE") or os.path.join(
    os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "zmk-layer-hud")

CONFIG_DIR = os.path.expanduser("~/.config/zmk-layer-hud")
CONFIG = os.path.join(CONFIG_DIR, "config.yaml")
BIN_DIR = os.path.expanduser("~/.local/bin")
BIN_LINK = os.path.join(BIN_DIR, "zmk-layer-hud")

REPO = "rafaelromao/zmk-layer-hud"
PYTHON_MIN = (3, 10)

# The one definition of what the venv holds. hidapi is macOS only: Linux reads /dev/hidrawN
# itself, and the wheel there bundles the libusb backend, which wants an access no udev rule
# grants and detaches the kernel HID driver.
VENV_PKGS = ["pyserial", "keymap-drawer", "websockets", "bleak"]
if platform.system() == "Darwin":
    VENV_PKGS += ["hidapi", "pyobjc-framework-Cocoa", "pyobjc-framework-WebKit"]

# Verbs that need keymap-drawer, pyserial, websockets or pyobjc. Everything else must keep
# working on a half-installed machine.
NEEDS_VENV = {"start", "restart", "keymap", "import", "sync", "poke", "feed", "demo"}


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


def env_for_host(reserve=False):
    env = dict(os.environ)
    env["ZMKHUD_RESERVE"] = "1" if reserve else "0"
    env["ZMKHUD_STATE"] = STATE
    env["ZMKHUD_ROOT"] = ROOT
    # The host scripts prefer $ZMKHUD_PYTHON over their own search, so pointing them at the venv
    # here means neither has to learn where an installed tree keeps it.
    if os.path.exists(VENV_PYTHON) and not os.environ.get("ZMKHUD_PYTHON"):
        env["ZMKHUD_PYTHON"] = VENV_PYTHON
    return env


def run_host(verb, reserve=False):
    os.makedirs(STATE, exist_ok=True)
    return subprocess.call(["bash", host_script(), verb], env=env_for_host(reserve))


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

def cmd_start(args):
    return run_host("start", args.reserve)


def cmd_stop(args):
    return run_host("stop")


def cmd_restart(args):
    run_host("stop")
    return run_host("start", args.reserve)


def pgrep(pattern):
    return subprocess.call(["pgrep", "-f", pattern],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0


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
    print("panel: " + ("running" if pgrep(host_dir + r"/.*/panel\.py") else "stopped"))
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
    if args.no_fetch:
        argv.append("--no-fetch")
    return keymap_mod.main(argv)


def cmd_sync(args, verb):
    """`import` and `sync` keep their own parser: its messages already say `zmk-layer-hud import`
    and they have been right all along, so the argv is reassembled rather than re-declared."""
    sys.path.insert(0, os.path.join(ROOT, "host"))
    import sync as sync_mod
    argv = [verb]
    if verb == "import":
        argv.append(args.source)
        if args.keyboard:
            argv += ["--keyboard", args.keyboard]
    if args.config:
        argv += ["--config", args.config]
    if args.quiet:
        argv.append("--quiet")
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

    Both names are linked, and that is not a convenience. `<config>.imported.yaml` is found beside
    the config *path*, not beside whatever that path points at, so linking only config.yaml would
    leave the machine's stale imported file in play -- and the two can disagree about
    combo_term_ms in ways that cancel out only while they travel together."""
    src = os.path.abspath(os.path.expanduser(args.file))
    if not os.path.isfile(src):
        raise Fail(f"no such file: {args.file}")
    os.makedirs(CONFIG_DIR, exist_ok=True)
    stem = os.path.splitext(src)[0]
    for source, dest in ((src, CONFIG),
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

def cmd_demo(args):
    """The HUD's pages against a sample keymap, with no keyboard and no feed: what `examples/`
    is for. The page's own API (`hud.setLayers([1])`, `hud.pressAt(13)`) drives it from the
    browser console, and `zmk-layer-hud poke` drives it over the socket."""
    import http.server
    import functools
    import threading
    import webbrowser

    sys.path.insert(0, os.path.join(ROOT, "host"))
    import keymap as keymap_mod

    config = args.config or os.path.join(ROOT, "config", "example-3x5.yaml")
    pages = os.path.join(ROOT, "hud")
    out = os.path.join(pages, "keymap.json")
    print(f"==> keymap from {config}")
    import io
    buf = io.StringIO()
    stdout, sys.stdout = sys.stdout, buf
    try:
        rc = keymap_mod.main(["--config", config, "--dump"])
    finally:
        sys.stdout = stdout
    if rc:
        return rc
    with open(out, "w", encoding="utf-8") as f:
        f.write(buf.getvalue())

    url = f"http://localhost:{args.port}/index.html?keymap=keymap.json"
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=pages)
    handler.log_message = lambda *a, **k: None
    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    except OSError as e:
        raise Fail(f"cannot serve on port {args.port}: {e}. Something else is on it -- "
                   f"pass --port to pick another.")
    print(f"==> {url}")
    print("    hud.setLayers([1]) switches layers, hud.pressAt(13) lights a key; ^C to stop")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    return 0


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
    pkgs = ["python-gobject", "webkit2gtk-4.1", "gtk-layer-shell"]
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
    print("    set `keymap:` to your keymap-drawer YAML, then: zmk-layer-hud keymap")


def link_command():
    os.makedirs(BIN_DIR, exist_ok=True)
    target = os.path.join(ROOT, "bin", "zmk-layer-hud")
    # A tarball carries whatever mode the archive held, and a file written by hand may carry none;
    # the command is useless without the bit, so set it rather than assume it.
    os.chmod(target, 0o755)
    if os.path.islink(BIN_LINK) or os.path.exists(BIN_LINK):
        os.unlink(BIN_LINK)
    os.symlink(target, BIN_LINK)
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
                          "gi.require_version('GtkLayerShell', '0.1')"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if gi == 0:
        results.append(report(OK, "gtk", "the system python has the panel's bindings"))
    else:
        results.append(report(BAD, "gtk", "python-gobject / webkit2gtk-4.1 / gtk-layer-shell "
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
        results.append(report(OK, "keymap", "the configured YAML converts"))
    else:
        results.append(report(BAD, "keymap", "the configured YAML does not convert",
                              "zmk-layer-hud keymap   (it prints the reason)"))


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
    if pgrep(re.escape(os.path.join(ROOT, "host")) + r"/.*/panel\.py"):
        raise Fail("the HUD is running; `zmk-layer-hud stop` first")
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
    print("ready: zmk-layer-hud doctor")
    return 0


def cmd_uninstall(args):
    if in_clone():
        raise Fail(f"{ROOT} is a git clone -- delete it yourself if that is what you want")
    print(f"this will remove {BIN_LINK} and {ROOT}")
    if args.purge:
        print(f"      and, because of --purge, {CONFIG_DIR}, {STATE} and "
              f"{os.path.expanduser('~/.cache/zmk-layer-hud')}")
    if not confirm("proceed?", args):
        print("nothing was removed")
        return 1
    run_host("stop")
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
        print(f"    left {CONFIG_DIR} alone (--purge removes it too)")
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
    s.set_defaults(func=cmd_start)

    add("stop", "stop the HUD").set_defaults(func=cmd_stop)

    s = add("restart", "stop the HUD, then start it")
    s.add_argument("--reserve", action="store_true", help="as for `start`")
    s.set_defaults(func=cmd_restart)

    add("status", "is it running, and what is it reading").set_defaults(func=cmd_status)

    s = add("log", "follow the panel and feed logs")
    s.add_argument("-n", "--lines", type=int, default=40, help="lines of history (default 40)")
    s.add_argument("--no-follow", action="store_true", help="print and exit instead of following")
    s.set_defaults(func=cmd_log)

    add("doctor", "check this machine and say what is missing").set_defaults(func=cmd_doctor)

    s = add("setup", "set this machine up (packages, venv, config, permissions)")
    s.add_argument("--no-sudo", action="store_true",
                   help="print the privileged steps instead of running them")
    s.add_argument("--yes", action="store_true", help="do not ask before a privileged step")
    s.add_argument("--link", action="store_true",
                   help="also put zmk-layer-hud on PATH from this tree")
    s.add_argument("--link-only", action="store_true",
                   help="with --link, do nothing else")
    s.set_defaults(func=cmd_setup)

    s = add("update", "fetch a newer tree over this one")
    s.add_argument("--ref", default=os.environ.get("ZMKHUD_REF", "main"),
                   help="branch to fetch (default: main, or $ZMKHUD_REF)")
    s.set_defaults(func=cmd_update)

    s = add("uninstall", "remove the tree and the command")
    s.add_argument("--purge", action="store_true", help="also remove the config, cache and logs")
    s.add_argument("--yes", action="store_true", help="do not ask")
    s.set_defaults(func=cmd_uninstall)

    s = add("import", "read layer ids, key positions and combo layers out of a ZMK repo")
    s.add_argument("source", metavar="REPO", help="a GitHub URL, or a path to a working copy")
    s.add_argument("--keyboard", help="which keyboard in that repo")
    s.add_argument("--config", help="config file (default: $ZMKHUD_CONFIG or ~/.config/...)")
    s.add_argument("--quiet", action="store_true", help="say nothing but errors")
    s.set_defaults(func=lambda a: cmd_sync(a, "import"))

    s = add("sync", "read the recorded source again, and say what changed")
    s.add_argument("--config", help="config file")
    s.add_argument("--quiet", action="store_true", help="say nothing but errors")
    s.set_defaults(func=lambda a: cmd_sync(a, "sync"))

    s = add("keymap", "check the configured keymap-drawer YAML converts")
    s.add_argument("--config", help="config file")
    s.add_argument("--dump", action="store_true", help="print the full JSON message")
    s.add_argument("--no-fetch", action="store_true", help="do not fetch missing glyphs (offline)")
    s.set_defaults(func=cmd_keymap)

    s = add("config", "where the config is, and what is in it")
    s.add_argument("action", nargs="?", default="path", choices=("path", "show", "edit", "link"),
                   help="path (default), show, edit, or link a config kept in a repo")
    s.add_argument("file", nargs="?", help="with `link`: the config to link at")
    s.set_defaults(func=cmd_config)

    s = add("demo", "serve the pages against a sample keymap, with no keyboard")
    s.add_argument("--config", help="a config to draw (default: config/example-3x5.yaml)")
    s.add_argument("--port", type=int, default=8765, help="port to serve on (default 8765)")
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
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 0
    if args.cmd in NEEDS_VENV:
        reexec_into_venv()
    try:
        return args.func(args) or 0
    except Fail as e:
        warn(f"zmk-layer-hud {args.cmd}: {e}")
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
