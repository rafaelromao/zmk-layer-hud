#!/usr/bin/env python3
"""zmk-layer-hud Linux host: transparent Wayland (layer-shell) surfaces, layer HUD with the
typed-keys strip below it, plus hudfeed.py for layers, keys and daemon decisions.

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
import panelstate  # noqa: E402  (host/panelstate.py)

ROOT = Path(__file__).resolve().parent.parent.parent
PAGES = ROOT / "hud"
# Outside the tree, because `zmk-layer-hud update` replaces the tree wholesale. hud.sh exports
# ZMKHUD_STATE; the default is repeated so running this directly still works.
RUN = Path(os.environ.get("ZMKHUD_STATE") or
           Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "zmk-layer-hud")
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


def save_position(top, right):
    try:
        try:
            state = json.loads(POSITION.read_text())
        except (OSError, ValueError):
            state = {}
        state = state if isinstance(state, dict) else {}
        state["linux"] = {"top": top, "right": right}
        panelstate._write(str(POSITION), state)     # whole or not at all, and this user's alone
    except OSError as e:
        print(f"panel: could not save {POSITION}: {e}", file=sys.stderr, flush=True)


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
    """Whether Hyprland has the HUD's surface. A panel can outlive its surface unseen, told to show
    a HUD there is nothing left of; True when Hyprland cannot be asked."""
    try:
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
                  on_size=lambda body: page_said(body))
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
    shown = [not HIDDEN]
    wpm = [0]           # the page's live WPM, for the bar icon (host/linux/omarchy)
    stats = [None]      # ...and its stats column, for the icon's menu and its hover panel

    def take_input(window):
        # None is the whole surface again, a layer surface's own.
        window.input_shape_combine_region(None if shown[0] else cairo.Region())

    def set_shown(want):
        """On screen or off it; the pages go on as before either way. The rail goes with the HUD,
        so a hidden one takes no room from the windows either."""
        if want and not surface_there():
            remap("asked to show the HUD, and Hyprland had no surface of it")
        if want != shown[0]:
            shown[0] = want
            for window, css in ((hud, hud_css), (keys, keys_css)):
                if want:
                    css.remove_style_sheet(HIDE_SHEET)
                else:
                    css.add_style_sheet(HIDE_SHEET)
                take_input(window)
                window.queue_draw()
            if rail is not None:
                rail.show_all() if want else rail.hide()
        panelstate.write(RUN, want, wpm=wpm[0], stats=stats[0])

    no_drag = []

    def page_said(body):
        try:
            msg = json.loads(body)
            kind = msg.get("kind")
        except (ValueError, AttributeError):
            return
        if kind == "hide":
            set_shown(False)
        elif kind == "wpm":
            if isinstance(msg.get("wpm"), int) and msg["wpm"] != wpm[0]:
                wpm[0] = msg["wpm"]
                panelstate.write(RUN, shown[0], wpm=wpm[0], stats=stats[0])
        elif kind == "stats":
            said = panelstate.stats_of(msg)
            if said is not None and said != stats[0]:
                stats[0] = said
                panelstate.write(RUN, shown[0], wpm=wpm[0], stats=said)
        elif kind == "nodrag":
            rects = msg.get("rects")
            if isinstance(rects, list):
                no_drag[:] = [r for r in rects if isinstance(r, list) and len(r) == 4
                              and all(isinstance(v, (int, float)) for v in r)]
        else:
            follow_page(body)

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
        if event.button != 1 or event.type != Gdk.EventType.BUTTON_PRESS:
            return False
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

    def follow_page(body):
        """The HUD's surface as big as the page's layout -- its width is the config's hud.width,
        its height the board's and the stats bar's -- and the strip just below it, as on macOS."""
        try:
            msg = json.loads(body)
            w, h = int(msg.get("width") or size[0]), int(msg.get("height") or 0)
        except (ValueError, TypeError, AttributeError):
            return
        if msg.get("kind") != "size" or not (100 <= w <= 4000 and 50 <= h <= 4000) or [w, h] == size:
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
        if not shown[0]:
            take_input(hud)
            take_input(keys)

    take_input(hud)       # kept until each is realized
    take_input(keys)
    if rail is not None and shown[0]:
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
        keys.show_all()
        hud.show_all()
        if rail is not None and shown[0]:
            rail.show_all()
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
        # `zmk-layer-hud show` / `hide`: what they want is in panel.want.
        want = panelstate.wanted(RUN)
        if want is not None:
            set_shown(want)
        return GLib.SOURCE_CONTINUE
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, panelstate.SIGNAL, asked)
    # Only now, with the handler in: the CLI signals whatever pid this file names.
    panelstate.write(RUN, shown[0])
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
        panelstate.remove(RUN)
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
