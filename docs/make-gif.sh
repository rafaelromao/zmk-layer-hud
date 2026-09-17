#!/bin/bash
# Render an animation of the HUD stepping through a scripted demo.
#
#   docs/make-gif.sh [--config FILE] [--script FILE] [--out FILE] [--size WxH] [--framerate FPS]
#
# Defaults render the 3x5 sample to docs/hud.gif. Each frame is index.html?keymap=…&demo=N
# (see demoFrame in hud/hud.js), screenshotted by a headless Chromium-family browser, then
# assembled with ffmpeg. Needs: the repo's venv (make venv), ffmpeg, and Brave / Chrome /
# Chromium. It cannot run inside a sandbox that denies unix sockets or TCP binds — Chromium
# aborts with "Failed to create socket directory" and the http.server cannot listen.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PYTHON="${ZMKHUD_PYTHON:-$ROOT/.venv/bin/python3}"; [ -x "$PYTHON" ] || PYTHON=python3
PORT="${ZMKHUD_GIF_PORT:-8791}"

CONFIG="$ROOT/config/example-3x5.yaml"
SCRIPT="$HERE/demo-3x5.json"
OUT="$HERE/hud.gif"
SIZE="640x430"
FRAMERATE="0.9"
TIMEOUT_S="${ZMKHUD_FRAME_TIMEOUT:-25}"        # per frame, waiting for the browser to write it
TIMEOUT_TICKS=$((TIMEOUT_S * 5))
while [ $# -gt 0 ]; do
  case "$1" in
    --config)    CONFIG="$2"; shift 2 ;;
    --script)    SCRIPT="$2"; shift 2 ;;
    --out)       OUT="$2"; shift 2 ;;
    --size)      SIZE="$2"; shift 2 ;;
    --framerate) FRAMERATE="$2"; shift 2 ;;
    -h|--help)   sed -n '2,8p' "$0"; exit 0 ;;
    *)           echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
W="${SIZE%x*}"; H="${SIZE#*x}"

# $ZMKHUD_BROWSER overrides the search. Brave is last on purpose: its --headless=new starts and
# then hangs without ever writing --screenshot (tested on macOS 15, Brave 152), which is what
# made this script look broken. Edge is a plain Chromium and does the job.
for c in "${ZMKHUD_BROWSER:-}" \
         "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
         "/Applications/Chromium.app/Contents/MacOS/Chromium" \
         "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" \
         "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser" \
         chromium google-chrome msedge brave; do
  [ -n "$c" ] || continue
  if [ -x "$c" ] || command -v "$c" >/dev/null 2>&1; then BROWSER="$c"; break; fi
done
[ -n "${BROWSER:-}" ] || { echo "no Chromium-family browser found" >&2; exit 1; }
command -v ffmpeg >/dev/null || { echo "ffmpeg is required (brew install ffmpeg)" >&2; exit 1; }
[ -f "$SCRIPT" ] || { echo "no such demo script: $SCRIPT" >&2; exit 1; }

# The page fetches its script same-origin, so it is served beside the pages (gitignored).
cp "$SCRIPT" "$ROOT/hud/demo.json"
FRAMES="$("$PYTHON" -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["steps"]))' "$SCRIPT")"
[ "$FRAMES" -gt 0 ] || { echo "$SCRIPT has no steps" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'kill $SERVER 2>/dev/null || true; rm -rf "$WORK"' EXIT

"$PYTHON" "$ROOT/host/keymap.py" --config "$CONFIG" --dump > "$ROOT/hud/keymap.json"
# No subshell: $! must be the server itself, or the trap kills the wrapper and leaves the
# server holding the port (which then silently breaks every later run).
python3 -m http.server -d "$ROOT/hud" "$PORT" >/dev/null 2>&1 & SERVER=$!
sleep 1
kill -0 "$SERVER" 2>/dev/null || { echo "could not serve $ROOT/hud on port $PORT (already in use?)" >&2; exit 1; }

# The panel is translucent (hud.opacity), so it is composited on the dark it is drawn for
# rather than on the browser's white. The virtual-time budget stays under combo_pill_ms
# (1000 ms) and the strip's fade (1.8 s), or those frames screenshot empty.
# Headless --screenshot is not reliably a one-shot command: Edge writes the PNG and then keeps
# running, Brave exits without ever writing one. So wait for the file rather than for the
# process, and take the browser down ourselves.
for i in $(seq 0 $((FRAMES - 1))); do
  FRAME="$WORK/frame-$(printf %02d "$i").png"
  "$BROWSER" --headless=new --disable-gpu --no-first-run --user-data-dir="$WORK/profile-$i" \
    --window-size="$W,$H" --hide-scrollbars --virtual-time-budget=800 \
    --default-background-color=1A1B26FF --screenshot="$FRAME" \
    "http://localhost:$PORT/index.html?keymap=keymap.json&demo=$i" >/dev/null 2>&1 &
  BPID=$!
  for _ in $(seq 1 "$TIMEOUT_TICKS"); do [ -s "$FRAME" ] && break; sleep 0.2; done
  sleep 0.3                                   # let the PNG finish landing
  kill "$BPID" 2>/dev/null || true
  wait "$BPID" 2>/dev/null || true
  [ -s "$FRAME" ] || { echo "$BROWSER wrote no frame in ${TIMEOUT_S}s — set ZMKHUD_BROWSER to another Chromium" >&2; exit 1; }
  echo "frame $i"
done

# A palette for crisp colours, loop forever.
ffmpeg -y -loglevel error -framerate "$FRAMERATE" -pattern_type glob -i "$WORK/frame-*.png" \
  -vf "split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=none" -loop 0 "$OUT"
echo "wrote $OUT ($(du -h "$OUT" | cut -f1), $FRAMES frames at ${FRAMERATE}fps)"
