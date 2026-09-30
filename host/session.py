"""zmk-layer-hud sessions: what the keyboard typed, counted, and kept in files in the user's
state directory.

There is always one active session. The page counts every keystroke it draws (hud/hud.js, the
ledger), the host adds those counts to the active session, and `zmk-layer-hud session` names it,
starts another, or loads one back -- after which typing adds to that one again, the way a tmux
session is reattached. A session holds counts and nothing else: how often each key was pressed
on each layer, each combo, how many characters were typed and deleted, the time spent typing and
the best speed, how long each key takes, and those totals again for each day it typed on. Never
what was typed, in what order, or when within a day.

    $ZMKHUD_STATE/sessions/       (default ~/.local/state/zmk-layer-hud/sessions; 0700)
        <name>.json               one session (0600)
        .state.json               {"active": <name>, "heatmap": "live" | "session" | "off"}
        .lock                     held by whatever reads a session to add to it or rewrite it

The files are the truth. The feed keeps what the page reported since its last write and adds it
to the file every few seconds, under the lock; the commands change the files under the same lock
and the feed notices within a second. So neither has to know whether the other is running.

Standard library only, and Python 3.9 syntax: `zmk-layer-hud session` has to work where the venv
does not exist yet, like `doctor`.
"""

import contextlib
import datetime
import fcntl
import json
import os
import re
import sys
import tempfile
import threading
import time

VERSION = 1
# What the keys glow with: what was just typed; the session's presses on the layer on screen;
# its presses on every layer, by where the fingers went; the time each key takes; nothing.
HEATMAP_MODES = ("live", "session", "physical", "speed", "off")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# Added up from what the page reports; peak_wpm is kept as a maximum instead. sfb and bigrams:
# two keystrokes in a row on one finger, of two keystrokes in a row by any two (hud.js "strokes").
TOTALS = ("chars", "deleted", "active_ms", "active_net", "sfb", "bigrams")
# Counts by layer, then by key: presses and combos (their keys "p,q"), and for each key the time
# from the keystroke before it, in ms and in how many presses were timed.
MAPS = ("presses", "combos", "ms", "timed")
# A batch the page sends covers two seconds of typing: anything past these is not typing.
MAX_COUNT = 100000
MAX_ENTRIES = 4096
SFB_MIN = 20           # a share of same-finger bigrams is said once there are this many bigrams
LATE_S = 10.0          # a batch for a session replaced this recently still lands in it
PAGES_KEPT = 4         # acknowledgements kept, one per page that has reported


class SessionError(Exception):
    """A message for the user, not a traceback."""


def _warn(msg):
    print(msg, file=sys.stderr, flush=True)


def default_dir():
    state = os.environ.get("ZMKHUD_STATE") or os.path.join(
        os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "zmk-layer-hud")
    return os.path.join(state, "sessions")


def check_name(name):
    if not isinstance(name, str) or not NAME.match(name):
        raise SessionError(f"{name!r} is not a session name: letters, digits, '.', '_' and '-', "
                           "starting with a letter or a digit, at most 64")
    return name


def path_of(directory, name):
    return os.path.join(directory, check_name(name) + ".json")


def _now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def empty(name, named):
    t = _now()
    return {"version": VERSION, "id": os.urandom(8).hex(), "gen": 0, "name": name, "named": bool(named),
            "created": t, "updated": t, "keyboards": [], "keymap": "", "layers": [],
            **{kind: {} for kind in MAPS}, "totals": dict({k: 0 for k in TOTALS}, peak_wpm=0), "days": {}}


def is_empty(s):
    return not s["presses"] and not s["combos"] and not any(s["totals"].get(k) for k in TOTALS)


def _generated_name(directory):
    """A session nobody has named goes by when it began."""
    base = datetime.datetime.now().strftime("%Y-%m-%d-%H%M")
    name, n = base, 1
    while os.path.exists(path_of(directory, name)):
        n += 1
        name = f"{base}-{n}"
    return name


