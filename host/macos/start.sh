#!/bin/bash
# Start / stop the layer HUD on macOS.
#   host/macos/start.sh          start (checks the config and the keymap-drawer YAML first)
#   host/macos/start.sh stop
#   host/macos/start.sh log      tail the panel and feed logs
# Needs the repo's virtualenv: make venv
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
RUN="$ROOT/run"
mkdir -p "$RUN"

if [ -n "${ZMKHUD_PYTHON:-}" ]; then PYTHON="$ZMKHUD_PYTHON"
elif [ -x "$ROOT/.venv/bin/python3" ]; then PYTHON="$ROOT/.venv/bin/python3"
else echo "no .venv: run 'make venv' in $ROOT (or set ZMKHUD_PYTHON)" >&2; exit 1; fi

stop() { pkill -f "$HERE/panel.py" 2>/dev/null || true; pkill -f "$ROOT/host/hudfeed.py" 2>/dev/null || true; }

case "${1:-start}" in
  start)
    "$PYTHON" -c 'import serial, websockets, objc, WebKit' 2>/dev/null || {
      echo "the venv lacks pyserial/websockets/pyobjc: run 'make venv' again" >&2; exit 1; }
    # Fails early with a readable reason if the config or the keymap-drawer YAML is off.
    "$PYTHON" "$ROOT/host/keymap.py" ${ZMKHUD_CONFIG:+--config "$ZMKHUD_CONFIG"}
    stop
    nohup "$PYTHON" -u "$HERE/panel.py" >"$RUN/panel.log" 2>&1 &
    PID=$!
    sleep 2
    kill -0 "$PID" 2>/dev/null || { echo "panel failed; see $RUN/panel.log" >&2; cat "$RUN/panel.log" >&2; exit 1; }
    echo "HUD started on the recording display (logs: $RUN/panel.log, $RUN/hudfeed.log). Stop with: $0 stop"
    ;;
  stop) stop; echo "HUD stopped" ;;
  log) tail -n 40 -f "$RUN/panel.log" "$RUN/hudfeed.log" ;;
  *) echo "usage: $0 [start|stop|log]" >&2; exit 2 ;;
esac
