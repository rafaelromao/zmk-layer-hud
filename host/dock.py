"""Where the docked HUD goes and how tall it may be: the parts of docking that are decisions, not
windows, kept apart from the panels so they can be tested without a compositor.

Docked, the HUD is a strip along the bottom of one screen, as wide as the screen, the way an
operating system's onboard keyboard is (docs/touch.md). On Hyprland it reserves its height, so the
tiled windows above make room; macOS has no such thing for an app to ask, so there it is an overlay
over the bottom of the screen, as Apple's own Accessibility Keyboard is.

The screen, on Hyprland: the one `dock.screen` names; else the one Hyprland maps touches to
(`input:touchdevice:output`); else, where a touch screen is connected, the laptop's or tablet's own
panel (eDP, LVDS, DSI), which is where touch screens are; else the focused one. The floating HUD
picks the other way round (host/linux/panel.py pick_monitor): there the laptop's panel is the one
to avoid.

Stdlib only, and Python 3.9: the Linux panel runs under the system interpreter.
"""

BUILT_IN = ("eDP", "LVDS", "DSI")
MIN_HEIGHT = 120           # a strip shorter than this is no keyboard
DEFAULT_PCT = 40           # of the screen's height, at most


def choose_monitor(monitors, devices=None, touch_output=None, wanted=""):
    """Hyprland's monitor (an entry of `hyprctl monitors -j`) to dock on, or None when there is none.
    `devices` is `hyprctl devices -j`, `touch_output` the value of input:touchdevice:output."""
    live = [m for m in monitors or [] if isinstance(m, dict) and not m.get("disabled")]
    if not live:
        return None
    if wanted:
        w = wanted.lower()
        for m in live:
            if w == str(m.get("name", "")).lower() or w in str(m.get("description", "")).lower():
                return m
    if touch_output and touch_output not in ("[[Auto]]", "[[EMPTY]]"):
        for m in live:
            if m.get("name") == touch_output:
                return m
    if (devices or {}).get("touch"):
        for m in live:
            if str(m.get("name", "")).startswith(BUILT_IN):
                return m
    return next((m for m in live if m.get("focused")), live[0])


def max_height(screen_h, pct=DEFAULT_PCT):
    """The tallest the strip may be on a screen this tall."""
    return int(max(MIN_HEIGHT, screen_h * (pct or DEFAULT_PCT) / 100))


def height(page_h, screen_h, pct=DEFAULT_PCT):
    """How tall the strip is: what the page needs, within the screen's share; the share itself while
    the page has not said."""
    cap = max_height(screen_h, pct)
    if not page_h:
        return cap
    return int(max(MIN_HEIGHT, min(page_h, cap)))
