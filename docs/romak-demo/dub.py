#!/usr/bin/env python3
"""The Romak 34 demo, narrated in Brazilian Portuguese, and the same take as a GIF.

    PY=~/.cache/zmk-showcase/tts/bin/python
    $PY docs/romak-demo/dub.py anchor    where the script's clock sits in the take
    $PY docs/romak-demo/dub.py render    one clip per line, Piper pt_BR-faber-medium
    $PY docs/romak-demo/dub.py master    lay the clips, normalise, mux onto the take
    $PY docs/romak-demo/dub.py gif       the take, silent, as a GIF
    $PY docs/romak-demo/dub.py all       the four, in order

The take is docs/romak-demo.mp4, recorded on Omarchy (REPORT.md). What this makes sits beside it,
docs/romak-demo-pt-br.mp4 and docs/romak-demo.gif; the clips, the beds and the cues it measures go to
docs/romak-demo/run/, which git ignores. It runs where the showcase's dub did (zmk-vim-mode's
showcase/README.md, "Voice"), from the same ~/.cache/zmk-showcase, with the showcase's delivery:
-16 LUFS with a -1.5 dBTP ceiling, 48 kHz stereo, the picture copied. The voice is Piper's
pt_BR-faber-medium (huggingface.co/rhasspy/piper-voices, into ~/.cache/zmk-showcase/voices): the
author chose it over kokoro's pt-BR voices, which hurry a line and run its words together.

Every line is spoken as the take starts typing it. When is the script's own: host/play.py compiles
docs/demo-romak.json on config/romak-34.yaml exactly as drive.py did when it was recorded, so each
part's first keystroke is known to the millisecond on that clock; `anchor` finds where that clock
sits in the take, matching the HUD's keys lighting up against the script's presses, and refuses a
take that does not match. In slow motion each example word is spoken as it is typed. The text is
make-script.py's; SPOKEN below is how it is said where reading it as written would not do (wpm,
HUD, qu, ç, the link).
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TAKE = os.path.join(ROOT, "docs", "romak-demo.mp4")
RUN = os.path.join(HERE, "run")
CLIPS = os.path.join(RUN, "clips")
OUT = os.path.join(ROOT, "docs", "romak-demo-pt-br.mp4")
GIF = os.path.join(ROOT, "docs", "romak-demo.gif")
sys.path.insert(0, os.path.join(ROOT, "host"))

VOICE = os.environ.get("ROMAK_DUB_VOICE", "pt_BR-faber-medium")
LENGTH = os.environ.get("ROMAK_DUB_LENGTH", "")            # Piper's length_scale; empty: the voice's own
LEAD_S = float(os.environ.get("ROMAK_DUB_LEAD", "0.3"))   # a line starts this far ahead of its typing
PIPER = os.path.expanduser(os.environ.get("ZMK_PIPER_VENV", "~/.cache/zmk-showcase/tts"))
VOICES = os.path.expanduser("~/.cache/zmk-showcase/voices")
LUFS, TRUE_PEAK = -16, -1.5

# make-script.py's PARTS, as spoken: a line, or for a slow-motion part the words it shows. A line
# is read a piece at a time and the pieces joined with the pauses below: a clause, and `{...}`, a
# letter or a macro read out, which has a pause either side. `[...]` is IPA, said as written: an
# accented vowel or an "e" on its own is otherwise read as the letter's name.
SPOKEN = [
    "Este é um exemplo de uso do layout Romak, de setenta a cento e vinte palavras por minuto.",
    "À direita, o painel mostra cada tecla, a camada ativa e o calor das teclas.",
    "Os acentos ficam na Alfa dois, a um toque do polegar. Em câmera lenta.",
    ["[ˈɛ]", "[ˈɐ̃]", "[ˈo]"],
    "As letras {quê} [i] {u} saem de uma tecla só da Alfa dois.",
    ["quando."],
    "Depois do {cê-cedilha}, a camada de extensão dá {ão} numa tecla.",
    ["ação."],
    "Maiúsculas usam o shift de um toque.",
    ["Às.", "João."],
    "Letras dobradas usam a tecla de repetição.",
    ["carro.", "cem."],
    "Sem câmera lenta, o ritmo sobe até cento e vinte palavras por minuto. Repare no calor:",
    "a fileira do meio trabalha mais, e os polegares cuidam do espaço, dos acentos e das camadas.",
    "O layout Romak está disponível no endereço que aparece na tela.",
]
CLAUSE_PAUSE_S = 0.32      # after a comma, a semicolon, a colon
SENTENCE_PAUSE_S = 0.6     # after a full stop
LETTER_PAUSE_S = 0.25      # before and after a letter or a macro read out
WORD = re.compile(r"[^\W_]+")
CLAUSE = re.compile(r"(?<=[,;:.!?])\s+")
PIECE = re.compile(r"\{([^}]+)\}|\[([^\]]+)\]")
# The voice says ʎ (lh) as a bare glide, "cedía"; a held l before the glide is the lh the author
# hears in cedilha and trabalha.
LH = ["l", "ː", "j"]


def fail(msg):
    sys.exit(f"dub: {msg}")


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def make_script():
    spec = importlib.util.spec_from_file_location("make_script", os.path.join(HERE, "make-script.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def compiled():
    """The script as drive.py played it: (compiled, step of each part, PARTS)."""
    import play
    ms = make_script()
    if len(SPOKEN) != len(ms.PARTS):
        fail(f"SPOKEN has {len(SPOKEN)} lines for make-script.py's {len(ms.PARTS)} parts")
    s, at = ms.script()
    with open(os.path.join(ROOT, "docs", "demo-romak.json"), encoding="utf-8") as f:
        if json.load(f) != s:
            fail("docs/demo-romak.json is not what make-script.py writes: the take may not be this script")
    km = json.loads(run([sys.executable, os.path.join(ROOT, "host", "keymap.py"), "--config",
                         os.path.join(ROOT, "config", "romak-34.yaml"), "--dump"],
                        capture_output=True, text=True).stdout)
    return play.compile(s, km), at, ms.PARTS


def cues(c, at, parts):
    """(seconds on the script's clock, text) for every line and every slow-motion word."""
    t, presses = 0, []
    for wait, m in c.timeline:
        t += wait
        if m["kind"] == "press":
            presses.append(t)

    def first_press(stroke):
        """When the keys for this keystroke start going down: the one-shot or the layer key
        before its letter (the thumb on Alpha 2, a Shift tapped), else the letter itself."""
        g = c.strokes.index(stroke)
        before = c.strokes[g - 1]["at_ms"] if g else -1
        return min(p for p in presses if before < p <= stroke["at_ms"])

    out = []
    for n, (_, text, _, speed) in enumerate(parts):
        strokes = [s for s in c.strokes if s["step"] == at[n]]
        if speed == 1:
            out.append((strokes[0]["at_ms"] / 1000 - LEAD_S, SPOKEN[n]))
            continue
        # Each word, as the keys that type its first character start going down.
        starts, typed = [], ""
        for s in strokes:
            starts.append(len(typed))
            typed += "\n" if s["text"] == "\n" else s["text"]
        words = list(WORD.finditer(typed))
        if len(words) != len(SPOKEN[n]):
            fail(f"part {n + 1} shows {len(words)} words, SPOKEN says {len(SPOKEN[n])}")
        for w, say in zip(words, SPOKEN[n]):
            k = max(i for i, st in enumerate(starts) if st <= w.start())
            out.append((first_press(strokes[k]) / 1000, say))
    return out


# ---------- anchor ----------

def anchor():
    """Seconds into the take of the script's 0: the HUD's board lighting up, matched to the
    script's presses. Sampled in one decode (never -ss before -i: these takes seek to a keyframe)."""
    import numpy as np
    c, _, _ = compiled()
    w, h, fps = 180, 80, 60.0
    raw = run(["ffmpeg", "-v", "error", "-i", TAKE, "-vf",
               f"crop=720:320:1470:230,scale={w}:{h},format=gray", "-fps_mode", "passthrough",
               "-f", "rawvideo", "-"], capture_output=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, h, w).astype(np.int16)
    d = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))
    onsets = np.array([i + 1 for i in range(1, len(d)) if d[i] > 1.5 >= d[i - 1]]) / fps
    t, presses = 0, []
    for wait, m in c.timeline:
        t += wait
        if m["kind"] == "press":
            presses.append(t / 1000)
    presses = np.array(presses)
    best = max((float((np.min(np.abs(onsets[:, None] - presses[None, :] - off), axis=1) < 0.05).mean()), off)
               for off in np.arange(0.0, 6.0, 0.005))
    hit, off = best
    res = onsets[:, None] - presses[None, :] - off
    res = res[np.arange(len(onsets)), np.argmin(np.abs(res), axis=1)]
    near = np.abs(res) < 0.05
    if len(onsets) < 20 or hit < 0.9:
        fail(f"the take does not follow the script: {hit:.0%} of {len(onsets)} changes on the HUD land on a press")
    halves = [float(np.median(res[near & (onsets < onsets.mean())])), float(np.median(res[near & (onsets >= onsets.mean())]))]
    if abs(halves[1] - halves[0]) > 0.05:
        fail(f"the take drifts against the script: {halves[0] * 1000:.0f} ms early on, {halves[1] * 1000:.0f} ms late")
    off += float(np.median(res[near]))
    print(f"anchor: the script's 0 is {off:.3f} s into the take ({hit:.0%} of {len(onsets)} HUD changes on a press)")
    os.makedirs(RUN, exist_ok=True)
    with open(os.path.join(RUN, "anchor.json"), "w") as f:
        json.dump({"offset_s": round(off, 3), "matched": hit, "changes": int(len(onsets))}, f)
    return off


