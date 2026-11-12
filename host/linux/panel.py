#!/usr/bin/env python3
"""zmk-layer-hud Linux host: transparent Wayland (layer-shell) surfaces, layer HUD with the
typed-keys strip below it, plus hudfeed.py for layers, keys and daemon decisions. What the page
says and the command line asks are the panel's (host/panelcore.py); these surfaces are its window
here.

By default the HUD is an overlay: it floats over whatever is on screen and takes no room from
it, which is what a HUD should do to a session you are working in.

ZMKHUD_RESERVE=1 (./start.sh --reserve) adds a full-height rail on the right with an exclusive
zone, so the compositor tiles windows beside the HUD instead of under it. That is for recording
-- the zmk-vim-mode showcase needs the editor never to sit behind the board -- and it rearranges
every window on the output, which is too much for a HUD to do because it was started."""

import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys

# WebKit's DMA-BUF renderer triggers a Wayland protocol error on NVIDIA hosts when it hands the
# compositor GPU buffers. Shared-memory buffers avoid that and keep the transparent background.
# Not WEBKIT_DISABLE_DMABUF_RENDERER: from WebKitGTK 2.54 on, that fallback repaints only the
# damaged rects, each on an opaque backdrop, so the panel draws as dark boxes around whatever changed.
os.environ.setdefault("WEBKIT_DMABUF_RENDERER_FORCE_SHM", "1")

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("WebKit2", "4.1")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, GLib, Gtk, GtkLayerShell, WebKit2
# A hidden HUD takes no clicks through an empty input region, which is a cairo one: PyGObject hands
# it over only with pycairo (python-cairo).
gi.require_foreign("cairo")
import cairo  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import panelcore  # noqa: E402  (host/panelcore.py)
import panelstate  # noqa: E402  (host/panelstate.py)

ROOT = Path(__file__).resolve().parent.parent.parent
PAGES = ROOT / "hud"
# Outside the tree, because `zmk-layer-hud update` replaces the tree wholesale. hud.sh exports
# ZMKHUD_STATE; panelstate works out the default, so running this directly still works.
RUN = Path(panelstate.default_dir())
# Until the page says how big it is (follow_page): the board, the banner and one row of the stats
# bar above them (hud/hud.css #stats: 26px + 6px).
STATS_H = 32
HUD_W, HUD_H = 598, 392 + STATS_H
KEYS_W, KEYS_H = 598, 96
KEYS_GAP = 8
INSET = 8
WINDOWS = []
# Taking room from every window on the output is a recording decision, not a HUD one.
RESERVE = os.environ.get("ZMKHUD_RESERVE") == "1"
# The session takes counts only from the page this panel shows: the feed and that page are given
# this, and a browser pointed at the socket is not.
TALLY_TOKEN = secrets.token_hex(16)
# The socket's own token (hudfeed.Hub.process_request): in the pages' socket URLs and in the feed's
# environment. The feed writes it to $STATE/token for `zmk-layer-hud poke`.
SOCKET_TOKEN = secrets.token_hex(16)
# `zmk-layer-hud start --hidden`: off screen from the first frame.
HIDDEN = os.environ.get("ZMKHUD_HIDDEN") == "1"
# Hidden is drawn as nothing, not unmapped. An unmapped view's page is a hidden page to WebKit,
# which slows its timers, and the page's timers are what count (hud.js): so the surfaces stay,
# painting nothing and taking no input, and the page runs as it does on screen.
HIDE_SHEET = WebKit2.UserStyleSheet.new("* { visibility: hidden !important; }",
                                        WebKit2.UserContentInjectedFrames.ALL_FRAMES,
                                        WebKit2.UserStyleLevel.USER, None, None)


# Where the HUD was dragged to (drag, in main), kept with the macOS panel's frame.
POSITION = Path.home() / ".config/zmk-layer-hud/state.json"
# ZMKHUD_DRAG=1 logs presses, moves, saves and the page's size reports to panel.log: the only way
# to see what a drag did without a hand on the pointer (and to tell an event problem from a math
# one).
DEBUG = os.environ.get("ZMKHUD_DRAG") == "1"


