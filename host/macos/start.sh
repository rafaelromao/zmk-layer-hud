#!/bin/bash
# Start / stop the layer HUD on macOS.
#   host/macos/start.sh          start (checks the config and the keymap-drawer YAML first)
#   host/macos/start.sh run      the same, but this process becomes the panel (a login item's way)
#   host/macos/start.sh stop
#   host/macos/start.sh log      tail the panel and feed logs
# ZMKHUD_HIDDEN=1 starts it off screen, counting (`zmk-layer-hud start --hidden`).
# Needs the repo's virtualenv: brew install hidapi && make venv
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
# Logs live outside the tree: `zmk-layer-hud update` replaces the tree wholesale, and that cannot
# be the directory the logs are in. The CLI exports ZMKHUD_STATE; the default is repeated here so
# running this script directly still works -- and exported, because the panel says in there
# whether it is shown (panel.json), where `zmk-layer-hud show` looks.
RUN="${ZMKHUD_STATE:-${XDG_STATE_HOME:-$HOME/.local/state}/zmk-layer-hud}"
mkdir -p "$RUN"
export ZMKHUD_STATE="$RUN"
export ZMKHUD_HIDDEN="${ZMKHUD_HIDDEN:-0}"

if [ -n "${ZMKHUD_PYTHON:-}" ]; then PYTHON="$ZMKHUD_PYTHON"
elif [ -x "$ROOT/.venv/bin/python3" ]; then PYTHON="$ROOT/.venv/bin/python3"
else echo "no .venv: run 'brew install hidapi && make venv' in $ROOT (or set ZMKHUD_PYTHON)" >&2; exit 1; fi

# A panel told to stop writes what its session counted before it goes; the next one waits for
# that (up to 5 s), or it would start beside the old one and find the socket's port taken.
stop() {
  pkill -f "$HERE/panel.py" 2>/dev/null || true
  pkill -f "$ROOT/host/hudfeed.py" 2>/dev/null || true
  for _ in $(seq 50); do pgrep -f "$HERE/panel.py" >/dev/null || return 0; sleep 0.1; done
}

# The menubar icon is a process of its own (menubar.py), so it stays when the HUD quits: started
# here when it is not running yet, unless `zmk-layer-hud menubar disable` said not to.
menubar() {
  [ -e "$HOME/.config/zmk-layer-hud/menubar-off" ] && return 0
  pgrep -f "$HERE/menubar.py" >/dev/null && return 0
  nohup "$PYTHON" -u "$HERE/menubar.py" >"$RUN/menubar.log" 2>&1 &
}

case "${1:-start}" in
  start|run)
    "$PYTHON" -c 'import serial, hid, websockets, objc, WebKit' 2>/dev/null || {
      echo "the venv lacks pyserial/hidapi/websockets/pyobjc: run 'make venv' again" >&2; exit 1; }
    # Fails early with a readable reason if the config or its definitions are off -- or missing,
    # when nothing has been imported yet (it names `zmk-layer-hud import`).
    "$PYTHON" "$ROOT/host/keymap.py" ${ZMKHUD_CONFIG:+--config "$ZMKHUD_CONFIG"}
    stop
    menubar
    # A login item has to stay attached: launchd ends whatever a job leaves behind when it exits.
    [ "${1:-start}" = run ] && exec "$PYTHON" -u "$HERE/panel.py" >"$RUN/panel.log" 2>&1
    nohup "$PYTHON" -u "$HERE/panel.py" >"$RUN/panel.log" 2>&1 &
    PID=$!
    sleep 2
    kill -0 "$PID" 2>/dev/null || { echo "panel failed; see $RUN/panel.log" >&2; cat "$RUN/panel.log" >&2; exit 1; }
    # The feed runs inside the panel here, so its lines are in panel.log too.
    if [ "$ZMKHUD_HIDDEN" = 1 ]; then
      echo "HUD started hidden: it is counting, and \`zmk-layer-hud show\` or its menubar icon brings it up (log: $RUN/panel.log)"
    else
      echo "HUD started on the recording display (log: $RUN/panel.log). Stop with: $0 stop"
    fi
    ;;
  stop) stop; echo "HUD stopped" ;;
  log) tail -n 40 -f "$RUN/panel.log" ;;
  *) echo "usage: $0 [start|run|stop|log]" >&2; exit 2 ;;
esac