def offset():
    path = os.path.join(RUN, "anchor.json")
    if not os.path.isfile(path):
        return anchor()
    with open(path) as f:
        return json.load(f)["offset_s"]


# ---------- render ----------

def pieces(text):
    """A line as the pieces it is rendered in: {text, ipa, pause after}. Clauses end on their
    punctuation's pause; a {letter} has a pause either side; [ipa] is said as written."""
    out = []
    clauses = CLAUSE.split(text.strip())
    for n, clause in enumerate(clauses):
        at = 0
        for m in PIECE.finditer(clause):
            before = clause[at:m.start()].strip()
            if before:
                out.append({"text": before, "ipa": False, "pause": 0.0})
            if m.group(1) and out:
                out[-1]["pause"] = max(out[-1]["pause"], LETTER_PAUSE_S)
            out.append({"text": m.group(1) or m.group(2), "ipa": m.group(2) is not None,
                        "pause": LETTER_PAUSE_S if m.group(1) else 0.0})
            at = m.end()
        rest = clause[at:].strip()
        if rest and rest not in ",;:.!?":
            out.append({"text": rest, "ipa": False, "pause": 0.0})
        elif rest and out:
            out[-1]["text"] += rest   # the clause's own mark, after a piece
        if n < len(clauses) - 1:
            mark = clause.rstrip()[-1]
            out[-1]["pause"] = max(out[-1]["pause"], SENTENCE_PAUSE_S if mark in ".!?" else CLAUSE_PAUSE_S)
        else:
            out[-1]["pause"] = 0.0
    return out


