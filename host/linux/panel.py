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
import signal
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

ROOT = Path(__file__).resolve().parent.parent.parent
PAGES = ROOT / "hud"
# Outside the tree, because `zmk-layer-hud update` replaces the tree wholesale. hud.sh exports
# ZMKHUD_STATE; the default is repeated so running this directly still works.
RUN = Path(os.environ.get("ZMKHUD_STATE") or
           Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "zmk-layer-hud")
HUD_W, HUD_H = 598, 392
KEYS_W, KEYS_H = 598, 96
KEYS_GAP = 8
INSET = 8
WINDOWS = []
# Taking room from every window on the output is a recording decision, not a HUD one.
RESERVE = os.environ.get("ZMKHUD_RESERVE") == "1"


def surface(monitor, page, namespace, width, height):
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
    view = WebKit2.WebView.new_with_user_content_manager(manager)
    view.set_background_color(Gdk.RGBA(0, 0, 0, 0))
    view.set_size_request(width, height)
    view.load_uri((PAGES / page).as_uri() +
                  f"?ws=ws://127.0.0.1:{os.environ.get('ZMKHUD_PORT', '8766')}")
    window.add(view)
    WINDOWS.append(window)
    return window


def main():
    if not GtkLayerShell.is_supported():
        sys.exit("A Wayland compositor with layer-shell support is required")
    monitors = json.loads(subprocess.check_output(["hyprctl", "monitors", "-j"]))
    info = next((m for m in monitors if not m["name"].startswith("eDP")), monitors[0])
    display = Gdk.Display.get_default()
    # Match output geometry rather than assuming GDK and Hyprland enumeration agree.
    monitor = next((display.get_monitor(i) for i in range(display.get_n_monitors())
                    if (display.get_monitor(i).get_geometry().x,
                        display.get_monitor(i).get_geometry().y) == (info["x"], info["y"])), None)
    if monitor is None:
        sys.exit(f"Cannot locate recording monitor {info['name']} in GDK")

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

    hud = surface(monitor, "index.html", "zmkhud-layer", HUD_W, HUD_H)
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
        rail_width = 598 + right + INSET
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
    keys = surface(monitor, "keys.html", "zmkhud-keys", KEYS_W, KEYS_H)
    GtkLayerShell.set_anchor(keys, GtkLayerShell.Edge.TOP, True)
    GtkLayerShell.set_anchor(keys, GtkLayerShell.Edge.RIGHT, True)
    GtkLayerShell.set_exclusive_zone(keys, -1)
    GtkLayerShell.set_margin(keys, GtkLayerShell.Edge.TOP, top + HUD_H + KEYS_GAP)
    GtkLayerShell.set_margin(keys, GtkLayerShell.Edge.RIGHT, right)
    if rail is not None:
        rail.show_all()
    keys.show_all()
    hud.show_all()
    room = f"reserved right {rail_width}px, bottom reclaimed" if rail is not None else \
        "overlay, nothing reserved (--reserve tiles windows beside it)"
    print(f"Panel on {info['name']}: {room}; "
          f"HUD inset top={top}, right={right}; keys below HUD", flush=True)

    def quit_host(*_):
        Gtk.main_quit()
        return False

    for sig in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, quit_host)
    RUN.mkdir(exist_ok=True)
    with (RUN / "hudfeed.log").open("w") as output:
        feed_python = os.environ.get("ZMKHUD_PYTHON", sys.executable)
        feed = subprocess.Popen([feed_python, "-u", str(ROOT / "host" / "hudfeed.py"), "--debug"],
                                stdout=output, stderr=output, start_new_session=True)
    def watch_feed():
        if feed.poll() is not None:
            quit_host()
            return False
        return True
    GLib.timeout_add(250, watch_feed)
    try:
        Gtk.main()
    finally:
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
