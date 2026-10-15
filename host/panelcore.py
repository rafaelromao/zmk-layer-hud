"""The panel: what the HUD's native window does with what its page tells it and with what the command
line asks, the same on macOS and Linux. Standard library only, and no window of its own: each
platform's panel (host/macos/panel.py, AppKit; host/linux/panel.py, GTK and layer-shell) is a Window
to it, and owns how the HUD is drawn, placed, moved and hidden there.

    panel = Panel(window, run_dir, typing, settings, said=...)
    panel.start()           # panel.json says the panel is there: the CLI and the bar icons read it
    panel.page(body)        # a page message, as the page posted it (hud.js)
    panel.wake()            # SIGUSR2 (panelstate.SIGNAL): show, hide, dock, `typing setup`
    panel.set_shown(on)     # and what the panel's own controls ask: a shortcut, a menu
    panel.set_dock(on)
    panel.tell(msg)         # to the page, kept for a reload: the touch keyboard's state
    panel.stop()

The panel's state -- shown, the page's WPM and stats column, docked, the touch keyboard's state --
is kept here and written to panel.json (host/panelstate.py) whenever it changes.
"""

import json
import time

import dock as dock_mod
import panelstate


WIDTH_MIN, HEIGHT_MIN, SIZE_MAX = 100, 50, 4000     # the page's layout, in its pixels


class Window:
    """What a platform's panel does for the panel, each a callable:

        show(on)        on a change: the HUD drawn, or drawn as nothing and taking no clicks
        front()         asked to show: on top again, its surface back if the compositor dropped it
        dock(on)        docked: placed along a screen's bottom -> the page's dock message, or None
                        with no screen to dock on; floating: placed back. Either way remembered
                        for the next start
        resize(w, h)    the page's size, checked; w None: the width it has
        no_drag(rects)  where the page's controls are, checked: a press elsewhere moves the window
        open(url)       in the browser
        tell(msg)       a message for the page, kept for a page loaded afresh
        typing_access() `zmk-layer-hud typing setup`: ask the system for leave to type
        let_go()        the page lets go of the keys its touch keyboard holds (hiding)
        close()         the page's ✕
    """

    NAMES = ("show", "front", "dock", "resize", "no_drag", "open", "tell", "typing_access", "close", "let_go")

    def __init__(self, **calls):
        missing = [n for n in self.NAMES if n not in calls]
        if missing or len(calls) != len(self.NAMES):
            raise TypeError(f"a window does {', '.join(self.NAMES)}; missing {missing}")
        self.__dict__.update(calls)


def starts_docked(remembered, settings, environ):
    """Docked from the start: asked to (`start --dock`, dock.start), or left docked last time."""
    return environ.get("ZMKHUD_DOCK") == "1" or bool(settings["dock"]["start"]) or bool(remembered)


