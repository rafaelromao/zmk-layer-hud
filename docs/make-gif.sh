#!/bin/bash
# Render the README animation: the HUD stepping through a scripted demo on the 3x5 sample.
#
#   docs/make-gif.sh [config] [out.gif]        default: config/example-3x5.yaml docs/hud.gif
#
# Each frame is index.html?keymap=…&demo=N (see DEMO in hud/hud.js), screenshotted by a headless
# Chromium-family browser, then assembled with ffmpeg. Needs: the repo's venv (make venv),
# ffmpeg, and Brave / Chrome / Chromium.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
CONFIG="${1:-$ROOT/config/example-3x5.yaml}"
OUT="${2:-$HERE/hud.gif}"
PYTHON="${ZMKHUD_PYTHON:-$ROOT/.venv/bin/python3}"; [ -x "$PYTHON" ] || PYTHON=python3
PORT=8791
FRAMES=9            # steps in DEMO
W=640; H=430

for c in "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser" \
         "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
         "/Applications/Chromium.app/Contents/MacOS/Chromium" chromium google-chrome brave; do
  if [ -x "$c" ] || command -v "$c" >/dev/null 2>&1; then BROWSER="$c"; break; fi
done
[ -n "${BROWSER:-}" ] || { echo "no Chromium-family browser found" >&2; exit 1; }
command -v ffmpeg >/dev/null || { echo "ffmpeg is required (brew install ffmpeg)" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'kill $SERVER 2>/dev/null || true; rm -rf "$WORK"' EXIT

"$PYTHON" "$ROOT/host/keymap.py" --config "$CONFIG" --dump > "$ROOT/hud/keymap.json"
(cd "$ROOT/hud" && python3 -m http.server "$PORT" >/dev/null 2>&1) & SERVER=$!
sleep 1

for i in $(seq 0 $((FRAMES - 1))); do
  "$BROWSER" --headless=new --disable-gpu --no-first-run --user-data-dir="$WORK/profile" \
    --window-size="$W,$H" --hide-scrollbars --virtual-time-budget=1500 \
    --screenshot="$WORK/frame-$(printf %02d "$i").png" \
    "http://localhost:$PORT/index.html?keymap=keymap.json&demo=$i" >/dev/null 2>&1
  echo "frame $i"
done

# ~1.1 s per frame, a palette for crisp colours, loop forever.
ffmpeg -y -loglevel error -framerate 0.9 -pattern_type glob -i "$WORK/frame-*.png" \
  -vf "split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=none" -loop 0 "$OUT"
echo "wrote $OUT ($(du -h "$OUT" | cut -f1))"
