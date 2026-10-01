"""Whether the running panel is on screen, and how the command line asks it to change.

A running panel -- host/macos/panel.py or host/linux/panel.py -- keeps $STATE/panel.json saying
which process it is and whether it is shown. `zmk-layer-hud status` reads it, and so does the
Omarchy bar widget (host/linux/omarchy). `show` and `hide` write what they want to
$STATE/panel.want and send the panel SIGNAL. The panel does it, then writes panel.json again,
which is how they know it has.

One signal and a file, rather than SIGUSR1 for one and SIGUSR2 for the other: on Linux WebKit
takes SIGUSR1 to suspend its own threads (JSC_SIGNAL_FOR_GC), and the GTK panel is a WebKit
process. A stray SIGUSR1 there hangs or crashes it.

Stdlib only, and Python 3.9: `status` runs under Apple's python before any venv exists, and the
Linux panel runs under the system one.
"""

import json
import os
import signal

SIGNAL = signal.SIGUSR2
STATE_FILE = "panel.json"
WANT_FILE = "panel.want"


def default_dir():
    """$STATE as host/cli.py works it out, for a panel started by hand rather than by the CLI
    (which exports ZMKHUD_STATE, as the host scripts do)."""
    return os.environ.get("ZMKHUD_STATE") or os.path.join(
        os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "zmk-layer-hud")


def _write(path, obj):
    """Whole or not at all: the CLI and the bar widget read these at any moment."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            obj = json.load(f)
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def alive(pid):
    """Whether pid is a process of ours. Another user's (a pid reused since) is not."""
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def write(d, shown, pid=None):
    """The panel's word: which process it is, and whether it is on screen."""
    _write(os.path.join(d, STATE_FILE), {"pid": pid or os.getpid(), "shown": bool(shown)})


def read(d):
    """What a live panel last said, as {"pid", "shown"}; None when there is no such file, it is not
    one of these, or its process is gone."""
    st = _load(os.path.join(d, STATE_FILE))
    if not st or type(st.get("pid")) is not int or not isinstance(st.get("shown"), bool):
        return None
    return st if alive(st["pid"]) else None


def remove(d, pid=None):
    """The panel's last word, on its way out -- unless the file is already another panel's: a
    restart's new panel can say it is there before the old one has finished going."""
    path = os.path.join(d, STATE_FILE)
    st = _load(path)
    if st and st.get("pid") == (pid or os.getpid()):
        discard(d)


def discard(d):
    """For the command line: a file whose panel is gone says nothing true, and the bar widget
    would go on believing it."""
    try:
        os.remove(os.path.join(d, STATE_FILE))
    except OSError:
        pass


def ask(d, shown):
    """The command line's request, written before it sends SIGNAL."""
    _write(os.path.join(d, WANT_FILE), {"shown": bool(shown)})


def wanted(d):
    """What the command line last asked for, or None."""
    w = _load(os.path.join(d, WANT_FILE))
    return w["shown"] if w and isinstance(w.get("shown"), bool) else None