def render():
    """One wav per cue, Piper at the voice's own rate; says when a line would still be speaking at
    the next."""
    py = os.path.join(PIPER, "bin", "python")
    model = os.path.join(VOICES, f"{VOICE}.onnx")
    for p in (py, model, model + ".json"):
        if not os.path.exists(p):
            fail(f"no {p}: Piper as zmk-vim-mode's showcase/README.md sets it up, and the voice beside it")
    c, at, parts = compiled()
    lines = cues(c, at, parts)
    shutil.rmtree(CLIPS, ignore_errors=True)
    os.makedirs(CLIPS)
    jobs = [pieces(text) for _, text in lines]
    prog = r"""
import sys, json, wave, numpy as np
from piper import PiperVoice
from piper.config import SynthesisConfig
v = PiperVoice.load(sys.argv[1])
sr, out, lh = v.config.sample_rate, sys.argv[2], json.loads(sys.argv[4])
syn = SynthesisConfig(length_scale=float(sys.argv[3])) if sys.argv[3] else None

def trim(a):
    loud = np.flatnonzero(np.abs(a) > np.abs(a).max() * 10 ** (-45 / 20))
    return a[loud[0]:loud[-1] + 1] if len(loud) else a[:0]

for i, line in enumerate(json.load(sys.stdin)):
    parts = []
    for p in line:
        sentences = [list(p["text"].replace(" ", ""))] if p["ipa"] else v.phonemize(p["text"])
        for ph in sentences:
            ph = [q for c in ph for q in (lh if c == "ʎ" else [c])]
            parts.append(trim(v.phoneme_ids_to_audio(v.phonemes_to_ids(ph), syn)))
        parts.append(np.zeros(int(p["pause"] * sr), dtype=np.float32))
    a = np.concatenate(parts)
    a = a * min(1.0, 0.99 / max(1e-9, float(np.abs(a).max())))
    with wave.open(f"{out}/{i:02d}.wav", "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((a * 32767).astype(np.int16).tobytes())
"""
    run([py, "-c", prog, model, CLIPS, LENGTH, json.dumps(LH)], input=json.dumps(jobs, ensure_ascii=False),
        text=True, stderr=subprocess.DEVNULL)
    import wave
    made, late = [], 0
    for i, (at_s, text) in enumerate(lines):
        with wave.open(os.path.join(CLIPS, f"{i:02d}.wav")) as w:
            dur = w.getnframes() / w.getframerate()
        made.append({"at_s": round(at_s, 3), "dur_s": round(dur, 3), "text": text})
    for a, b in zip(made, made[1:]):
        if a["at_s"] + a["dur_s"] > b["at_s"] - 0.1:
            late += 1
            print(f"dub: {a['text'][:40]!r} still speaking {a['at_s'] + a['dur_s'] - b['at_s']:+.2f} s into "
                  f"{b['text'][:30]!r}", file=sys.stderr)
    with open(os.path.join(RUN, "cues.json"), "w", encoding="utf-8") as f:
        json.dump(made, f, ensure_ascii=False, indent=1)
    print(f"render: {len(made)} clips, {VOICE}, {sum(m['dur_s'] for m in made):.1f} s of voice")
    if late:
        fail(f"{late} line(s) run into the next: shorten them, or slow the part they narrate")


