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
import signal
import socket
import subprocess
import sys

# WebKit's DMA-BUF renderer triggers a Wayland protocol error on this NVIDIA host.
# Software composition also preserves the transparent webview background reliably.
os.environ.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")

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
import dock as dock_mod  # noqa: E402  (host/dock.py)
import inject  # noqa: E402  (host/inject.py)
import panelcore  # noqa: E402  (host/panelcore.py)
import panelstate  # noqa: E402  (host/panelstate.py)
import uinput  # noqa: E402  (host/linux/uinput.py)

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


def load_position():
    try:
        p = json.loads(POSITION.read_text()).get("linux") or {}
        return int(p["top"]), int(p["right"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def load_state():
    try:
        state = json.loads(POSITION.read_text())
    except (OSError, ValueError):
        state = {}
    state = state if isinstance(state, dict) else {}
    if not isinstance(state.get("linux"), dict):
        state["linux"] = {}
    return state


def write_state(state):
    try:
        panelstate.write_json(str(POSITION), state)     # whole or not at all, and this user's alone
    except OSError as e:
        print(f"panel: could not save {POSITION}: {e}", file=sys.stderr, flush=True)


def save_position(top, right):
    state = load_state()
    state["linux"].update(top=top, right=right)    # beside what else it keeps (docked or not)
    write_state(state)


def cursor():
    """Where the pointer is on the whole layout, from Hyprland's own socket. A layer surface
    knows only where the pointer is on itself, and that moves as the surface does."""
    sig, runtime = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"), os.environ.get("XDG_RUNTIME_DIR")
    if not (sig and runtime):
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            s.connect(f"{runtime}/hypr/{sig}/.socket.sock")
            s.sendall(b"j/cursorpos")
            data = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
        at = json.loads(data)
        return int(at["x"]), int(at["y"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def pick_monitor():
    """(Hyprland's monitor, GDK's) for the HUD: the first that is not the laptop's own (eDP), else
    the laptop's, matched by position rather than by assuming GDK and Hyprland number them alike.
    (None, None) when Hyprland says nothing, (info, None) while GDK does not have it."""
    try:
        monitors = json.loads(subprocess.check_output(["hyprctl", "monitors", "-j"], timeout=5))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None, None
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
    """Whether Hyprland has the HUD's surface, floating or docked. A panel can outlive its surface
    unseen, told to show a HUD there is nothing left of; True when Hyprland cannot be asked."""
    try:
        layers = json.loads(subprocess.check_output(["hyprctl", "layers", "-j"], timeout=5))
    except (OSError, subprocess.SubprocessError, ValueError):
        return True
    return any(e.get("namespace") in ("zmkhud-layer", "zmkhud-dock")
               for m in layers.values() if isinstance(m, dict)
               for entries in (m.get("levels") or {}).values() for e in entries)


def hypr(args):
    """What `hyprctl ARGS -j` says, or None."""
    try:
        return json.loads(subprocess.check_output(["hyprctl", *args, "-j"], timeout=5))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def gdk_monitor(info):
    """GDK's monitor for Hyprland's, matched by position."""
    display = Gdk.Display.get_default()
    for i in range(display.get_n_monitors()):
        m = display.get_monitor(i)
        if (m.get_geometry().x, m.get_geometry().y) == (info["x"], info["y"]):
            return m
    return None


def host_settings():
    """The config's touch:, dock: and remote: sections (host/keymap.py --host-settings): the
    venv's interpreter reads the YAML, which this one cannot. The defaults when it cannot be asked."""
    try:
        out = subprocess.check_output([os.environ.get("ZMKHUD_PYTHON", sys.executable),
                                       str(ROOT / "host" / "keymap.py"), "--host-settings"],
                                      timeout=30, stderr=subprocess.DEVNULL)
        return json.loads(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        return {"touch": {"enabled": 1}, "dock": {"screen": "", "height_pct": dock_mod.DEFAULT_PCT, "start": 0, "split": 1},
                "remote": {"port": 8768, "minutes": 30, "bind": ""}}


def load_dock():
    return bool(((load_state().get("linux") or {}).get("dock")))


def save_dock(on):
    state = load_state()
    state.setdefault("linux", {})["dock"] = bool(on)
    write_state(state)


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


def keep_to_pages(_view, decision, kind):
    """Only the HUD's own pages load in its views, and nothing opens a window. The page can type
    (the touch keyboard, through the handler below), so whatever is shown there must be it."""
    if kind == WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION:
        decision.ignore()
        return True
    if kind == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
        uri = decision.get_navigation_action().get_request().get_uri() or ""
        if not uri.startswith(PAGES.as_uri() + "/"):
            decision.ignore()
            return True
    return False


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
    view.get_settings().set_enable_webaudio(True)      # the touch keyboard's key clicks (hud/touch.js)
    view.connect("web-process-terminated", reload_page)
    view.connect("decide-policy", keep_to_pages)
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
        sys.exit(f"Cannot locate recording monitor {info['name'] if info else '(Hyprland lists none)'} in GDK")

    css = Gtk.CssProvider()
    css.load_from_data(b"window, webview { background-color: transparent; }")
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css,
                                            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    windows = json.loads(subprocess.check_output(["hyprctl", "clients", "-j"]))
    tiled = [w for w in windows if w["monitor"] == info["id"] and not w["floating"] and w["mapped"]]
    # Stay inside the client area plus an inset: the blue border remains visible.
    top = min((w["at"][1] - info["y"] for w in tiled), default=info["reserved"][1]) + INSET
    right_edge = max((w["at"][0] + w["size"][0] - info["x"] for w in tiled),
                     default=monitor.get_geometry().width - info["reserved"][2])
    right = monitor.get_geometry().width - right_edge + INSET
    geo = monitor.get_geometry()
    # Where it was dragged to last, while that is still on this monitor.
    saved = load_position()
    if saved and 0 <= saved[0] <= geo.height - 50 and 0 <= saved[1] <= geo.width - 100:
        top, right = saved
    pos = [top, right]

    hud, hud_css = surface(monitor, "index.html", "zmkhud-layer", HUD_W, HUD_H, query=f"&tally={TALLY_TOKEN}",
                  on_size=lambda body: panel.page(body))   # only its own pages load there (keep_to_pages)
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

    # What the panel itself tells its page (the feed tells it everything else, on the socket):
    # whether it may type, and docked or not. The latest of each is said again to a page loaded
    # afresh.
    told = {}
    page_view = hud.get_child()

    def run_js(script):
        try:
            page_view.evaluate_javascript(script, -1, None, None, None, None, None)
        except (AttributeError, TypeError):
            page_view.run_javascript(script, None, None, None)

    def tell_page(msg):
        told[msg["kind"]] = msg
        run_js(f"window.hud && hud.receive({json.dumps(msg)})")

    def page_loaded(_view, event):
        if event == WebKit2.LoadEvent.STARTED:
            typing.release()            # a page going away lets its keys go
        elif event == WebKit2.LoadEvent.FINISHED:
            for msg in list(told.values()):
                run_js(f"window.hud && hud.receive({json.dumps(msg)})")

    def make_keyboard():
        try:
            return uinput.Uinput().open()
        except OSError as e:
            raise inject.Unavailable(uinput.state_of(e), str(e)) from None

    # The touch keyboard's keys (host/inject.py): a virtual keyboard, made the first time the page
    # may type, its keys pressed from what the page sends ({"kind":"hid"}).
    typing = inject.Typing(make_keyboard, lambda msg: panel.tell(msg),
                           lambda seconds, fn: GLib.timeout_add(int(seconds * 1000), lambda: fn() or False),
                           log=lambda msg: print("panel:", msg, file=sys.stderr, flush=True))
    page_view.connect("load-changed", page_loaded)
    page_view.connect("web-process-terminated", lambda *_: typing.release())

    # ---------- docked: a strip along the bottom of a screen, as wide as it (host/dock.py) ----------
    settings = host_settings()
    pct = settings["dock"]["height_pct"]

    def strip_wanted():
        """The typed-keys strip is the HUD's, not the touch keyboard's: away while docked."""
        return not panel.docked
    dock_geo = [None]       # the dock's screen, as GDK has it
    dock_ppmm = [None]      # ...and how many of its pixels are a millimetre (dock.px_per_mm)
    dock_h = [0]            # the strip's height, and the room it reserves

    def dock_monitor():
        option = hypr(["getoption", "input:touchdevice:output"]) or {}
        info_d = dock_mod.choose_monitor(hypr(["monitors"]) or [], hypr(["devices"]) or {},
                                         option.get("str") if isinstance(option, dict) else None,
                                         settings["dock"]["screen"])
        return (info_d, gdk_monitor(info_d)) if info_d else (None, None)

    def size_dock(h):
        dock_h[0] = h
        page_view.set_size_request(dock_geo[0].width, h)
        hud.resize(dock_geo[0].width, h)
        # The room it takes from the windows above: none while hidden.
        GtkLayerShell.set_exclusive_zone(hud, h if panel.shown else 0)

    def apply_dock():
        info_d, mon = dock_monitor()
        if mon is None:
            print("panel: no screen to dock on", file=sys.stderr, flush=True)
            return False
        dock_geo[0] = mon.get_geometry()
        # The touch keyboard's keys are drawn their real size (hud.js dockFit): the screen's size, as
        # its EDID tells the compositor, over the pixels the page is laid out in.
        dock_ppmm[0], measured = dock_mod.px_per_mm(dock_geo[0].width, dock_geo[0].height,
                                                    mon.get_width_mm(), mon.get_height_mm())
        if not measured:
            print(f"panel: {info_d['name']} does not say how big it is; its keys drawn at 96 pixels to the inch",
                  file=sys.stderr, flush=True)
        # Named apart, so `hyprctl layers` and a layer rule can tell it from the floating HUD; on the
        # overlay layer, which a fullscreen window does not cover; and, as always, never the keyboard's.
        GtkLayerShell.set_namespace(hud, "zmkhud-dock")
        GtkLayerShell.set_monitor(hud, mon)
        GtkLayerShell.set_layer(hud, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_anchor(hud, GtkLayerShell.Edge.TOP, False)
        for edge in (GtkLayerShell.Edge.BOTTOM, GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
            GtkLayerShell.set_anchor(hud, edge, True)
        for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM, GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
            GtkLayerShell.set_margin(hud, edge, 0)
        size_dock(dock_mod.height(dock_h[0], dock_geo[0].height, pct))
        print(f"panel: docked on {info_d['name']}, {dock_h[0]}px reserved", flush=True)
        return True

    def apply_float():
        GtkLayerShell.set_namespace(hud, "zmkhud-layer")
        GtkLayerShell.set_monitor(hud, screen["monitor"])
        GtkLayerShell.set_layer(hud, GtkLayerShell.Layer.TOP)
        for edge, on in ((GtkLayerShell.Edge.TOP, True), (GtkLayerShell.Edge.RIGHT, True),
                         (GtkLayerShell.Edge.BOTTOM, False), (GtkLayerShell.Edge.LEFT, False)):
            GtkLayerShell.set_anchor(hud, edge, on)
            GtkLayerShell.set_margin(hud, edge, 0)
        GtkLayerShell.set_margin(hud, GtkLayerShell.Edge.TOP, pos[0])
        GtkLayerShell.set_margin(hud, GtkLayerShell.Edge.RIGHT, pos[1])
        GtkLayerShell.set_exclusive_zone(hud, -1)
        page_view.set_size_request(*size)
        hud.resize(*size)

    def dock_message():
        msg = {"kind": "dock", "on": True, "max_height": dock_mod.max_height(dock_geo[0].height, pct),
               "split": bool(settings["dock"]["split"])}
        if settings["touch"]["enabled"]:       # a keyboard: its keys their real size, or no keyboard
            msg.update(px_per_mm=dock_ppmm[0], width=dock_geo[0].width)
        return msg

    def dock_window(on):
        """Docked or floating, at runtime (panelcore.Panel.set_dock): the surface is taken down, set
        up the other way, and put back. Docked, the strip below and the recording rail are put away."""
        msg = None
        hud.hide()
        if on:
            if not apply_dock():
                hud.show_all()
                return None
            keys.hide()
            if rail is not None:
                rail.hide()
            msg = dock_message()
        else:
            apply_float()
            if strip_wanted():
                keys.show_all()
            if rail is not None and panel.shown:
                rail.show_all()
        hud.show_all()
        take_input(hud)
        save_dock(on)
        return msg

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
        if rail is not None and not panel.docked:
            rail.show_all() if want else rail.hide()
        if panel.docked:
            # A hidden dock gives its room back to the windows.
            GtkLayerShell.set_exclusive_zone(hud, dock_h[0] if want else 0)

    def to_front():
        if not surface_there():
            remap("asked to show the HUD, and Hyprland had no surface of it")

    no_drag = []

    def open_url(url):
        try:
            Gtk.show_uri_on_window(None, url, Gdk.CURRENT_TIME)
        except GLib.Error as e:
            print(f"panel: cannot open {url}: {e.message}", file=sys.stderr, flush=True)

    def typing_access():
        # `typing setup` asks this of the macOS panel only; here it is the udev rule, which it sets.
        print("panel: typing: zmk-layer-hud typing setup sets up /dev/uinput", file=sys.stderr, flush=True)

    # Dragging. A layer surface is not the compositor's to move, so the panel moves it: a press
    # anywhere but on the page's controls (it says where they are, kind "nodrag") starts a drag,
    # and the margins follow the pointer until it is let go. The press never reaches the page.
    drag = {}

    def move_to(top, right):
        top = max(0, min(top, geo.height - size[1]))
        right = max(0, min(right, geo.width - size[0]))
        if [top, right] == pos:
            return
        pos[:] = [top, right]
        GtkLayerShell.set_margin(hud, GtkLayerShell.Edge.TOP, top)
        GtkLayerShell.set_margin(hud, GtkLayerShell.Edge.RIGHT, right)
        GtkLayerShell.set_margin(keys, GtkLayerShell.Edge.TOP, top + size[1] + KEYS_GAP)
        GtkLayerShell.set_margin(keys, GtkLayerShell.Edge.RIGHT, right)

    def pressed(_view, event):
        if panel.docked or event.button != 1 or event.type != Gdk.EventType.BUTTON_PRESS:
            return False                # docked, nothing moves it: every press is the page's
        if any(x <= event.x <= x + w and y <= event.y <= y + h for x, y, w, h in no_drag):
            return False                # a control: the page has it
        at = cursor()
        if at is None:
            return False
        drag.update(at=at, pos=list(pos))
        return True

    # A mouse reports motion hundreds of times a second, and each move asks Hyprland where the
    # pointer is and commits the surface: done per event, they queue up and the HUD trails the
    # pointer. So motion only marks the drag, and one move a frame catches up with the pointer.
    def follow():
        at = cursor()
        if drag and at is not None:
            move_to(drag["pos"][0] + at[1] - drag["at"][1], drag["pos"][1] - (at[0] - drag["at"][0]))
        if drag:
            return GLib.SOURCE_CONTINUE
        return GLib.SOURCE_REMOVE

    def moved(_view, event):
        if not drag:
            return False
        if not drag.get("ticking"):
            drag["ticking"] = GLib.timeout_add(16, follow)
        return True

    def released(_view, event):
        if not drag:
            return False
        tick = drag.get("ticking")
        if tick:
            GLib.source_remove(tick)
        follow()                    # to where it was let go
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
    # A long press is a hold for the touch keyboard, not the web view's menu.
    view.connect("context-menu", lambda *_: panel.docked or typing.state == "ok")

    def follow_page(width, height):
        """The HUD's surface as big as the page's layout -- its width is the config's hud.width,
        its height the board's and the stats bar's -- and the strip just below it, as on macOS.
        Docked, only the height: as wide as the screen, as tall as the page within its share."""
        try:
            w, h = int(width or size[0]), int(height or 0)
        except (ValueError, TypeError):
            return
        if panel.docked:            # within dock.BAR_HEIGHT..: minimized, a bar (panelcore checks)
            want = dock_mod.height(h, dock_geo[0].height, pct)
            if want != dock_h[0]:
                size_dock(want)
            return
        if [w, h] == size:
            return
        size[:] = [w, h]
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
        panelcore.Window(show=show_window, front=to_front, dock=dock_window, resize=follow_page,
                         no_drag=lambda rects: no_drag.__setitem__(slice(None), rects), open=open_url,
                         tell=tell_page, typing_access=typing_access, close=lambda: quit_host()),
        RUN, typing, lambda: settings, shown=not HIDDEN,
        log=lambda msg: print(f"panel: {msg}", file=sys.stderr, flush=True))

    take_input(hud)       # kept until each is realized
    take_input(keys)
    if rail is not None and panel.shown:
        rail.show_all()
    if strip_wanted():
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
    # whenever monitors come or go, a surface is closed, or `show` finds Hyprland without the HUD's.
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
        if panel.docked:
            apply_dock()            # docked: on the screen the dock picks now, which may be another
            tell_page(dock_message())
        else:
            if strip_wanted():
                keys.show_all()
            if rail is not None and panel.shown:
                rail.show_all()
        hud.show_all()
        take_input(hud)
        take_input(keys)
        print(f"panel: {why}; the HUD is on {info_now['name']} again", flush=True)
        return False

    def remap_soon(why):
        # Monitors come and go in bursts, and Hyprland places an output a moment after it appears.
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
        # `zmk-layer-hud show` / `hide`, `dock`: what they want is in $STATE.
        panel.wake()
        return GLib.SOURCE_CONTINUE
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, panelstate.SIGNAL, asked)
    # Docked from the start when asked (`start --dock`, dock.start), or when it was left docked.
    if panelcore.starts_docked(load_dock(), settings, os.environ):
        GLib.idle_add(lambda: panel.set_dock(True) or False)
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
        debug = ["--debug"] if os.environ.get("ZMKHUD_DEBUG") == "1" else []
        feed = subprocess.Popen([feed_python, "-u", str(ROOT / "host" / "hudfeed.py"), *debug],
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
        typing.stop()                   # keys up, the virtual keyboard gone
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
