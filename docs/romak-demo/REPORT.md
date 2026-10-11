# Romak 34 demo recording report

Recorded 2026-10-11 on Omarchy (Hyprland), following `AGENT.md` in this directory.
The video is `../romak-demo.mp4`, next to the script that generated it
(`../demo-romak.json`, written by `make-script.py`).

## Setup

- HEAD `19b66c3` (contains `b98f9c5`), tree clean, nothing committed or pushed.
- Script: 650 keystrokes, 2897 messages over 113.5 s (`host/play.py --check
  --strict` passed; `--dry-run` output identical to `text.txt`).
- HUD on `config/romak-34.yaml` with `--reserve`, panel showing "Romak 34",
  stats `heat live` (each key glows as struck, off during slow-motion parts).
- Editor: the only window besides the HUD on its own workspace, focused.
- Screen quiet: notifications DND on, cursor hidden after 1 s of inactivity.
- Chapters for the video description (`chapters.txt`):
  - 0:03 Abertura
  - 0:27 Acentos na Alpha 2
  - 0:44 qu numa tecla
  - 0:53 ç e a camada de extensão
  - 1:04 Maiúsculas com shift de um toque
  - 1:15 Tecla de repetição
  - 1:27 120 wpm
  - 1:45 Onde encontrar

## Result

- Video: `docs/romak-demo.mp4`, 116.33 s, 10901524 bytes (11M), silent.
- Step 6 checks, all three passed:
  - typed text identical: `diff text.txt typed.txt` → `text matches`;
  - video-only stream: `ffprobe codec_type` → `video`;
  - duration 116.33 s ≈ 116 (113.5 script + 2 s lead + 1 s tail).

## Deviations from AGENT.md and why

- Recorder: `gpu-screen-recorder -encoder cpu` instead of `wf-recorder`
  (absent; GPU encoding unsupported here — NVENC API mismatch, no VAAPI).
  No `-a`: no audio, as required. Stopped with `pkill -INT -f`
  (the bare name never matches: it is longer than 15 characters).
- Notifications: `makoctl` is absent (this system uses Omarchy notifications),
  so DND went through `omarchy-shell notifications setDnd on/off`.
- `hyprctl keyword`/`dispatch` with the old syntax fail on Hyprland 0.56;
  the equivalents went through `hyprctl eval 'hl.dispatch(...)'` /
  `hl.config(...)`. Nothing else about the setup was changed.
- Editor: `nvim --clean` with `wrap+linebreak` (wraps between words, never
  mid-word) inside ghostty `--font-size=18`, instead of plain `cat`, per the
  user's request. Saved with `:w` after the recorder stopped, so saving is not
  in the video; the diff still passed.
- Workspace: the editor ran on a separate workspace, alone besides the HUD,
  so no other window appears in the recording.
- Session: recorded with a scratch `romak-demo` session, then back on the
  user's `2026-10-01-0846`. Under the live heatmap the demo does not reach
  user sessions.
- Keyboard: unplugged for the take, as the current AGENT.md asks (its
  heartbeat re-asserts layers about every two seconds and can cut a one-shot
  short on camera). Note: the Diamond's hidraw nodes were still listed when
  the take started; if a one-shot flickers in the video, that is why.

## Restored afterwards (step 7)

DND off, cursor timeout back to default (`hyprctl reload`), `heatmap session`,
the user's own Diamond config started, panel hidden as it was, session
`2026-10-01-0846` active, the `romak-demo` terminal closed.
