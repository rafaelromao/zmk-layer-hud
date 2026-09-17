#!/bin/bash
# zmk-layer-hud Linux host (Hyprland): native transparent layer-shell HUD + typed-keys panel,
# fed by host/hudfeed.py (raw HID layer signal, evdev keys, daemon decisions).
#   bash host/linux/hud.sh        start (rebuilds hud/keymap.json first)
#   bash host/linux/hud.sh stop
# Arch dependencies: python-gobject webkit2gtk-4.1 gtk-layer-shell python-hidapi python-evdev python-websockets
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
RUN="$ROOT/run"
mkdir -p "$RUN"

stop() {
  # Also retire overlays started by the previous Chromium host.
  pkill -f -- "--class=zmkhud" 2>/dev/null || true
  pkill -f "$HERE/panel.py" 2>/dev/null || true
  pkill -f "$ROOT/host/hudfeed.py" 2>/dev/null || true
  hyprctl eval 'if zmkhud_rules then for _, r in ipairs(zmkhud_rules) do r:set_enabled(false) end end' >/dev/null 2>&1 || true
}

if [ "${1:-start}" = stop ]; then
  stop
  echo "HUD stopped; panel reservation released"
  exit 0
fi
# The repo's virtualenv (make venv) has hidapi, keymap-drawer and websockets; the GTK bindings
# come from the system python, so the panel runs with that one and hands the venv to hudfeed.
python3 -c "import gi; gi.require_version('Gtk', '3.0'); gi.require_version('WebKit2', '4.1'); gi.require_version('GtkLayerShell', '0.1')"
FEED_PYTHON="${ZMKHUD_PYTHON:-$ROOT/.venv/bin/python3}"; [ -x "$FEED_PYTHON" ] || FEED_PYTHON=python3
"$FEED_PYTHON" -c "import hid, websockets"
export ZMKHUD_PYTHON="$FEED_PYTHON"
"$FEED_PYTHON" "$ROOT/host/keymap.py"   # validates the config + keymap-drawer YAML before the panel opens
stop
sleep 0.5
nohup python3 -u "$HERE/panel.py" >"$RUN/panel.log" 2>&1 &
PID=$!
sleep 2
kill -0 "$PID" 2>/dev/null || { echo "HUD failed; see $RUN/panel.log" >&2; exit 1; }
echo "HUD started; HUD reserves the right rail, typed keys sit below it. Stop with: $0 stop"
