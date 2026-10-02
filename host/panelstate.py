"""Whether the running panel is on screen, and how the command line asks it to change.

A running panel -- host/macos/panel.py or host/linux/panel.py -- keeps $STATE/panel.json saying
which process it is, whether it is shown, the live WPM the page last showed, and -- once the page
has said them -- the session's average and top WPM and the rows of its stats column. `zmk-layer-hud
status` reads it, and so do the menubar icons (host/macos/menubar.py, host/linux/omarchy). `show`
and `hide` write what they want to $STATE/panel.want and send the panel SIGNAL. The panel does it, then writes panel.json again,
which is how they know it has.

Which of the three WPMs the icons show -- the live one, the session's average, or its top -- is
the icons' own choice, in $STATE/menubar.json: their menus set it through `zmk-layer-hud menubar
wpm`, and both read it.

One signal and a file, rather than SIGUSR1 for one and SIGUSR2 for the other: on Linux WebKit
takes SIGUSR1 to suspend its own threads (JSC_SIGNAL_FOR_GC), and the GTK panel is a WebKit
process. A stray SIGUSR1 there hangs or crashes it.

The feed's socket takes a token in its URL path, one per run (host/hudfeed.py, Hub.process_request):
whoever serves the socket keeps it in $STATE/token, readable by this user alone, and `zmk-layer-hud
poke` reads it from there. A browser page on another origin, or another user on the machine, has
no way to it.

Stdlib only, and Python 3.9: `status` runs under Apple's python before any venv exists, and the
Linux panel runs under the system one.
"""

import contextlib
import json
import os
import signal
import tempfile

SIGNAL = signal.SIGUSR2
STATE_FILE = "panel.json"
WANT_FILE = "panel.want"
CHOICE_FILE = "menubar.json"
TOKEN_FILE = "token"
WPM_CHOICES = ("current", "average", "top")
ROWS_MAX = 24           # rows of the stats column, and each one's label and value at most this long:
TEXT_MAX = 64           # a page says what its column shows, and nothing like a page of it


def default_dir():
    """$STATE as host/cli.py works it out, for a panel started by hand rather than by the CLI
    (which exports ZMKHUD_STATE, as the host scripts do)."""
    return os.environ.get("ZMKHUD_STATE") or os.path.join(
        os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "zmk-layer-hud")


def _write(path, obj):
    """Whole or not at all: the CLI and the bar widget read these at any moment. For this user
    alone (mkstemp's 0600): what they say -- the live WPM, the session's name -- is theirs, and
    everything that reads them runs as them."""
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            obj = json.load(f)
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _write_private(path, text):
    """For this user alone (0600, in a 0700 directory), and whole or not at all."""
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)              # makedirs leaves an existing directory's mode as it was
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def write_token(d, token):
    """The socket's token for this run, from whoever serves the socket."""
    _write_private(os.path.join(d, TOKEN_FILE), token + "\n")


def read_token(d):
    """The running feed's socket token, or None while nothing serves one."""
    try:
        with open(os.path.join(d, TOKEN_FILE), encoding="utf-8") as f:
            token = f.read().strip()
    except OSError:
        return None
    return token or None


def remove_token(d, token):
    """On the way out -- unless the file is already a newer run's: a restart's new feed can have
    written its token before the old one has finished going."""
    if read_token(d) == token:
        with contextlib.suppress(OSError):
            os.remove(os.path.join(d, TOKEN_FILE))


def alive(pid):
    """Whether pid is a process of ours. Another user's (a pid reused since) is not."""
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def write(d, shown, pid=None, wpm=0, stats=None):
    """The panel's word: which process it is, whether it is on screen, how fast it is being typed
    on, and `stats` (from stats_of) once the page has said them."""
    obj = {"pid": pid or os.getpid(), "shown": bool(shown), "wpm": int(wpm or 0)}
    obj.update(stats or {})
    _write(os.path.join(d, STATE_FILE), obj)


def _wpm_or_none(n):
    return n if type(n) is int and 0 <= n <= 1000 else None


def stats_of(msg):
    """What a page's {"kind": "stats"} says, checked, as panel.json keeps it: {"avg_wpm", "top_wpm"
    (each a number, or None while there is none yet), "session" (its name, where the column shows
    it, else None), "stats": [[label, value], ...], the rows of its stats column in order}. None for
    a message that is not one."""
    if not isinstance(msg, dict) or not isinstance(msg.get("rows"), list) or len(msg["rows"]) > ROWS_MAX:
        return None
    rows = []
    for row in msg["rows"]:
        if not (isinstance(row, list) and len(row) == 2 and all(isinstance(t, str) and len(t) <= TEXT_MAX for t in row)):
            return None
        rows.append(list(row))
    session = msg.get("session")
    return {"avg_wpm": _wpm_or_none(msg.get("avg")), "top_wpm": _wpm_or_none(msg.get("top")),
            "session": session if isinstance(session, str) and 0 < len(session) <= TEXT_MAX else None, "stats": rows}


def read(d):
    """What a live panel last said, as {"pid", "shown", "wpm"}; None when there is no such file,
    it is not one of these, or its process is gone."""
    st = _load(os.path.join(d, STATE_FILE))
    if not st or type(st.get("pid")) is not int or not isinstance(st.get("shown"), bool):
        return None
    if type(st.get("wpm")) is not int:
        st["wpm"] = 0
    # A panel from before it said these, or a page that has not said them yet.
    checked = stats_of({"avg": st.get("avg_wpm"), "top": st.get("top_wpm"), "session": st.get("session"),
                        "rows": st.get("stats")}) or {"avg_wpm": None, "top_wpm": None, "session": None, "stats": []}
    st.update(checked)
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


def wpm_choice(d):
    """Which WPM the icons show: "current" (the live one, until another was chosen), "average" or
    "top"."""
    c = _load(os.path.join(d, CHOICE_FILE))
    return c["wpm"] if c and c.get("wpm") in WPM_CHOICES else "current"


def set_wpm_choice(d, choice):
    if choice not in WPM_CHOICES:
        raise ValueError(f"{choice!r} is not one of {', '.join(WPM_CHOICES)}")
    _write(os.path.join(d, CHOICE_FILE), {"wpm": choice})


def wpm_shown(st, choice):
    """The number an icon shows for `choice`, from what read() returned: None while there is none
    (no average before ten seconds of typing, no top before a full window of it)."""
    if not st:
        return None
    return {"current": st.get("wpm"), "average": st.get("avg_wpm"), "top": st.get("top_wpm")}.get(choice)
