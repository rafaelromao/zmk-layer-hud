"""The panel: what the HUD's native window does with what its page tells it and with what the command
line asks, the same on macOS and Linux. Standard library only, and no window of its own: each
platform's panel (host/macos/panel.py, AppKit; host/linux/panel.py, GTK and layer-shell) is a Window
to it, and owns how the HUD is drawn, placed, moved and hidden there.

    panel = Panel(window, run_dir, said=...)
    panel.start()           # panel.json says the panel is there: the CLI and the bar icons read it
    panel.page(body)        # a page message, as the page posted it (hud.js)
    panel.wake()            # SIGUSR2 (panelstate.SIGNAL): show, hide
    panel.set_shown(on)     # and what the panel's own controls ask: a shortcut, a menu
    panel.stop()

The panel's state -- shown, the page's WPM and stats column -- is kept here and written to panel.json
(host/panelstate.py) whenever it changes.
"""

import json

import panelstate


WIDTH_MIN, HEIGHT_MIN, SIZE_MAX = 100, 50, 4000     # the page's layout, in its pixels


class Window:
    """What a platform's panel does for the panel, each a callable:

        show(on)        on a change: the HUD drawn, or drawn as nothing and taking no clicks
        front()         asked to show: on top again, its surface back if the compositor dropped it
        resize(w, h)    the page's size, checked; w None: the width it has
        room()          how wide the HUD may be where it is, in the page's pixels; None: not known
        no_drag(rects)  where the page's controls are, checked: a press elsewhere moves the window
        open(url)       in the browser
        close()         the page's ✕
    """

    NAMES = ("show", "front", "resize", "room", "no_drag", "open", "close")

    def __init__(self, **calls):
        missing = [n for n in self.NAMES if n not in calls]
        if missing or len(calls) != len(self.NAMES):
            raise TypeError(f"a window does {', '.join(self.NAMES)}; missing {missing}")
        self.__dict__.update(calls)


class Panel:
    """`said(msg)`: a page message of a kind the panel does not know -- the page's counts, its heatmap
    and preferences -- for whoever keeps the session, if anyone does here."""

    def __init__(self, window, run_dir, said=None, shown=True):
        self.window, self.run, self.said = window, str(run_dir), said
        self.shown = shown
        self.wpm = 0
        self.stats = None           # the page's stats column, once it has said it (panelstate.stats_of)

    # ---------- panel.json ----------

    def start(self):
        self.write()

    def stop(self):
        panelstate.remove(self.run)

    def write(self):
        panelstate.write(self.run, self.shown, wpm=self.wpm, stats=self.stats)

    # ---------- what the page says ----------

    def page(self, body):
        """A page message, as posted: a JSON string, or the bare "close" of the ✕."""
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
        if kind == "size":
            self.resize(msg.get("width"), msg.get("height"))
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
        """The page's layout, 100..4000 wide (or the width it has: None or 0) and 50..4000 tall.
        Anything else is a page mid-load or gone wrong, and would shrink the panel to nothing or grow
        it off every screen.

        The page asks for the width its keyboard is drawn at full size (hud.js panelWidth), which a
        small screen may not have: the panel is then as wide as there is room for, and the page
        draws the board to fit what it got."""
        def within(v, low):
            return isinstance(v, (int, float)) and not isinstance(v, bool) and low <= v <= SIZE_MAX
        if within(height, HEIGHT_MIN) and (width in (None, 0) or within(width, WIDTH_MIN)):
            room = self.window.room() if width else None
            if within(room, WIDTH_MIN) and room < width:
                width = room
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

    # ---------- shown ----------

    def set_shown(self, shown):
        """On screen or off it; the page goes on as before either way."""
        if shown:
            self.window.front()
        if shown != self.shown:
            self.shown = shown
            self.window.show(shown)
        self.write()

    # ---------- what the command line asks ----------

    def wake(self):
        """`zmk-layer-hud show` / `hide` (panel.want), written before the signal that brings the
        panel here."""
        want = panelstate.wanted(self.run)
        if want is not None:
            self.set_shown(want)
