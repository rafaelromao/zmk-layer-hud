#!/bin/bash
# Render an animation of the HUD playing a demo script (docs/demo-scripts.md).
#
#   docs/make-gif.sh [--config FILE] [--script FILE] [--out FILE] [--size WxH] [--framerate FPS]
#   docs/make-gif.sh --live [--fps N] ...     moments, not stills: the glow fading, the speed moving
#
# Defaults render the 3x5 sample to docs/hud.gif. A frame is the page screenshotted by a headless
# Chromium-family browser, and ffmpeg assembles them. By default a frame is a still, one per step
# (index.html?keymap=…&demo=N, demoFrame in hud/hud.js); with --live, a frame is the script played
# up to a moment, every 1/fps of a second (…&timeline=…&at=T, replayTo in hud/hud.js). Needs: the
# repo's venv (make venv), ffmpeg, and Chrome / Chromium / Edge / Brave. It cannot run inside a
# sandbox that denies unix sockets or TCP binds: Chromium aborts with "Failed to create socket
# directory" and the http.server cannot listen.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PYTHON="${ZMKHUD_PYTHON:-$ROOT/.venv/bin/python3}"; [ -x "$PYTHON" ] || PYTHON=python3
PORT="${ZMKHUD_GIF_PORT:-8791}"

CONFIG="$ROOT/config/example-3x5.yaml"
SCRIPT="$HERE/demo-3x5.json"
OUT="$HERE/hud.gif"
SIZE=""
FRAMERATE="0.9"
LIVE=0
FPS=8
TIMEOUT_S="${ZMKHUD_FRAME_TIMEOUT:-25}"        # per frame, waiting for the browser to write it
TIMEOUT_TICKS=$((TIMEOUT_S * 5))
while [ $# -gt 0 ]; do
  case "$1" in
    --config)    CONFIG="$2"; shift 2 ;;
    --script)    SCRIPT="$2"; shift 2 ;;
    --out)       OUT="$2"; shift 2 ;;
    --size)      SIZE="$2"; shift 2 ;;
    --framerate) FRAMERATE="$2"; shift 2 ;;
    --live)      LIVE=1; shift ;;
    --fps)       FPS="$2"; shift 2 ;;
    -h|--help)   sed -n '2,13p' "$0"; exit 0 ;;
    *)           echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
# A live frame shows the stats bar above the panel, which a still leaves out.
[ -n "$SIZE" ] || { [ "$LIVE" = 1 ] && SIZE="640x480" || SIZE="640x430"; }
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

"$PYTHON" "$ROOT/host/keymap.py" --config "$CONFIG" --dump > "$ROOT/hud/keymap.json"
# The page fetches what it draws same-origin, so it is served beside the pages (gitignored).
if [ "$LIVE" = 1 ]; then
  # The script played: what a keyboard would send and when (host/play.py --capture). A frame is
  # the page as that leaves it at a moment, every 1/fps of a second from the start to the end.
  "$PYTHON" "$ROOT/host/play.py" "$SCRIPT" --keymap "$ROOT/hud/keymap.json" --capture --strict > "$ROOT/hud/timeline.json"
  DURATION="$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["duration_ms"])' "$ROOT/hud/timeline.json")"
  FRAMES=$((DURATION * FPS / 1000 + 1))
  FRAMERATE="$FPS"
  frame_url() { echo "index.html?keymap=keymap.json&timeline=timeline.json&at=$(($1 * 1000 / FPS))"; }
else
  # The script's stills (host/play.py --stills): text to type becomes a frame per keystroke, a
  # pause none, and a script of frames alone is itself.
  "$PYTHON" "$ROOT/host/play.py" "$SCRIPT" --keymap "$ROOT/hud/keymap.json" --stills --strict > "$ROOT/hud/demo.json"
  FRAMES="$("$PYTHON" -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["steps"]))' "$ROOT/hud/demo.json")"
  frame_url() { echo "index.html?keymap=keymap.json&demo=$1"; }
fi
[ "$FRAMES" -gt 0 ] || { echo "$SCRIPT has no frames" >&2; exit 1; }

# Where the system puts temporary files: macOS's mktemp ignores $TMPDIR unless given a template.
WORK="$(mktemp -d "${TMPDIR:-/tmp}/zmk-layer-hud-gif.XXXXXX")"
trap 'kill $SERVER 2>/dev/null || true; rm -rf "$WORK"' EXIT
# No subshell: $! must be the server itself, or the trap kills the wrapper and leaves the
# server holding the port (which then silently breaks every later run).
python3 -m http.server -d "$ROOT/hud" "$PORT" >/dev/null 2>&1 & SERVER=$!
sleep 1
kill -0 "$SERVER" 2>/dev/null || { echo "could not serve $ROOT/hud on port $PORT (already in use?)" >&2; exit 1; }

# The panel is translucent (hud.opacity), so it is composited on the dark it is drawn for
# rather than on the browser's white. The virtual-time budget stays under combo_pill_ms
# (1000 ms) and the strip's fade (1.8 s), or those stills screenshot empty; a live frame keeps
# its own clock and is done drawing as soon as the page has loaded.
# Headless --screenshot is not reliably a one-shot command: Edge writes the PNG and then keeps
# running, Brave exits without ever writing one. So wait for the file rather than for the
# process, and take the browser down ourselves.
for i in $(seq 0 $((FRAMES - 1))); do
  FRAME="$WORK/frame-$(printf %04d "$i").png"
  "$BROWSER" --headless=new --disable-gpu --no-first-run --user-data-dir="$WORK/profile-$i" \
    --window-size="$W,$H" --hide-scrollbars --virtual-time-budget=800 \
    --default-background-color=1A1B26FF --screenshot="$FRAME" \
    "http://localhost:$PORT/$(frame_url "$i")" >/dev/null 2>&1 &
  BPID=$!
  for _ in $(seq 1 "$TIMEOUT_TICKS"); do [ -s "$FRAME" ] && break; sleep 0.2; done
  sleep 0.3                                   # let the PNG finish landing
  kill "$BPID" 2>/dev/null || true
  wait "$BPID" 2>/dev/null || true
  rm -rf "$WORK/profile-$i"
  [ -s "$FRAME" ] || { echo "$BROWSER wrote no frame in ${TIMEOUT_S}s — set ZMKHUD_BROWSER to another Chromium" >&2; exit 1; }
  echo "frame $((i + 1)) of $FRAMES"
done

# A palette for crisp colours, loop forever.
ffmpeg -y -loglevel error -framerate "$FRAMERATE" -pattern_type glob -i "$WORK/frame-*.png" \
  -vf "split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=none" -loop 0 "$OUT"
echo "wrote $OUT ($(du -h "$OUT" | cut -f1), $FRAMES frames at ${FRAMERATE}fps)"
