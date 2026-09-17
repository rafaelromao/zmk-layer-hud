"""Turn a keymap message on stdin into the committed test fixture.

Every glyph keeps its id but loses its vendor SVG: the tests compare legend markup, not pixels, so
a placeholder exercises legendHTML's glyph branch just as well and keeps the file readable. Written
one value per line with sorted keys, so `make fixture` produces a diff a human can read.
"""
import json
import sys

msg = json.load(sys.stdin)
msg["glyphs"] = {gid: '<svg data-glyph="%s"></svg>' % gid for gid in msg.get("glyphs", {})}
msg["source"] = "examples/diamond.yaml"      # the dump records a path under someone's home
json.dump(msg, sys.stdout, ensure_ascii=False, indent=0, sort_keys=True)
sys.stdout.write("\n")
