#!/bin/bash
# zmk-layer-hud Linux host (Hyprland): native transparent layer-shell HUD + typed-keys panel,
# fed by host/hudfeed.py -- the keyboard's own layer signal on its CDC-ACM channel, and what is
# typed from the keyboard's HID reports. No evdev, and no daemon: the keyboard is the only source.
#   bash host/linux/hud.sh        start (validates the config and the keymap YAML first)
#   bash host/linux/hud.sh stop
#   bash host/linux/hud.sh start --reserve   tile windows beside the HUD instead of under it
#
# The HUD is an overlay by default and takes no room from anything. --reserve (ZMKHUD_RESERVE=1)
# gives it an exclusive zone on the right, which moves every window on that output; that is for
# recording, where the editor must never end up behind the board.
# System packages (Arch): python-gobject webkit2gtk-4.1 gtk-layer-shell, which `make install`
# installs. Everything Python (pyserial, hidapi, websockets, keymap-drawer) lives in the repo's
# venv from `make venv`, and this script hands that interpreter to hudfeed; the system python is
# used only for the panel, because the GTK bindings are not in the venv.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
RUN="$ROOT/run"
mkdir -p "$RUN"

# start.sh exports this; honour --reserve here too, so calling this script directly still works.
export ZMKHUD_RESERVE="${ZMKHUD_RESERVE:-0}"
CMD=start
for arg in "$@"; do
  case "$arg" in
    --reserve)   ZMKHUD_RESERVE=1 ;;
    start|stop)  CMD="$arg" ;;
    *) echo "usage: $0 [start|stop] [--reserve]" >&2; exit 1 ;;
  esac
done

stop() {
  # Also retire overlays started by the previous Chromium host.
  pkill -f -- "--class=zmkhud" 2>/dev/null || true
  pkill -f "$HERE/panel.py" 2>/dev/null || true
  pkill -f "$ROOT/host/hudfeed.py" 2>/dev/null || true
  hyprctl eval 'if zmkhud_rules then for _, r in ipairs(zmkhud_rules) do r:set_enabled(false) end end' >/dev/null 2>&1 || true
}

if [ "$CMD" = stop ]; then
  stop
  echo "HUD stopped; panel reservation released"
  exit 0
fi
# The repo's virtualenv (make venv) has pyserial, hidapi, keymap-drawer and websockets; the GTK bindings
# come from the system python, so the panel runs with that one and hands the venv to hudfeed.
python3 -c "import gi; gi.require_version('Gtk', '3.0'); gi.require_version('WebKit2', '4.1'); gi.require_version('GtkLayerShell', '0.1')"
FEED_PYTHON="${ZMKHUD_PYTHON:-$ROOT/.venv/bin/python3}"; [ -x "$FEED_PYTHON" ] || FEED_PYTHON=python3
"$FEED_PYTHON" -c "import serial, hid, websockets"
export ZMKHUD_PYTHON="$FEED_PYTHON"
"$FEED_PYTHON" "$ROOT/host/keymap.py"   # validates the config + keymap-drawer YAML before the panel opens
stop
sleep 0.5
nohup python3 -u "$HERE/panel.py" >"$RUN/panel.log" 2>&1 &
PID=$!
sleep 2
kill -0 "$PID" 2>/dev/null || { echo "HUD failed; see $RUN/panel.log" >&2; exit 1; }
if [ "$ZMKHUD_RESERVE" = 1 ]; then
  echo "HUD started; it reserves the right rail, so windows tile beside it. Stop with: $0 stop"
else
  echo "HUD started as an overlay; nothing is reserved (--reserve tiles windows beside it). Stop with: $0 stop"
fi
