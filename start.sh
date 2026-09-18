#!/bin/bash
# Start, stop or watch the HUD, on whichever of the two hosts this is.
#
#   ./start.sh          start
#   ./start.sh stop
#   ./start.sh log      follow the panel and feed logs
#   ./start.sh status   is it running, and what is it reading
#
# The work is in host/macos/start.sh and host/linux/hud.sh; this picks one and gives them the
# same three verbs, because remembering which host spells it `hud.sh` is not worth anyone's time.
# `make install` first, once per machine.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RUN="$HERE/run"
CMD="${1:-start}"

case "$(uname -s)" in
  Darwin) HOST_SCRIPT="$HERE/host/macos/start.sh" ;;
  Linux)  HOST_SCRIPT="$HERE/host/linux/hud.sh" ;;
  *)      echo "start.sh: no HUD host for $(uname -s); macOS and Linux (Hyprland) only" >&2; exit 1 ;;
esac

# Only the macOS script has `log`, and neither has `status`, so those are answered here rather
# than added to both.
case "$CMD" in
  log)
    [ -d "$RUN" ] || { echo "start.sh: nothing has run yet (no $RUN)" >&2; exit 1; }
    exec tail -n 40 -F "$RUN/panel.log" "$RUN/hudfeed.log"
    ;;
  status)
    if pgrep -f "$HERE/host/.*/panel.py" >/dev/null 2>&1; then echo "panel: running"; else echo "panel: stopped"; fi
    if pgrep -f "$HERE/host/hudfeed.py" >/dev/null 2>&1; then echo "feed:  running"; else echo "feed:  stopped (the macOS panel runs it in-process)"; fi
    # What it last managed to open says more than whether it is alive: the layer signal and the
    # typed-keys strip are separate grants and either can be the one that is missing.
    if [ -f "$RUN/hudfeed.log" ]; then
      echo "--- last from hudfeed.log ---"
      grep -E "reading|cannot open|cannot read|is not the layer signal" "$RUN/hudfeed.log" | tail -5 || true
    fi
    ;;
  start|stop)
    exec bash "$HOST_SCRIPT" "$CMD"
    ;;
  *)
    echo "usage: $0 [start|stop|log|status]" >&2
    exit 1
    ;;
esac