def load_position():
    try:
        p = json.loads(POSITION.read_text()).get("linux") or {}
        return int(p["top"]), int(p["right"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def save_position(top, right):
    if DEBUG:
        print(f"drag: save position ({top}, {right})", file=sys.stderr, flush=True)
    try:
        try:
            state = json.loads(POSITION.read_text())
        except (OSError, ValueError):
            state = {}
        state = state if isinstance(state, dict) else {}
        state["linux"] = {"top": top, "right": right}
        panelstate.write_json(str(POSITION), state)     # whole or not at all, and this user's alone
    except OSError as e:
        print(f"panel: could not save {POSITION}: {e}", file=sys.stderr, flush=True)


def compositor_monitors():
    """The compositor's monitors as a list of dicts -- name, x, y, and reserved room where it
    says it (Hyprland does; niri's IPC does not, and none is assumed) -- in logical coordinates,
    as GDK's geometry has them. niri's own IPC where this is niri's session (NIRI_SOCKET), else
    Hyprland's; [] where neither answers, which pick_monitor then says."""
    if os.environ.get("NIRI_SOCKET"):
        try:
            got = json.loads(subprocess.check_output(["niri", "msg", "--json", "outputs"], timeout=5))
        except (OSError, subprocess.SubprocessError, ValueError):
            return []
        out = []
        for m in (got.values() if isinstance(got, dict) else []):
            logical = (m.get("logical") or {}) if isinstance(m, dict) else {}
            if m.get("name") and logical.get("x") is not None and logical.get("y") is not None:
                out.append({"name": m["name"], "x": logical["x"], "y": logical["y"], "reserved": [0, 0, 0, 0]})
        return out
    try:
        monitors = json.loads(subprocess.check_output(["hyprctl", "monitors", "-j"], timeout=5))
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    return monitors if isinstance(monitors, list) else []


def pick_monitor():
    """(the compositor's monitor, GDK's) for the HUD: the first that is not the laptop's own (eDP),
    else the laptop's, matched by position rather than by assuming the compositor and GDK number
    them alike. (None, None) when the compositor says nothing, (info, None) while GDK does not
    have it."""
    monitors = compositor_monitors()
    if not monitors:
        return None, None
    info = next((m for m in monitors if not m["name"].startswith("eDP")), monitors[0])
    display = Gdk.Display.get_default()
    for i in range(display.get_n_monitors()):
        m = display.get_monitor(i)
        if (m.get_geometry().x, m.get_geometry().y) == (info["x"], info["y"]):
            return info, m
    return info, None


def surface_there():
    """Whether the compositor has the HUD's surface. A panel can outlive its surface unseen, told
    to show a HUD there is nothing left of; True when the compositor cannot be asked."""
    try:
        if os.environ.get("NIRI_SOCKET"):
            layers = json.loads(subprocess.check_output(["niri", "msg", "--json", "layers"], timeout=5))
            if not isinstance(layers, list):
                return True
            return any(isinstance(e, dict) and e.get("namespace") == "zmkhud-layer" for e in layers)
        layers = json.loads(subprocess.check_output(["hyprctl", "layers", "-j"], timeout=5))
    except (OSError, subprocess.SubprocessError, ValueError):
        return True
    return any(e.get("namespace") == "zmkhud-layer"
               for m in layers.values() if isinstance(m, dict)
               for entries in (m.get("levels") or {}).values() for e in entries)


# A page whose web process went away -- crashed, or killed for its memory -- leaves an empty view,
# and an empty view is a transparent one: the HUD is gone while the panel goes on as if it were
# there. The page is loaded again, the hide sheet (the view's, not the page's) keeping a hidden
# HUD hidden, and a page that keeps going away is tried again less and less often.
RELOADS = {}


def reload_page(view, reason):
    if reason == getattr(WebKit2.WebProcessTerminationReason, "TERMINATED_BY_API", None):
        return
    n = RELOADS[view] = RELOADS.get(view, 0) + 1
    delay = min(60, 2 ** n)
    print(f"panel: the page's web process went away ({reason.value_nick}); loading it again in {delay} s",
          file=sys.stderr, flush=True)
    GLib.timeout_add_seconds(delay, lambda: view.reload() or False)


def surface(monitor, page, namespace, width, height, query="", on_size=None):
    """A layer surface with the page on it, and the page's UserContentManager, which is where the
    hide sheet goes."""
    window = Gtk.Window()
    window.set_app_paintable(True)
    window.set_visual(window.get_screen().get_rgba_visual())
    window.set_default_size(width, height)
    GtkLayerShell.init_for_window(window)
    GtkLayerShell.set_namespace(window, namespace)
    GtkLayerShell.set_monitor(window, monitor)
    GtkLayerShell.set_layer(window, GtkLayerShell.Layer.TOP)
    GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.NONE)

    sheet = ("html, body, #keys { background: transparent !important; }"
             "html, body { overflow: hidden !important; }"
             "#hud, #keys .chip { border-color: transparent !important; }")
    # The strip is a surface of its own here (keys.html), so index.html's own #keys would draw
    # every chip a second time, in a second place, most of it clipped by this surface's height.
    # macOS has no second surface and that div is its only strip, so the page keeps it and this is
    # the one place it goes.
    if page == "index.html":
        sheet += "#keys { display: none !important; }"
    manager = WebKit2.UserContentManager()
    manager.add_style_sheet(WebKit2.UserStyleSheet.new(
        sheet, WebKit2.UserContentInjectedFrames.ALL_FRAMES,
        WebKit2.UserStyleLevel.USER, None, None))
    if HIDDEN:
        manager.add_style_sheet(HIDE_SHEET)     # before the page loads: not one frame shows
    if on_size is not None:
        # The page says how big its layout is (hud.js postSize), where its controls are, so a drag
        # starts anywhere else (postNoDrag), and that its hide button was pressed, all on this one
        # handler. Not on a handler called zmkhud:
        # that is the macOS panel's bridge, and a page that finds one reports its session's counts
        # through it instead of the socket, where the feed keeps them.
        manager.register_script_message_handler("zmkhudsize")
        manager.connect("script-message-received::zmkhudsize",
                        lambda _manager, result: on_size(result.get_js_value().to_string()))
    view = WebKit2.WebView.new_with_user_content_manager(manager)
    view.set_background_color(Gdk.RGBA(0, 0, 0, 0))
    view.connect("web-process-terminated", reload_page)
    view.set_size_request(width, height)
    view.load_uri((PAGES / page).as_uri() +
                  f"?ws=ws://127.0.0.1:{os.environ.get('ZMKHUD_PORT', '8766')}/{SOCKET_TOKEN}" + query)
    window.add(view)
    WINDOWS.append(window)
    return window, manager


def main():
    if not GtkLayerShell.is_supported():
        sys.exit("A Wayland compositor with layer-shell support is required")
    info, monitor = pick_monitor()
    if monitor is None:
        sys.exit(f"Cannot locate recording monitor {info['name'] if info else '(no compositor lists none)'} in GDK")

    css = Gtk.CssProvider()
    css.load_from_data(b"window, webview { background-color: transparent; }")
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css,
                                            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    # Where the HUD sits before a drag has saved a place: inside the client area of the monitor's
    # tiled windows, as Hyprland says it (niri's IPC does not say where its windows are in pixels,
    # so under niri it is the corner of the screen itself -- the first drag from there, and the
    # place is saved). Every failure falls back to the same corner rather than no panel at all.
    top = right = None
    if not os.environ.get("NIRI_SOCKET") and shutil.which("hyprctl"):
        try:
            windows = json.loads(subprocess.check_output(["hyprctl", "clients", "-j"], timeout=5))
            tiled = [w for w in windows if w["monitor"] == info["id"] and not w["floating"] and w["mapped"]]
            # Stay inside the client area plus an inset: the blue border remains visible.
            top = min((w["at"][1] - info["y"] for w in tiled), default=info["reserved"][1]) + INSET
            right_edge = max((w["at"][0] + w["size"][0] - info["x"] for w in tiled),
                             default=monitor.get_geometry().width - info["reserved"][2])
            right = monitor.get_geometry().width - right_edge + INSET
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, AttributeError):
            top = right = None
    if top is None:
        top = info["reserved"][1] + INSET
        right = info["reserved"][2] + INSET
    geo = monitor.get_geometry()
    # Where it was dragged to last, while that is still on this monitor.
    saved = load_position()
    if saved and 0 <= saved[0] <= geo.height - 50 and 0 <= saved[1] <= geo.width - 100:
        top, right = saved
    pos = [top, right]

    hud, hud_css = surface(monitor, "index.html", "zmkhud-layer", HUD_W, HUD_H, query=f"&tally={TALLY_TOKEN}",
                  on_size=lambda body: panel.page(body))
    GtkLayerShell.set_anchor(hud, GtkLayerShell.Edge.TOP, True)
    GtkLayerShell.set_anchor(hud, GtkLayerShell.Edge.RIGHT, True)
    # Explicit coordinates include the top bar; ignore other panels' exclusive zones.
    GtkLayerShell.set_exclusive_zone(hud, -1)
    GtkLayerShell.set_margin(hud, GtkLayerShell.Edge.TOP, top)
    GtkLayerShell.set_margin(hud, GtkLayerShell.Edge.RIGHT, right)

    # Only when asked: a full-height right rail gives layer-shell an unambiguous exclusive edge,
    # and the compositor tiles everything else beside it. The visible HUD stays a separate surface
    # so it keeps its compact top-right geometry either way.
    rail, rail_width = None, 0
    if RESERVE:
        rail = Gtk.Window()
        rail.set_app_paintable(True)
        rail.set_visual(rail.get_screen().get_rgba_visual())
        rail_width = HUD_W + right + INSET
        rail.set_size_request(rail_width, 1)
        GtkLayerShell.init_for_window(rail)
        GtkLayerShell.set_namespace(rail, "zmkhud-reserved")
        GtkLayerShell.set_monitor(rail, monitor)
        GtkLayerShell.set_layer(rail, GtkLayerShell.Layer.TOP)
        GtkLayerShell.set_keyboard_mode(rail, GtkLayerShell.KeyboardMode.NONE)
        for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM, GtkLayerShell.Edge.RIGHT):
            GtkLayerShell.set_anchor(rail, edge, True)
        GtkLayerShell.set_exclusive_zone(rail, rail_width)
        WINDOWS.append(rail)

    # Typed-keys strip sits below the HUD; it reserves no space of its own either way, so the
    # bottom of the screen is never taken from the windows under it.
    keys, keys_css = surface(monitor, "keys.html", "zmkhud-keys", KEYS_W, KEYS_H)
    GtkLayerShell.set_anchor(keys, GtkLayerShell.Edge.TOP, True)
    GtkLayerShell.set_anchor(keys, GtkLayerShell.Edge.RIGHT, True)
    GtkLayerShell.set_exclusive_zone(keys, -1)
    GtkLayerShell.set_margin(keys, GtkLayerShell.Edge.TOP, top + HUD_H + KEYS_GAP)
    GtkLayerShell.set_margin(keys, GtkLayerShell.Edge.RIGHT, right)

    size = [HUD_W, HUD_H]

    def take_input(window):
        # None is the whole surface again, a layer surface's own.
        window.input_shape_combine_region(None if panel.shown else cairo.Region())

    def show_window(want):
        """Drawn, or drawn as nothing and taking no input (panelcore.Panel.set_shown). The rail goes
        with the HUD, so a hidden one takes no room from the windows either."""
        for window, css in ((hud, hud_css), (keys, keys_css)):
            if want:
                css.remove_style_sheet(HIDE_SHEET)
            else:
                css.add_style_sheet(HIDE_SHEET)
            take_input(window)
            window.queue_draw()
        if rail is not None:
            rail.show_all() if want else rail.hide()

    def to_front():
        if not surface_there():
            remap("asked to show the HUD, and the compositor had no surface of it")

    no_drag = []

    def open_url(url):
        try:
            Gtk.show_uri_on_window(None, url, Gdk.CURRENT_TIME)
        except GLib.Error as e:
            print(f"panel: cannot open {url}: {e.message}", file=sys.stderr, flush=True)

    # Dragging. A layer surface is not the compositor's to move, so the panel moves it: a press
    # anywhere but on the page's controls (it says where they are, kind "nodrag") starts a drag,
    # and the margins follow the pointer until it is let go. The press never reaches the page.
    # The pointer's place on the layout is what no compositor's IPC here can say (Hyprland's
    # cursorpos, niri none), so the drag is worked out from where the pointer is on the surface
    # instead: each motion's coordinates differenced against the last one's are the pointer's
    # movement over that event, and the surface carrying that reading with it is what keeps the
    # difference honest -- a move that has not reached the commit yet is read against the old
    # place all the same, so it is only ever counted once (follow). A mouse reports motion
    # hundreds of times a second, so the movement is only counted on the event and one move a
    # frame applies it: done per event, they queue up and the HUD trails the pointer. The move
    # itself brings the motion back a second time (below), which is not the pointer at all.
    drag = {}

    def move_to(top, right):
        """The surfaces to (top, right) margins; what they actually moved by, which is less than
        asked at the screen's edges."""
        top = max(0, min(top, geo.height - size[1]))
        right = max(0, min(right, geo.width - size[0]))
        delta = (top - pos[0], right - pos[1])
        if delta == (0, 0):
            return delta
        pos[:] = [top, right]
        GtkLayerShell.set_margin(hud, GtkLayerShell.Edge.TOP, top)
        GtkLayerShell.set_margin(hud, GtkLayerShell.Edge.RIGHT, right)
        GtkLayerShell.set_margin(keys, GtkLayerShell.Edge.TOP, top + size[1] + KEYS_GAP)
        GtkLayerShell.set_margin(keys, GtkLayerShell.Edge.RIGHT, right)
        # Margins are the surface's double-buffered state: they take effect on its next commit,
        # and an idle page draws nothing of its own, so without this the panel would sit where it
        # was until something else next redrew -- while every pointer event in between were read
        # against that old place. Drawing both surfaces here is the commit.
        hud.queue_draw()
        keys.queue_draw()
        return delta

    def pressed(_view, event):
        if DEBUG:
            hit = any(x <= event.x <= x + w and y <= event.y <= y + h for x, y, w, h in no_drag)
            print(f"drag: press at ({event.x:.1f}, {event.y:.1f}) button={event.button} "
                  f"type={event.type} nodrag={hit} pos={pos}", file=sys.stderr, flush=True)
        if event.button != 1 or event.type != Gdk.EventType.BUTTON_PRESS:
            return False
        if any(x <= event.x <= x + w and y <= event.y <= y + h for x, y, w, h in no_drag):
            return False                # a control: the page has it
        drag.update(at=(event.x, event.y), echo=(event.x, event.y),
                    pending=[0.0, 0.0], pos=list(pos), ticking=False)
        return True

    def follow():
        if drag and drag["pending"] != [0.0, 0.0]:
            # The pointer moved `pending` over the last frame: on the surface, so down it is the
            # top margin (the surface follows it down), and right it is the right margin the other
            # way round (the surface is anchored by its right edge, so following right is a
            # smaller gap to it).
            before, pending = list(drag["pos"]), tuple(drag["pending"])
            d_top, d_right = move_to(drag["pos"][0] + drag["pending"][1],
                                     drag["pos"][1] - drag["pending"][0])
            drag["pos"] = list(pos)
            drag["pending"] = [0.0, 0.0]
            # `at` stays where the last event left it: the margins this moved only land on the
            # surface's next commit, so the motion read against them in the meantime is still
            # read against the old place -- carrying `at` forward here as well would count this
            # very move a second time, and again every frame it went uncommitted: the panel ran
            # away from the pointer as if the pointer had accelerated.
            if DEBUG:
                print(f"drag: pending={pending} moved {before}->{list(pos)} "
                      f"by ({d_top:.1f}, {d_right:.1f})", file=sys.stderr, flush=True)
        if drag:
            return GLib.SOURCE_CONTINUE
        return GLib.SOURCE_REMOVE

    def moved(_view, event):
        if not drag:
            return False
        here = (event.x, event.y)
        echo = drag.get("echo")
        # Every margin change configures the surface, and the toolkit then re-delivers the last
        # motion with a fresh timestamp but its coordinates as they were -- the pointer has not
        # moved at all. Differenced against `at`, which the surface's own move has just carried
        # forward, such an echo reads as exactly that move again: the panel would follow itself
        # for as long as the button is held. A real move always changes where the pointer is, so
        # one that has not is the echo and lets go of it.
        if echo is not None and abs(here[0] - echo[0]) < 1e-6 and abs(here[1] - echo[1]) < 1e-6:
            if DEBUG:
                print(f"drag: echo ({here[0]:.2f}, {here[1]:.2f}) let go", file=sys.stderr, flush=True)
            return True
        drag["echo"] = here
        if DEBUG:
            print(f"drag: motion ({event.x:.2f}, {event.y:.2f}) t={event.time} at={drag['at']}",
                  file=sys.stderr, flush=True)
        drag["pending"][0] += event.x - drag["at"][0]
        drag["pending"][1] += event.y - drag["at"][1]
        drag["at"] = here
        if not drag.get("ticking"):
            drag["ticking"] = GLib.timeout_add(16, follow)
        return True

    def released(_view, event):
        if not drag:
            return False
        tick = drag.get("ticking")
        if tick:
            GLib.source_remove(tick)
        follow()                    # what was counted since the last frame
        drag.clear()
        if rail is not None:
            rail.set_size_request(size[0] + pos[1] + INSET, 1)
            GtkLayerShell.set_exclusive_zone(rail, size[0] + pos[1] + INSET)
        save_position(*pos)
        return True

    view = hud.get_child()
    view.connect("button-press-event", pressed)
    view.connect("motion-notify-event", moved)
    view.connect("button-release-event", released)

    gripped = []        # not empty: the page's grip has moved the HUD's right edge, not yet saved

    def follow_page(width, height, grip=False):
        """The HUD's surface as big as the page's layout -- its width is the keyboard's own, the
        config's hud.width or the one the page's grip left it at (hud.js panelWidth), its height
        the board's and the stats bar's -- and the strip just below it, as on macOS. The size is
        checked first, and narrowed to the room there is (panelcore.Panel.resize).

        The HUD is anchored by its right edge, so a new width moves its left one. Not for the page's
        grip (grip): that is the bottom right corner, and it has to stay under the pointer, so the
        right margin gives up what the width gains and the left edge stays where it is. Where that
        leaves the HUD is saved once the grip is let go: the page then says its size without it."""
        try:
            w, h = int(width or size[0]), int(height or 0)
        except (ValueError, TypeError):
            return
        if DEBUG:
            print(f"drag: page says {w}x{h} grip={grip} pos={pos} size={size}",
                  file=sys.stderr, flush=True)
        if not grip and gripped:
            gripped.clear()
            save_position(*pos)
        if [w, h] == size:
            return
        wider = w - size[0]
        size[:] = [w, h]
        if grip and wider:
            gripped.append(True)
            move_to(pos[0], pos[1] - wider)
        hud.get_child().set_size_request(w, h)
        hud.resize(w, h)     # a layer surface is the window's size, smaller as well as larger
        keys.get_child().set_size_request(w, KEYS_H)
        keys.resize(w, KEYS_H)
        GtkLayerShell.set_margin(keys, GtkLayerShell.Edge.TOP, pos[0] + h + KEYS_GAP)
        if rail is not None:
            rail.set_size_request(w + pos[1] + INSET, 1)
            GtkLayerShell.set_exclusive_zone(rail, w + pos[1] + INSET)
        if not panel.shown:
            take_input(hud)
            take_input(keys)

    # The panel (host/panelcore.py): what the page says and the CLI asks, and panel.json; these
    # surfaces are how it is drawn and placed here.
    panel = panelcore.Panel(
        # room: from the HUD's right edge, the one it is anchored by and keeps, to its monitor's
        # left. For the page's grip it is the left edge that is kept (follow_page): from there to
        # the monitor's right, which is the HUD's width and its right margin.
        panelcore.Window(show=show_window, front=to_front, resize=follow_page,
                         room=lambda grip=False: size[0] + pos[1] if grip else geo.width - pos[1],
                         no_drag=lambda rects: no_drag.__setitem__(slice(None), rects), open=open_url,
                         close=lambda: quit_host()),
        RUN, shown=not HIDDEN)

    take_input(hud)       # kept until each is realized
    take_input(keys)
    if rail is not None and panel.shown:
        rail.show_all()
    keys.show_all()
    hud.show_all()
    room = f"reserved right {rail_width}px, bottom reclaimed" if rail is not None else \
        "overlay, nothing reserved (--reserve tiles windows beside it)"
    print(f"Panel on {info['name']}: {room}; "
          f"HUD inset top={top}, right={right}; keys below HUD", flush=True)

    # Its output going away -- a monitor asleep or unplugged, a suspend -- takes the HUD's surfaces
    # with it: the compositor closes them, and gtk-layer-shell hands that on as a delete event, which
    # would destroy the windows and leave a panel answering `show` with nothing. So a closed window
    # is kept, and the surfaces are put on the monitor the panel would pick now and mapped again
    # whenever monitors come or go, a surface is closed, or `show` finds the compositor without the HUD's.
    screen = {"monitor": monitor, "due": None}

    def remap(why):
        nonlocal geo                # move_to keeps the HUD on it
        screen["due"] = None
        info_now, mon = pick_monitor()
        if mon is None:
            print(f"panel: {why}; no monitor to put the HUD on yet", file=sys.stderr, flush=True)
            return False
        geo = mon.get_geometry()
        for window in [hud, keys] + ([rail] if rail is not None else []):
            window.hide()
            GtkLayerShell.set_monitor(window, mon)
        screen["monitor"] = mon
        move_to(*pos)               # kept on it, should it be a smaller one
        keys.show_all()
        hud.show_all()
        if rail is not None and panel.shown:
            rail.show_all()
        take_input(hud)
        take_input(keys)
        print(f"panel: {why}; the HUD is on {info_now['name']} again", flush=True)
        return False

    def remap_soon(why):
        # Monitors come and go in bursts, and the compositor places an output a moment after it appears.
        if screen["due"] is None:
            screen["due"] = GLib.timeout_add(1000, remap, why)

    def closed(_window, _event):
        remap_soon("the compositor closed the HUD's surface")
        return True                 # kept: a destroyed window could not come back

    for window in [hud, keys] + ([rail] if rail is not None else []):
        # From gtk-layer-shell 0.10 on, a closed surface is dropped unless asked for: the window
        # stays as it was, with no surface under it. Before, it always came as a delete event.
        if hasattr(GtkLayerShell, "set_respect_close"):
            GtkLayerShell.set_respect_close(window, True)
        window.connect("delete-event", closed)
    display = Gdk.Display.get_default()
    display.connect("monitor-added", lambda *_: remap_soon("a monitor came"))
    display.connect("monitor-removed",
                    lambda _display, gone: gone == screen["monitor"] and remap_soon("the HUD's monitor went away"))

    def quit_host(*_):
        Gtk.main_quit()
        return False

    for sig in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, quit_host)

    def asked(*_):
        # `zmk-layer-hud show` / `hide`: what they want is in panel.want.
        panel.wake()
        return GLib.SOURCE_CONTINUE
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, panelstate.SIGNAL, asked)
    # Only now, with the handler in: the CLI signals whatever pid this file names.
    panel.start()
    RUN.mkdir(mode=0o700, exist_ok=True)
    RUN.chmod(0o700)
    # The feed's log is this user's alone, like the rest of $STATE: O_CREAT leaves the mode of a
    # file from an earlier run as it was, so it is set again. ZMKHUD_DEBUG=1 adds the layer
    # messages with timestamps, as it does for the macOS panel; never the presses.
    log_path = RUN / "hudfeed.log"
    with os.fdopen(os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as output:
        os.chmod(log_path, 0o600)
        feed_python = os.environ.get("ZMKHUD_PYTHON", sys.executable)
        feed_debug = ["--debug"] if os.environ.get("ZMKHUD_DEBUG") == "1" else []
        feed = subprocess.Popen([feed_python, "-u", str(ROOT / "host" / "hudfeed.py"), *feed_debug],
                                stdout=output, stderr=output, start_new_session=True,
                                env=dict(os.environ, ZMKHUD_TALLY_TOKEN=TALLY_TOKEN, ZMKHUD_TOKEN=SOCKET_TOKEN))
    def watch_feed():
        if feed.poll() is not None:
            quit_host()
            return False
        return True
    GLib.timeout_add(250, watch_feed)
    try:
        Gtk.main()
    finally:
        panel.stop()
        for window in WINDOWS:
            window.destroy()
        # Include journalctl, including after the page's close request exits hudfeed.
        try:
            os.killpg(feed.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        feed.wait(timeout=5)


if __name__ == "__main__":
    main()
