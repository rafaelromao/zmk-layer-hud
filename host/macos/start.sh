#!/bin/bash
# Start / stop the layer HUD in the running Hammerspoon.
#   host/macos/start.sh        start (rebuilds hud/keymap.json first)
#   host/macos/start.sh stop
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
HS=/opt/homebrew/bin/hs
# The repo's virtualenv (make venv) has hidapi and keymap-drawer; ZMKHUD_PYTHON overrides.
if [ -n "${ZMKHUD_PYTHON:-}" ]; then PYTHON="$ZMKHUD_PYTHON"
elif [ -x "$ROOT/.venv/bin/python3" ]; then PYTHON="$ROOT/.venv/bin/python3"
else PYTHON="$(command -v python3)"; fi

if ! command -v "$HS" >/dev/null; then
  echo "hs CLI not found; Hammerspoon installs it at $HS" >&2; exit 1
fi
if ! "$HS" -c 'return 1' >/dev/null 2>&1; then
  cat >&2 <<MSG
Hammerspoon's IPC port is not loaded. Add this line to ~/.hammerspoon/init.lua and reload Hammerspoon:

    require("hs.ipc")

MSG
  exit 1
fi

case "${1:-start}" in
  start)
    # Fails early with a readable reason if the config or the keymap-drawer YAML is off.
    "$PYTHON" "$ROOT/host/keymap.py" ${ZMKHUD_CONFIG:+--config "$ZMKHUD_CONFIG"}
    if ! "$PYTHON" -c 'import hid' 2>/dev/null; then
      cat >&2 <<MSG
warning: $PYTHON has no 'hid' module, so the keyboard's layer signal cannot be read and the HUD
will fall back to inference. Create the repo's virtualenv with:

    brew install hidapi && make -C "$ROOT" venv

MSG
    fi
    # Errors from hud.lua are printed here, not swallowed.
    ZMKHUD_PYTHON="$PYTHON" "$HS" -c "if zmkhud then zmkhud.stop() end; zmkhud = dofile('$HERE/hud.lua'); return zmkhud.selftest()"
    echo "HUD started on the recording display. Stop with: $0 stop"
    ;;
  stop) "$HS" -c "if zmkhud then zmkhud.stop() end" ;;
  *) echo "usage: $0 [start|stop]" >&2; exit 2 ;;
esac