class Panel:
    """`typing` is the touch keyboard's keys (host/inject.py Typing): released, started, stopped and
    pressed from here. `settings` -> the config's touch: and dock: sections (keymap.host_settings),
    read when they are needed. `said(msg)`: a page message of a kind the panel does not know -- the
    page's counts, its heatmap and preferences -- for whoever keeps the session, if anyone does
    here."""

    def __init__(self, window, run_dir, typing, settings, said=None, shown=True, log=print, clock=time.time):
        self.window, self.run, self.typing, self.settings = window, str(run_dir), typing, settings
        self.said, self.log, self.clock = said, log, clock
        self.shown = shown
        self.docked = False
        self.wpm = 0
        self.stats = None           # the page's stats column, once it has said it (panelstate.stats_of)
        self.extra = {}             # the touch keyboard's state (panelstate.EXTRA)

    # ---------- panel.json ----------

    def start(self):
        self.write()

    def stop(self):
        panelstate.remove(self.run)

    def write(self):
        panelstate.write(self.run, self.shown, wpm=self.wpm, stats=self.stats, extra=self.extra)

    # ---------- what the page says ----------

    def page(self, body, from_page=True):
        """A page message, as posted: a JSON string, or the bare "close" of the ✕. `from_page`: it
        came from the HUD's own page, which alone may type."""
        if body == "close":
            self.window.close()
            return
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except ValueError:
                return
        if not isinstance(body, dict):
            return
        msg, kind = body, body.get("kind")
        if kind == "hid":               # the touch keyboard: keys to press (docs/touch.md); none while hidden
            if from_page and self.shown:
                self.typing.hid(msg)
        elif kind == "size":
            self.resize(msg.get("width"), msg.get("height"))
        elif kind == "attach":          # the banner's attach/detach: docked as a touch keyboard, or floating
            if isinstance(msg.get("on"), bool):
                self.set_dock(msg["on"])
        elif kind == "dock":            # the board too big to type on here at its real size: a HUD, floating
            self.refused_dock(msg)
        elif kind == "hide":            # the page's hide button
            self.set_shown(False)
        elif kind == "site":            # the stats column's heading: the landing page, in the browser
            self.window.open(panelstate.SITE)
        elif kind == "wpm":             # the live WPM, for the bar icons
            self.set_wpm(msg.get("wpm"))
        elif kind == "stats":           # what its stats column shows, for the bar icons' menus
            self.set_stats(msg)
        elif kind == "nodrag":
            rects = msg.get("rects")
            if isinstance(rects, list):
                self.window.no_drag([tuple(float(v) for v in r) for r in rects if isinstance(r, list) and len(r) == 4
                                     and all(isinstance(v, (int, float)) for v in r)])
        elif self.said is not None:
            self.said(msg)

    def resize(self, width, height):
        """The page's layout, 100..4000 wide (or the width it has: None or 0) and 50..4000 tall --
        docked and minimized, a bar dock.BAR_HEIGHT tall. Anything else is a page mid-load or gone
        wrong, and would shrink the panel to nothing or grow it off every screen."""
        def within(v, low):
            return isinstance(v, (int, float)) and not isinstance(v, bool) and low <= v <= SIZE_MAX
        if within(height, dock_mod.BAR_HEIGHT if self.docked else HEIGHT_MIN) and (width in (None, 0) or within(width, WIDTH_MIN)):
            self.window.resize(width, height)

    def set_wpm(self, wpm):
        if isinstance(wpm, int) and wpm != self.wpm:
            self.wpm = wpm
            self.write()

    def set_stats(self, msg):
        stats = panelstate.stats_of(msg)
        if stats is not None and stats != self.stats:
            self.stats = stats
            self.write()

    # ---------- shown, docked ----------

    def set_shown(self, shown):
        """On screen or off it; the page goes on as before either way."""
        if shown:
            self.window.front()
        if shown != self.shown:
            self.shown = shown
            if not shown:
                # A hidden HUD holds no key down: the page lets go too, or the next of its heartbeats
                # would press again what it holds -- a locked modifier, system-wide, unseen.
                self.window.let_go()
                self.typing.release()
            self.window.show(shown)
        self.write()

    def set_dock(self, on, note=None):
        """Docked along the bottom of a screen, typing, or floating where it was. `note`: why it
        floats again when it is not asked to (the board too big for the screen at its real size)."""
        if on == self.docked:
            return
        self.typing.release()
        self.extra.pop("dock_note", None)
        if on:
            self.docked = True          # before it moves: a docked panel's place is not remembered
            msg = self.window.dock(True)
            if msg is None:             # no screen to dock on
                self.docked = False
                return
            self.tell(msg)
            if self.settings()["touch"]["enabled"]:
                self.typing.start()
        else:
            self.docked = False
            self.window.dock(False)
            self.tell({"kind": "dock", "on": False})
            self.typing.stop()
            if note:
                self.extra["dock_note"] = note
        self.extra.update(dock=self.docked, dock_at=self.clock())
        self.write()

    def refused_dock(self, msg):
        """The page's word that its board, at its real size, does not fit the dock (hud.js dockFit):
        the HUD floats again, a HUD and not a keyboard, and says why."""
        note = dock_mod.refusal(msg, self.settings()["dock"]["height_pct"])
        if note and self.docked:
            self.log(f"not docked: {note}")
            self.set_dock(False, note)

    def tell(self, msg):
        """What the panel tells its page (docked or not, whether it may type), kept for a reload; the
        typing state goes into panel.json too."""
        self.window.tell(msg)
        if msg.get("kind") == "typing" and self.extra.get("typing") != msg.get("state"):
            self.extra["typing"] = msg["state"]
            self.write()

    # ---------- what the command line asks ----------

    def wake(self):
        """`zmk-layer-hud show` / `hide` (panel.want), `dock` (dock.want), `typing setup`
        (typing.want), each written before the signal that brings the panel here."""
        want = panelstate.wanted(self.run)
        if want is not None:
            self.set_shown(want)
        if panelstate.take_want(self.run, "typing") == "request":
            self.window.typing_access()
        dock = panelstate.take_want(self.run, "dock")
        if dock is not None:
            self.set_dock(bool(dock))
