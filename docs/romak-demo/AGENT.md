# Recording the Romak 34 demo on Omarchy

A runbook for an agent on an Omarchy (Hyprland) machine. It records a silent screen video of the
HUD drawing Romak 34 while the text in `text.txt` is typed at 70 wpm into a terminal beside it.
Nothing here is typed on the real keyboard: `drive.py` sends the HUD what the keyboard would send
(`host/play.py` compiles `docs/demo-romak.json` on `config/romak-34.yaml`) and types each
character into the focused window with `wtype`, on one clock.

Ask the user before installing anything, and before any step that changes their setup beyond
what step 7 puts back.

## 0. What is in this directory

| file | what |
|---|---|
| `text.txt` | the text, exactly as it must come out |
| `drive.py` | plays the script into the HUD and types it with wtype |
| `../demo-romak.json` | the script: one `type` step per line of `text.txt`, `"wpm": 70`, `"repeat": true` |
| `../../config/romak-34.yaml` | the HUD config; the layout is `../../examples/romak-34.yaml` |

## 1. Prepare

```bash
cd ~/projects/zmk-layer-hud
git pull --ff-only
OUT=~/Videos/romak-demo; mkdir -p "$OUT"
PY=$( [ -x .venv/bin/python3 ] && echo .venv/bin/python3 || echo python3 )
```

Check the tools; report any that are missing and ask before `yay -S`/`pacman -S` them:

```bash
for t in wtype ffprobe hyprctl jq makoctl; do command -v $t >/dev/null || echo "missing: $t"; done
command -v wf-recorder || command -v gpu-screen-recorder || echo "missing: a screen recorder"
$PY -c 'import websockets' || echo "missing: python-websockets (make venv)"
```

Check the script types the whole text, before recording anything:

```bash
bin/zmk-layer-hud keymap --config config/romak-34.yaml --dump > "$OUT/keymap.json"
$PY host/play.py docs/demo-romak.json --keymap "$OUT/keymap.json" --check --strict
$PY docs/romak-demo/drive.py docs/demo-romak.json --keymap "$OUT/keymap.json" --dry-run > "$OUT/dry.txt"
diff <(printf '%s' "$(cat docs/romak-demo/text.txt)") "$OUT/dry.txt" && echo dry run matches
```

Both must pass (about 1577 keystrokes over about 284 s). If they do not, stop and report.

## 2. Start the HUD on Romak 34

Note what the HUD runs now so step 7 can put it back: `bin/zmk-layer-hud status`.

```bash
bin/zmk-layer-hud stop
ZMKHUD_CONFIG=$PWD/config/romak-34.yaml bin/zmk-layer-hud start --reserve
bin/zmk-layer-hud show
```

`--reserve` gives the HUD the right side of the screen and windows tile beside it instead of
under it. Wait until the panel shows "Romak 34" in its corner.

## 3. Open the editor

Open the default terminal (`xdg-terminal-exec`, Omarchy's) with a large font, running a plain
`cat` so nothing auto-pairs or auto-indents (an editor like nvim would):

```bash
xdg-terminal-exec --title=romak-demo -e sh -c "clear; exec cat > '$OUT/typed.txt'" &
```

If the terminal takes a font size flag (`ghostty --font-size=18`, `alacritty -o font.size=18`),
use it directly instead so the text reads well in the video. Make sure the terminal is the only
window on the workspace besides the HUD, and that it has focus: `hyprctl activewindow`.

## 4. Quiet the screen

```bash
makoctl mode -a do-not-disturb
hyprctl keyword cursor:inactive_timeout 1
```

Do not touch the keyboard or the mouse from here to step 6: the HUD also draws the real keyboard,
and a stray key or click lands in the recording or in the typed text.

## 5. Record

```bash
MON=$(hyprctl monitors -j | jq -r '.[] | select(.focused) | .name')
wf-recorder -o "$MON" -f "$OUT/romak-70wpm.mp4" &      # no -a: no audio
# or: gpu-screen-recorder -w "$MON" -f 60 -o "$OUT/romak-70wpm.mp4" &   (no -a: no audio)
sleep 2
$PY docs/romak-demo/drive.py docs/demo-romak.json
sleep 1
pkill -INT wf-recorder || pkill -INT gpu-screen-recorder
wait
```

`drive.py` takes the keymap from the running HUD, plays for about 4 min 45 s, and returns when
the last key has faded.

## 6. Check

```bash
hyprctl dispatch focuswindow title:romak-demo && wtype -M ctrl d -m ctrl   # ends cat
diff <(printf '%s' "$(cat docs/romak-demo/text.txt)") <(printf '%s' "$(cat "$OUT/typed.txt")") && echo text matches
ffprobe -v error -show_entries stream=codec_type -of csv=p=0 "$OUT/romak-70wpm.mp4"   # only: video
ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT/romak-70wpm.mp4"     # about 290
```

All three must hold: the typed text matches, the only stream is video, and the duration is close
to the script's plus the few seconds around it. If not, find out why, fix that, and record again
from step 3 — never edit the video to hide a fault.

## 7. Put things back

```bash
makoctl mode -r do-not-disturb
hyprctl reload                      # undoes the cursor setting
bin/zmk-layer-hud stop
bin/zmk-layer-hud start             # the user's own config again, as step 2's status showed
```

Close the `romak-demo` terminal. Report the video's path, its duration and size, and the three
checks of step 6.