# ---------- the files ----------

@contextlib.contextmanager
def locked(directory):
    """The sessions' lock: the feed adding counts and a command rewriting a file never interleave.
    It is taken on `.lock`, not on a session: a write replaces its file, and a lock on the file it
    replaced would hold nothing."""
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd = os.open(os.path.join(directory, ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)   # closing lets it go


def write_json(path, obj):
    """Replace `path` whole or not at all: written beside it (mkstemp's 0600), flushed to the disk,
    then renamed over it. A crash leaves the old file or the new one, never half of either."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, sort_keys=True, indent=1)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _quarantine(path, why, log):
    aside = f"{path}.corrupt-{int(time.time())}"
    with contextlib.suppress(OSError):
        os.replace(path, aside)
    (log or _warn)(f"sessions: {path} {why}; kept it as {aside} and went on without it")


def _read_json(path, log):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        _quarantine(path, f"could not be read ({e})", log)
        return None


def read_session(path, log=None):
    """A session file, or None when there is none. One that no longer parses, or is not a session,
    is moved aside rather than written over."""
    s = _read_json(path, log)
    if s is None:
        return None
    if not (isinstance(s, dict) and isinstance(s.get("id"), str) and isinstance(s.get("presses"), dict)
            and isinstance(s.get("combos"), dict) and isinstance(s.get("totals"), dict)):
        _quarantine(path, "is not a session", log)
        return None
    s["name"] = os.path.basename(path)[:-len(".json")]   # a file renamed by hand is its new name
    s.setdefault("gen", 0)
    s.setdefault("named", True)
    s.setdefault("keyboards", [])
    s.setdefault("keymap", "")
    s.setdefault("layers", [])
    for kind in MAPS + ("days",):
        if not isinstance(s.get(kind), dict):
            s[kind] = {}             # a session from before the key times, or the days, were kept
    for k in TOTALS + ("peak_wpm",):
        s["totals"].setdefault(k, 0)
    return s


def _read_state(directory, log):
    st = _read_json(os.path.join(directory, ".state.json"), log)
    st = st if isinstance(st, dict) else {}
    if st.get("heatmap") not in HEATMAP_MODES:
        st["heatmap"] = "live"
    return st


def _write_state(directory, st):
    write_json(os.path.join(directory, ".state.json"),
               {"version": VERSION, "active": st.get("active"), "heatmap": st.get("heatmap", "live")})


def sessions(directory, log=None):
    """name -> session, for every session there is."""
    out = {}
    names = sorted(os.listdir(directory)) if os.path.isdir(directory) else []
    for fn in names:
        if fn.endswith(".json") and NAME.match(fn[:-len(".json")]):
            s = read_session(os.path.join(directory, fn), log)
            if s is not None:
                out[s["name"]] = s
    return out


def active(directory, log=None):
    """(state, session): the active one, made when there is none. Call with the lock held."""
    st = _read_state(directory, log)
    name = st.get("active")
    s = read_session(path_of(directory, name), log) if isinstance(name, str) and NAME.match(name) else None
    if s is None:
        # Pointing nowhere -- the first run, a file deleted by hand: the newest there is, else a new one.
        every = sessions(directory, log)
        if every:
            s = every[max(every, key=lambda n: every[n].get("updated", ""))]
        else:
            s = empty(_generated_name(directory), False)
            write_json(path_of(directory, s["name"]), s)
        st["active"] = s["name"]
        _write_state(directory, st)
    return st, s


def peek(directory=None, log=None):
    """(state, active session or None), reading only: what `status` shows without making anything."""
    directory = directory or default_dir()
    if not os.path.isdir(directory):
        return {"heatmap": "live"}, None
    st = _read_state(directory, log)
    name = st.get("active")
    s = read_session(path_of(directory, name), log) if isinstance(name, str) and NAME.match(name) else None
    return st, s


def add(s, delta):
    """Counts the page reported, added to a session in place."""
    for kind in MAPS:
        for layer, counts in (delta.get(kind) or {}).items():
            into = s[kind].setdefault(layer, {})
            for k, n in counts.items():
                into[k] = into.get(k, 0) + n
    for k in TOTALS:
        s["totals"][k] = s["totals"].get(k, 0) + (delta.get(k) or 0)
    s["totals"]["peak_wpm"] = max(s["totals"].get("peak_wpm", 0), delta.get("peak_wpm") or 0)


def add_counts(directory, sid, gen, delta, keyboards=(), keymap="", log=None, layers=None):
    """Add `delta` to the session whose id is `sid`, if it is still at `gen`, and write it. A
    session reset since (a higher gen) or deleted takes nothing: those keys were typed into what
    the user threw away. `layers` are the keymap's, the one the counts were typed with. Returns the
    session as written, or None."""
    with locked(directory):
        every = sessions(directory, log)
        s = next((x for x in every.values() if x["id"] == sid), None)
        if s is None or s["gen"] != gen:
            return None
        add(s, delta)
        s["keyboards"] = sorted(set(s["keyboards"]) | {k for k in keyboards if k})
        if keymap:
            s["keymap"] = keymap
        if layers:
            s["layers"] = list(layers)
        if delta.get("presses") or delta.get("combos") or any(delta.get(k) for k in TOTALS):
            s["updated"] = _now()        # when something was typed, not when the layers were written
            add_day(s, delta)
        write_json(path_of(directory, s["name"]), s)
        return s


# ---------- what the commands do ----------

def status(directory=None, log=None):
    directory = directory or default_dir()
    with locked(directory):
        return active(directory, log)


def new(directory=None, name=None, log=None):
    """Start a fresh session and make it the active one. The one before stays saved -- unless it
    is empty and nobody named it, which is nothing worth keeping."""
    directory = directory or default_dir()
    with locked(directory):
        st, current = active(directory, log)
        if name is not None and os.path.exists(path_of(directory, name)):
            raise SessionError(f"there is a session called {name} already; `zmk-layer-hud session load {name}` resumes it")
        if is_empty(current) and not current.get("named"):
            with contextlib.suppress(OSError):
                os.unlink(path_of(directory, current["name"]))
        s = empty(name if name is not None else _generated_name(directory), name is not None)
        write_json(path_of(directory, s["name"]), s)
        st["active"] = s["name"]
        _write_state(directory, st)
        return s


def save(directory=None, name=None, log=None):
    """Name the active session. One with no name of its own yet takes this one and stays the same
    session. One that has a name keeps what was typed so far under it, and typing goes on in a
    copy under the new name, which is now the active one."""
    directory = directory or default_dir()
    check_name(name)
    with locked(directory):
        st, s = active(directory, log)
        if name == s["name"]:
            s["named"] = True
            write_json(path_of(directory, name), s)
            return s
        if os.path.exists(path_of(directory, name)):
            raise SessionError(f"there is a session called {name} already")
        old = s["name"]
        if s.get("named"):
            s = json.loads(json.dumps(s))
            s.update(id=os.urandom(8).hex(), gen=0, name=name, updated=_now())
            write_json(path_of(directory, name), s)
        else:
            s.update(name=name, named=True, updated=_now())
            write_json(path_of(directory, name), s)
            os.unlink(path_of(directory, old))
        st["active"] = name
        _write_state(directory, st)
        return s


def load(directory=None, name=None, log=None):
    """Make a saved session the active one again: typing adds to it from now on."""
    directory = directory or default_dir()
    check_name(name)
    with locked(directory):
        st, current = active(directory, log)
        s = read_session(path_of(directory, name), log)
        if s is None:
            raise SessionError(f"there is no session called {name}; `zmk-layer-hud session list` shows them")
        if name != current["name"] and is_empty(current) and not current.get("named"):
            with contextlib.suppress(OSError):
                os.unlink(path_of(directory, current["name"]))
        st["active"] = name
        _write_state(directory, st)
        return s


def reset(directory=None, log=None):
    """Zero the active session's counts. It stays the same session; `gen` says it was reset, so
    counts still on their way from before are dropped rather than added to the new zero."""
    directory = directory or default_dir()
    with locked(directory):
        _, s = active(directory, log)
        fresh = empty(s["name"], s.get("named"))
        fresh.update(id=s["id"], gen=s["gen"] + 1, created=s["created"])
        write_json(path_of(directory, s["name"]), fresh)
        return fresh


def delete(directory=None, name=None, log=None):
    directory = directory or default_dir()
    check_name(name)
    with locked(directory):
        _, current = active(directory, log)
        if name == current["name"]:
            raise SessionError(f"{name} is the active session; start or load another one first")
        if not os.path.exists(path_of(directory, name)):
            raise SessionError(f"there is no session called {name}")
        os.unlink(path_of(directory, name))


def set_heatmap(directory=None, mode=None, log=None):
    if mode not in HEATMAP_MODES:
        raise SessionError(f"{mode!r} is not a heatmap: {', '.join(HEATMAP_MODES)}")
    directory = directory or default_dir()
    with locked(directory):
        st, _ = active(directory, log)
        st["heatmap"] = mode
        _write_state(directory, st)


def orphans(s):
    """The layers a session has counts on that the keymap it was last typed with does not draw,
    as {layer: (presses, combos)}: a layer renamed or taken out since. Nothing when no keymap has
    said which layers it has (a session from before they were written down)."""
    known = set(s.get("layers") or [])
    if not known:
        return {}
    out = {}
    for i, kind in enumerate(("presses", "combos")):
        for layer, m in s[kind].items():
            if layer not in known and m:
                out.setdefault(layer, [0, 0])[i] += sum(m.values())
    return {layer: tuple(n) for layer, n in sorted(out.items())}


def rename_layer(directory=None, old=None, new=None, every=False, log=None):
    """Move what was counted on layer `old` to layer `new`, in the active session or in `every`
    one: a layer the keymap renamed keeps what was typed on it. Counts already under the new name
    are added to. Returns [(session, presses moved, combos moved)]."""
    directory = directory or default_dir()
    for layer in (old, new):
        if not isinstance(layer, str) or not layer.strip() or len(layer) > 64:
            raise SessionError(f"{layer!r} is not a layer name")
    if old == new:
        raise SessionError(f"{old} is already called {new}")
    with locked(directory):
        _, current = active(directory, log)
        moved = []
        for s in (sessions(directory, log).values() if every else [current]):
            n = [0, 0]
            for kind in MAPS:
                src = s[kind].pop(old, None) or {}
                into = s[kind].setdefault(new, {}) if src else None
                for k, c in src.items():
                    into[k] = into.get(k, 0) + c
                    if kind in ("presses", "combos"):
                        n[kind == "combos"] += c
            if any(n):
                write_json(path_of(directory, s["name"]), s)
                moved.append((s["name"], n[0], n[1]))
        if not moved:
            where = "any session" if every else current["name"]
            raise SessionError(f"nothing is counted on a layer called {old} in {where}")
        return moved


def _numbers(presses, combos, members, t):
    """The numbers the commands print, from keys, combos (and their keys) and the totals."""
    wpm = round(t["active_net"] / 5 / (t["active_ms"] / 60000)) if t.get("active_ms", 0) >= 10000 else None
    sfb = t["sfb"] / t["bigrams"] if t.get("bigrams", 0) >= SFB_MIN else None
    strokes = presses - members + combos            # a combo is one keystroke made with several keys
    return {"presses": presses, "combos": combos, "chars": t.get("chars", 0), "deleted": t.get("deleted", 0),
            "sfb": sfb, "active_ms": t.get("active_ms", 0), "wpm": wpm, "peak_wpm": t.get("peak_wpm") or None,
            "combo_share": combos / strokes if strokes > 0 else None,
            "accuracy": max(0.0, 1 - t.get("deleted", 0) / t["chars"]) if t.get("chars") else None}


def _combo_counts(s):
    combos = sum(n for m in s["combos"].values() for n in m.values())
    members = sum(n * len(k.split(",")) for m in s["combos"].values() for k, n in m.items())
    return combos, members


def summary(s):
    """The numbers `zmk-layer-hud session` prints for a session."""
    presses = sum(n for m in s["presses"].values() for n in m.values())
    return _numbers(presses, *_combo_counts(s), s["totals"])


# ---------- days ----------

def _today():
    return datetime.date.today().isoformat()     # the typist's own day, not UTC's


def add_day(s, delta, day=None):
    """What a batch added, into the session's day: its totals and nothing by key, so a day costs a
    line in the file however much was typed on it."""
    d = s.setdefault("days", {}).setdefault(day or _today(), {})
    d["presses"] = d.get("presses", 0) + sum(n for m in (delta.get("presses") or {}).values() for n in m.values())
    for m in (delta.get("combos") or {}).values():
        for k, n in m.items():
            d["combos"] = d.get("combos", 0) + n
            d["combo_keys"] = d.get("combo_keys", 0) + n * len(k.split(","))
    for k in TOTALS:
        d[k] = d.get(k, 0) + (delta.get(k) or 0)
    d["peak_wpm"] = max(d.get("peak_wpm", 0), delta.get("peak_wpm") or 0)


def history(s):
    """[(day, numbers)], oldest first: what the session typed each day it typed."""
    return [(day, _numbers(d.get("presses", 0), d.get("combos", 0), d.get("combo_keys", 0), d))
            for day, d in sorted((s.get("days") or {}).items())]


def all_days(every):
    """Every session's days added up, as one session's (for `session history --all`)."""
    out = {"days": {}}
    for s in every:
        for day, d in (s.get("days") or {}).items():
            into = out["days"].setdefault(day, {})
            for k, n in d.items():
                into[k] = max(into.get(k, 0), n) if k == "peak_wpm" else into.get(k, 0) + n
    return out


# ---------- the feed's side ----------

def _counts(value, depth, most=MAX_COUNT):
    """A reported count map, checked: {layer: {key: n}} with sane names and numbers, or None."""
    if not isinstance(value, dict) or len(value) > MAX_ENTRIES:
        return None
    out = {}
    for layer, m in value.items():
        if not isinstance(layer, str) or len(layer) > 64 or not isinstance(m, dict) or len(m) > MAX_ENTRIES:
            return None
        inner = {}
        for k, n in m.items():
            if not isinstance(k, str) or not re.match(r"^\d{1,3}(,\d{1,3}){0,%d}$" % depth, k):
                return None
            if not isinstance(n, int) or isinstance(n, bool) or not 0 <= n <= most:
                return None
            if n:
                inner[k] = n
        if inner:
            out[layer] = inner
    return out


def _batch(msg):
    """The counts in a page's tally message, checked, or None."""
    delta = {"presses": _counts(msg.get("presses", {}), 0), "combos": _counts(msg.get("combos", {}), 15),
             "ms": _counts(msg.get("ms", {}), 0, MAX_COUNT * 1000), "timed": _counts(msg.get("timed", {}), 0)}
    if any(m is None for m in delta.values()):
        return None
    for k in TOTALS + ("peak_wpm",):
        n = msg.get(k, 0)
        if not isinstance(n, int) or isinstance(n, bool) or not 0 <= n <= MAX_COUNT * 1000:
            return None
        delta[k] = n
    delta["peak_wpm"] = min(delta["peak_wpm"], 400)   # a peak past this is a stuck timer, not typing
    return delta


class Store:
    """The active session as a feed keeps it.

    What the page reports (`apply`) is kept here and added to the session's file every
    `flush_s`, under the lock (`flush`); the files are looked at every `poll_s`, so what a command
    did to them -- a session loaded, reset, renamed, a heatmap picked -- reaches the page within a
    second. Each change goes to the pages as {"kind": "session", ...} through `emit`, the feed's
    own. With `directory=False` nothing is written: the session lives as long as the process, the
    demo's.

    The page's batches carry the session and gen they were typed under, and a seq the page numbers
    them with; `acks` in each message says which the counts already include, so the page can show
    them together with what it has not sent without counting anything twice. Calls come from the
    page's thread and the WebSocket loop, and the timer runs on its own: everything is locked."""

    def __init__(self, emit, directory=None, flush_s=5.0, poll_s=1.0, log=None):
        self.emit, self.log = emit, log or _warn
        self.persist = directory is not False
        self.dir = (directory or default_dir()) if self.persist else None
        self.flush_s, self.poll_s = float(flush_s), float(poll_s)
        self.lock = threading.RLock()
        self.session = None
        self.heatmap = "live"
        self.pending = {}      # (id, gen) -> counts not yet written
        self.replaced = {}     # id -> (gen, when) of a session just replaced
        self.acks = {}         # page -> the last seq added
        self.devices = set()
        self.keymap = ""
        self.layers = []       # the keymap's, written into the session so that `session` can tell
                               # counts on a layer the keymap no longer has (orphans)
        self.stamp = None
        self._stop = threading.Event()
        self._thread = None
        self._flushed = time.monotonic()

    # -- lifecycle --

    def start(self):
        self.reload(announce=True)
        if self.persist:
            self._thread = threading.Thread(target=self._run, name="sessions", daemon=True)
            self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self.flush()

    def _run(self):
        while not self._stop.wait(self.poll_s):
            try:
                self.poll()
                if time.monotonic() - self._flushed >= self.flush_s:
                    self.flush()
            except Exception as e:  # a full disk, a directory removed under it: keep counting
                self.log(f"sessions: {type(e).__name__}: {e}")

    # -- the files --

    def _stamp(self):
        out = []
        for name in (".state.json", (self.session or {}).get("name", "") + ".json"):
            try:
                st = os.stat(os.path.join(self.dir, name))
                out.append((st.st_ino, st.st_mtime_ns, st.st_size))
            except OSError:
                out.append(None)
        return tuple(out)

    def reload(self, announce=False):
        """Read the active session (and the heatmap) again, and say so when anything changed."""
        with self.lock:
            before = self.session and (self.session["id"], self.session["gen"], self.session["name"])
            if self.persist:
                with locked(self.dir):
                    st, s = active(self.dir, self.log)
                self.heatmap = st["heatmap"]
            else:
                s = self.session or empty("demo", False)
            if self.session is not None and self.session["id"] != s["id"]:
                self.replaced[self.session["id"]] = (self.session["gen"], time.monotonic())
            self.session = s
            if self.persist:
                self.stamp = self._stamp()
            after = (s["id"], s["gen"], s["name"])
            if announce or before != after:
                self.announce()

    def poll(self):
        if self.persist and self._stamp() != self.stamp:
            self.reload(announce=True)

    def set_keymap(self, msg):
        """The keymap the pages draw with: its file's name and its layers go into the session with
        the next counts, or on their own at the next flush when they changed."""
        with self.lock:
            self.keymap = os.path.basename((msg or {}).get("source") or "") or self.keymap
            self.layers = list((msg or {}).get("layers") or {}) or self.layers

    def flush(self):
        """Add what the page reported to the files it belongs in. What cannot be written now (a
        full disk) is kept, and tried again next time."""
        with self.lock:
            pending, self.pending = self.pending, {}
            self._flushed = time.monotonic()
            if not self.persist:
                for (sid, gen), p in pending.items():
                    if sid == self.session["id"] and gen == self.session["gen"]:
                        add(self.session, _pending_as_delta(p))
                return
            mine = (self.session["id"], self.session["gen"])
            if self.layers and self.session.get("layers") != self.layers and mine not in pending:
                pending[mine] = _new_pending()   # the layers alone
            if not pending:
                return
            # Something else wrote since we last looked (a command): after our own write, poll must
            # still see it, so the snapshot is not taken over it.
            news = self._stamp() != self.stamp
            error = None
            for (sid, gen), p in pending.items():
                try:
                    written = add_counts(self.dir, sid, gen, _pending_as_delta(p), sorted(self.devices), self.keymap,
                                         self.log, layers=self.layers)
                except OSError as e:
                    error = e
                    _merge_delta(self.pending.setdefault((sid, gen), _new_pending()),
                                 _pending_as_delta(p))
                    continue
                if written is not None and written["id"] == self.session["id"]:
                    self.session = written
            if error is not None and not getattr(self, "_failing", False):
                self.log(f"sessions: cannot write to {self.dir} ({error}); counting on, and trying again")
            self._failing = error is not None
            if not news:
                self.stamp = self._stamp()   # our own write is not news

    # -- the page --

    def apply(self, msg):
        """A page's tally: its counts since the last one, for the session it believed active."""
        delta = _batch(msg)
        if delta is None:
            self.log("sessions: a tally that did not add up was dropped")
            return
        with self.lock:
            page, seq = str(msg.get("page") or "")[:64], msg.get("seq")
            if page and isinstance(seq, int) and seq <= self.acks.get(page, 0):
                return   # seen it: a resend after a reconnect
            sid, gen = msg.get("session"), msg.get("gen")
            now = time.monotonic()
            self.replaced = {k: v for k, v in self.replaced.items() if now - v[1] < LATE_S}
            if sid is None:
                sid, gen = self.session["id"], self.session["gen"]    # a page that has had no session yet
            elif sid != self.session["id"] and sid not in self.replaced:
                return   # a session long gone
            key = (sid, gen)
            into = self.pending.setdefault(key, _new_pending())
            _merge_delta(into, delta)
            if isinstance(msg.get("device"), str) and msg["device"]:
                self.devices.add(msg["device"][:64])
            if page and isinstance(seq, int):
                self.acks.pop(page, None)
                self.acks[page] = seq            # the page heard from last goes last
                while len(self.acks) > PAGES_KEPT:
                    self.acks.pop(next(iter(self.acks)))
            self.announce()

    def set_heatmap(self, mode):
        if mode not in HEATMAP_MODES:
            return
        with self.lock:
            if self.persist:
                news = self._stamp() != self.stamp
                set_heatmap(self.dir, mode, self.log)
                if not news:
                    self.stamp = self._stamp()
            self.heatmap = mode
            self.announce()

    def message(self):
        """The active session as the pages get it: what is written plus what is not yet."""
        with self.lock:
            s = json.loads(json.dumps(self.session))
            mine = self.pending.get((s["id"], s["gen"]))
            if mine:
                add(s, _pending_as_delta(mine))
            return {"kind": "session", "v": VERSION, "id": s["id"], "gen": s["gen"], "name": s["name"],
                    "named": s.get("named", False), "heatmap": self.heatmap, "presses": s["presses"],
                    "combos": s["combos"], "ms": s["ms"], "timed": s["timed"], "totals": s["totals"], "acks": dict(self.acks),
                    "keyboards": s.get("keyboards", []), "created": s.get("created"), "updated": s.get("updated")}

    def announce(self):
        self.emit(self.message())


# What the store holds per session until it is written: the counts, and the totals apart.
def _new_pending():
    return dict({kind: {} for kind in MAPS}, totals={})


def _merge_delta(into, delta):
    for kind in MAPS:
        for layer, counts in (delta.get(kind) or {}).items():
            m = into[kind].setdefault(layer, {})
            for k, n in counts.items():
                m[k] = m.get(k, 0) + n
    t = into["totals"]
    for k in TOTALS:
        t[k] = t.get(k, 0) + delta.get(k, 0)
    t["peak_wpm"] = max(t.get("peak_wpm", 0), delta.get("peak_wpm", 0))


def _pending_as_delta(p):
    return dict(p["totals"], **{kind: p[kind] for kind in MAPS})
