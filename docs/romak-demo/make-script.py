#!/usr/bin/env python3
"""The Romak 34 demo, written once: the script it plays, the text it types, and its chapters.

    python3 docs/romak-demo/make-script.py [--keymap KEYMAP.json]

writes docs/demo-romak.json (host/play.py plays it; docs/demo-scripts.md has the format),
docs/romak-demo/text.txt (what the window must end up with) and docs/romak-demo/chapters.txt (each
chapter's time in the recording, for the video's description). With --keymap (zmk-layer-hud keymap
--config config/romak-34.yaml --dump) it also says how long the demo plays and when each chapter
starts; without, the chapters are left as they were.

Each part is text, the pace it is typed at, and how slowed (`speed`: slow motion, so a one-shot
layer is seen up before its key, with the heatmap off). The narration says what the video shows as
it shows it.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "host"))

LEAD_MS = 1500      # before the first key, so the recording starts on a still board
TAIL_MS = 3000      # after the last, before it stops
RECORD_LEAD_S = 2   # docs/romak-demo/AGENT.md starts the recorder this long before drive.py
SLOW = 0.25         # slow motion: a quarter of the pace

# (chapter or None, text, wpm, speed)
PARTS = [
    ("Abertura", "Este é um exemplo de uso do layout Romak, de 70 a 120 wpm:\n\n", 70, 1),
    (None, "À direita, o HUD mostra cada tecla, a camada ativa e o calor das teclas.\n", 75, 1),
    ("Acentos na Alpha 2", "Os acentos ficam na Alpha 2, a um toque do polegar. Em câmera lenta: ", 80, 1),
    (None, "é, ã, ô.", 80, SLOW),
    ("qu numa tecla", "\nO qu sai de uma tecla só da Alpha 2: ", 85, 1),
    (None, "quando.", 85, SLOW),
    ("ç e a camada de extensão", "\nDepois do ç, a camada de extensão dá ão numa tecla: ", 90, 1),
    (None, "ação.", 90, SLOW),
    ("Maiúsculas com shift de um toque", "\nMaiúsculas usam o shift de um toque: ", 95, 1),
    (None, "Às, João.", 95, SLOW),
    ("Tecla de repetição", "\nLetras dobradas usam a tecla de repetição: ", 100, 1),
    (None, "carro, 100.", 100, SLOW),
    # The HUD's wpm is the last ten seconds' (hud.wpm_window_ms): the fast part is longer than that,
    # and typed a little above 120, since layer keys and leads take the measured pace ~5% under.
    ("120 wpm", "\nSem câmera lenta, o ritmo sobe até 120 palavras por minuto. Repare no calor: ", 110, 1),
    (None, "a fileira do meio trabalha mais, e os polegares cuidam do espaço, dos acentos e das "
           "camadas.\n\n", 125, 1),
    ("Onde encontrar", "O layout Romak está disponível em https://rafaelromao.github.io/romak", 125, 1),
]


def script():
    """The script, and the step each part is (1-based, as host/play.py counts them)."""
    steps, at = [{"wait": LEAD_MS}], []
    for _, text, wpm, speed in PARTS:
        step = {"type": text, "wpm": wpm}
        if speed != 1:
            # Slow motion shows the keys one by one: the heatmap is off for it, and back after.
            step["speed"] = speed
            steps.append({"heatmap": "off"})
        steps.append(step)
        at.append(len(steps))
        if speed != 1:
            steps.append({"heatmap": "live"})
    steps.append({"wait": TAIL_MS})
    return {"wpm": 70, "repeat": True, "steps": steps}, at


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--keymap", help="the keymap message, to time the demo and its chapters")
    args = p.parse_args(argv)
    s, at = script()
    with open(os.path.join(ROOT, "docs", "demo-romak.json"), "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
        f.write("\n")
    with open(os.path.join(HERE, "text.txt"), "w", encoding="utf-8") as f:
        f.write("".join(text for _, text, _, _ in PARTS) + "\n")
    if not args.keymap:
        return 0

    import play
    with open(args.keymap, encoding="utf-8") as f:
        keymap = json.load(f)
    c = play.compile(s, keymap)
    for pr in c.problems:
        print(play.describe(pr, "demo-romak.json"), file=sys.stderr)
    first = {}
    for st in c.strokes:
        first.setdefault(st["step"], st["at_ms"])
    lines = []
    for n, (chapter, *_rest) in enumerate(PARTS):
        if chapter:
            when = RECORD_LEAD_S + first[at[n]] / 1000
            lines.append(f"{int(when // 60)}:{int(when % 60):02d} {chapter}")
    with open(os.path.join(HERE, "chapters.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    total = RECORD_LEAD_S + c.duration_ms / 1000
    print(f"{len(c.strokes)} keystrokes; the recording runs about {int(total // 60)}:{int(total % 60):02d}")
    print("\n".join(lines))
    return 1 if c.problems else 0


if __name__ == "__main__":
    sys.exit(main())