# ---------- master ----------

def master():
    """The clips on the take's clock, -16 LUFS / -1.5 dBTP linear, 48 kHz stereo, muxed with the
    picture copied. The loudness is measured first and applied in a second pass, so the voice is
    scaled once and not pumped."""
    with open(os.path.join(RUN, "cues.json"), encoding="utf-8") as f:
        made = json.load(f)
    off = offset()
    dur = float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", TAKE],
                    capture_output=True, text=True).stdout)
    inputs, chains = [], []
    for i, m in enumerate(made):
        inputs += ["-i", os.path.join(CLIPS, f"{i:02d}.wav")]
        ms = max(0, round((off + m["at_s"]) * 1000))
        chains.append(f"[{i}:a]aresample=48000,adelay={ms}:all=1[a{i}]")
    mix = "".join(f"[a{i}]" for i in range(len(made)))
    bed = os.path.join(RUN, "narration.wav")
    graph = ";".join(chains) + f";{mix}amix=inputs={len(made)}:normalize=0,apad,atrim=0:{dur:.3f},aformat=channel_layouts=stereo[out]"
    run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", graph, "-map", "[out]", "-ar", "48000", bed])
    # The peak is aimed half a dB under the ceiling: the AAC encode overshoots it by about a tenth.
    tp = TRUE_PEAK - 0.5
    meas = run(["ffmpeg", "-hide_banner", "-i", bed, "-af", f"loudnorm=I={LUFS}:TP={tp}:LRA=11:print_format=json",
                "-f", "null", "-"], capture_output=True, text=True).stderr
    m = json.loads(meas[meas.rindex("{"):meas.rindex("}") + 1])
    norm = (f"loudnorm=I={LUFS}:TP={tp}:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
            f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    normed = os.path.join(RUN, "narration-normed.wav")
    run(["ffmpeg", "-v", "error", "-y", "-i", bed, "-af", norm, "-ar", "48000", normed])
    run(["ffmpeg", "-v", "error", "-y", "-i", TAKE, "-i", normed, "-map", "0:v", "-map", "1:a", "-c:v", "copy",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{dur:.3f}", OUT])
    check()


def check():
    streams = run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,sample_rate,channels",
                   "-of", "csv=p=0", OUT], capture_output=True, text=True).stdout.split()
    ebu = run(["ffmpeg", "-hide_banner", "-i", OUT, "-map", "0:a", "-af", "ebur128=peak=true", "-f", "null", "-"],
              capture_output=True, text=True).stderr
    lufs = float(re.findall(r"I:\s+(-?[\d.]+) LUFS", ebu)[-1])
    peak = float(re.findall(r"Peak:\s+(-?[\d.]+) dBFS", ebu)[-1])
    print(f"master: {os.path.relpath(OUT, ROOT)} -- {', '.join(streams)}; {lufs:.1f} LUFS, true peak {peak:.1f} dBTP")
    if abs(lufs - LUFS) > 1 or peak > TRUE_PEAK:
        fail(f"loudness off: {lufs:.1f} LUFS (want {LUFS}), {peak:.1f} dBTP (want <= {TRUE_PEAK})")


# ---------- gif ----------

def gif(width=960, fps=10):
    """The take, silent, as a looping GIF: make-gif.sh's palette (128 colours, no dither, so flat
    panels stay flat), one palette for the whole take."""
    vf = (f"fps={fps},scale={width}:-2:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=full[p];"
          "[b][p]paletteuse=dither=none")
    os.makedirs(RUN, exist_ok=True)
    run(["ffmpeg", "-v", "error", "-y", "-i", TAKE, "-vf", vf, "-loop", "0", GIF])
    print(f"gif: {os.path.relpath(GIF, ROOT)} -- {os.path.getsize(GIF) / 1e6:.1f} MB, {width} px, {fps} fps")


def main(argv):
    if not os.path.isfile(TAKE):
        fail(f"no take at {TAKE}")
    step = argv[1] if len(argv) > 1 else "all"
    steps = {"anchor": [anchor], "render": [render], "master": [master], "check": [check], "gif": [gif],
             "all": [anchor, render, master, gif]}
    if step not in steps:
        fail(f"one of {', '.join(steps)}")
    for f in steps[step]:
        f()


if __name__ == "__main__":
    main(sys.argv)
