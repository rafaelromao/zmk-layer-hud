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
MIN_HEIGHT = 120           # a screen's share shorter than this is no keyboard
BAR_HEIGHT = 30            # ...but minimized, the keyboard is its banner: a bar this tall
DEFAULT_PCT = 60           # of the screen's height, at most


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
    """How tall the strip is: what the page needs, within the screen's share -- as little as a bar,
    when the keyboard is minimized; the share itself while the page has not said."""
    cap = max_height(screen_h, pct)
    if not page_h:
        return cap
    return int(max(BAR_HEIGHT, min(page_h, cap)))


# CSS's own measure, 96 pixels to the inch: what a screen that does not say how big it is gets.
NOMINAL_PX_PER_MM = 96 / 25.4
# A screen measured outside these is not believed (a projector says 0): from about 50 to 760
# pixels to the inch. Nor are the sizes that are only an aspect ratio, which some TVs and
# projectors give as their size.
PX_PER_MM = (2.0, 30.0)
NOT_SIZES = {(16, 9), (16, 10), (160, 90), (160, 100)}


def px_per_mm(width_px, height_px, width_mm, height_mm):
    """How many of the dock's pixels -- the page's CSS pixels: GDK's and AppKit's logical pixels,
    which a scaled screen has fewer of than its panel -- make a millimetre on a screen this many
    pixels and millimetres across: (px_per_mm, True), or the nominal (NOMINAL_PX_PER_MM, False)
    when the screen does not say its size, or says one that cannot be. A turned screen whose
    millimetres are still given the other way round is taken the way its pixels are."""
    try:
        w, h, wmm, hmm = float(width_px), float(height_px), float(width_mm or 0), float(height_mm or 0)
    except (TypeError, ValueError):
        return NOMINAL_PX_PER_MM, False
    if w <= 0 or wmm <= 0 or tuple(sorted((round(wmm), round(hmm)), reverse=True)) in NOT_SIZES:
        return NOMINAL_PX_PER_MM, False
    if h > 0 and hmm > 0 and (h > w) != (hmm > wmm):
        wmm, hmm = hmm, wmm
    ppmm = w / wmm
    if not PX_PER_MM[0] <= ppmm <= PX_PER_MM[1]:
        return NOMINAL_PX_PER_MM, False
    return round(ppmm, 4), True


def _mm(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v < 10000


def refusal(msg, pct=DEFAULT_PCT):
    """The page's word that its board does not fit the dock, even with its keys made as small as
    touch.min_key_mm lets them be ({"kind": "dock", "fits": false, "key_mm", "want_mm" when that is
    smaller than touch.key_mm, "need_mm": [w, h], "room_mm": [w, h]}, hud.js dockFit) as the
    sentence the panel logs and `zmk-layer-hud dock` prints; None for a message that is not one."""
    if not isinstance(msg, dict) or msg.get("fits") is not False:
        return None
    need, room, key, want = msg.get("need_mm"), msg.get("room_mm"), msg.get("key_mm"), msg.get("want_mm")
    if not all(isinstance(p, list) and len(p) == 2 and all(_mm(v) for v in p) for p in (need, room)) or not _mm(key):
        return None
    where = (f"the screen is {room[0]:g} mm across" if need[0] > room[0] else
             f"the dock may be {room[1]:g} mm tall (dock.height_pct, {pct or DEFAULT_PCT}% of the screen)")
    size = (f"even at {key:g} mm a key, the smallest touch.min_key_mm lets them be (touch.key_mm is {want:g}), the"
            if _mm(want) and want > key else f"at {key:g} mm a key (touch.key_mm) the")
    return f"{size} board is {need[0]:g} x {need[1]:g} mm, and {where}"


def smaller(msg):
    """The page's word that its board docked with its keys made smaller than touch.key_mm to fit
    ({"kind": "dock", "fits": true, "key_mm", "want_mm"}) as what `zmk-layer-hud dock status` adds;
    "" when they are their own size again ({"fits": true, "key_mm"} alone); None for any other."""
    if not isinstance(msg, dict) or msg.get("fits") is not True or not _mm(msg.get("key_mm")):
        return None
    key, want = msg["key_mm"], msg.get("want_mm")
    return f"at {key:g} mm a key, smaller than touch.key_mm's {want:g} to fit" if _mm(want) and want > key else ""
