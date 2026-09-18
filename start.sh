#!/bin/bash
# Start, stop or watch the HUD, on whichever of the two hosts this is.
#
#   ./start.sh          start
#   ./start.sh stop
#   ./start.sh log      follow the panel and feed logs
#   ./start.sh status   is it running, and what is it reading
#   ./start.sh --reserve   (Linux) tile windows beside the HUD instead of under it
#
# --reserve is for recording: it gives the HUD an exclusive zone, so the compositor moves every
# window on that output out of its way. Without it the HUD is an overlay and takes no room.
#
# The work is in host/macos/start.sh and host/linux/hud.sh; this picks one and gives them the
# same three verbs, because remembering which host spells it `hud.sh` is not worth anyone's time.
# `make install` first, once per machine.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RUN="$HERE/run"
CMD=start
export ZMKHUD_RESERVE=0
for arg in "$@"; do
  case "$arg" in
    --reserve)             ZMKHUD_RESERVE=1 ;;
    start|stop|log|status) CMD="$arg" ;;
    *) echo "usage: $0 [start|stop|log|status] [--reserve]" >&2; exit 1 ;;
  esac
done

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
    # Both logs, because where the feed writes depends on the host: the Linux panel starts it as a
    # subprocess with its own hudfeed.log, the macOS one runs it in-process and it lands in
    # panel.log with everything else.
    feed_log() { cat "$RUN/hudfeed.log" "$RUN/panel.log" 2>/dev/null; }
    # The last line matching, or empty. Emptiness is the answer rather than the exit status: a
    # `grep | tail` pipeline reports tail's success whether or not grep matched anything, unless
    # pipefail happens to be on, and a verdict is too easy to get backwards to rest on that.
    feed_last() { feed_log | grep "$1" | tail -1 || true; }
    if [ -f "$RUN/hudfeed.log" ] || [ -f "$RUN/panel.log" ]; then
      # One line for the HID half, because both of the things it carries -- the typed-keys strip
      # and the shift flag the board draws its capitals from -- come from there and from nowhere
      # else, and neither of them looks like a permission when it goes. The state that matters
      # most is an absence, a scan that found no keyboard, so this asks after each state in turn
      # instead of printing whichever lines happen to be there.
      typed=$(feed_last "reading what is typed on")
      refused=$(feed_last "cannot read what is typed on")
      absent=$(feed_last "no HID keyboard")
      if [ -n "$typed" ]; then
        echo "keys:  reading ${typed##*reading what is typed on }"
      elif [ -n "$refused" ]; then
        echo "keys:  refused -- ${refused##*cannot read what is typed on }"
      elif [ -n "$absent" ]; then
        echo "keys:  ${absent##*hudfeed: }"
      else
        echo "keys:  nothing said yet (--no-hid-keys, or the feed has not scanned)"
      fi
      echo "--- last from the feed ---"
      feed_log | grep -E "reading|cannot open|cannot read|is not the layer signal|no HID keyboard" | tail -5 || true
    fi
    # The Linux panel draws three layer-shell surfaces, and an empty typed-keys strip is entirely
    # transparent: "mapped with nothing on it" and "never mapped" look the same on screen. Only
    # the compositor can tell them apart, so ask it.
    if [ "$(uname -s)" = Linux ] && command -v hyprctl >/dev/null 2>&1; then
      echo "--- layer-shell surfaces ---"
      hyprctl layers -j | python3 -c '
import json, sys
want = ("zmkhud-layer", "zmkhud-reserved", "zmkhud-keys")
found = {}
for monitor in json.load(sys.stdin).values():
    for entries in (monitor.get("levels") or {}).values():
        for e in entries:
            if e.get("namespace") in want:
                found[e["namespace"]] = e
for ns in want:
    e = found.get(ns)
    if e:
        print("  %-16s %d,%d %dx%d" % (ns, e["x"], e["y"], e["w"], e["h"]))
    elif ns == "zmkhud-reserved":
        # Absent is the normal state: the HUD only takes room from other windows when asked to.
        print("  %-16s overlay, nothing reserved (start with --reserve to tile beside it)" % ns)
    else:
        print("  %-16s missing" % ns)
' || true
    fi
    ;;
  start|stop)
    # ZMKHUD_RESERVE is exported above, so the host script and the panel both see it.
    exec bash "$HOST_SCRIPT" "$CMD"
    ;;
esac
