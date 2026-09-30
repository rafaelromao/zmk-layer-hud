#!/usr/bin/env python3
"""The landing page, put together: site/'s own files, the HUD page as it ships, and for each sample
board its keymap message and its demo, played ahead of time.

    site/build.py [--strict] [--out build/site]

It runs under the venv (make site): a board is converted the way the panel converts it, with
keymap-drawer's layouts and glyphs, by host/keymap.py. A demo is compiled by host/play.py into the
messages a keyboard would send and when, which the page plays back on its own clock.

Without --strict, a board whose files are not on this machine (the Diamond's keymap lives in
rafaelromao/keyboards) is skipped with a line saying so. With it, as in CI, a board that cannot be
built, a glyph that fell back to text or a demo with a character its keymap cannot type fails the
build: what is published is every board, drawn as it draws at home, or nothing.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

SITE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(SITE)
sys.path.insert(0, os.path.join(REPO, "host"))

import keymap as keymap_mod  # noqa: E402  (host/keymap.py)
import play  # noqa: E402  (host/play.py)

# The page's own files, copied as they are. og.png is rendered once by hand from og.html
# (docs/development.md), so a build before that has none, and says so.
SITE_FILES = ["index.html", "site.css", "site.js", "demo.js", "favicon.svg", "og.html"]
OPTIONAL_FILES = ["og.png"]
GIF = os.path.join(REPO, "docs", "hud.gif")   # what the page shows without JavaScript
REPO_URL = "https://github.com/rafaelromao/zmk-layer-hud"


class BuildError(Exception):
    """A page that cannot be built, said for the user."""


def log(msg):
    print(msg, file=sys.stderr)


def load_manifest(path=os.path.join(SITE, "boards.json")):
    with open(path, encoding="utf-8") as f:
        return json.load(f)["boards"]


def manifest_problems(boards, repo=REPO):
    """What is wrong with the manifest itself, whatever this machine has: the ids, and the files in
    this repo that each board names."""
    problems, seen = [], set()
    for b in boards:
        bid = b.get("id")
        if not isinstance(bid, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", bid):
            problems.append(f"board id {bid!r} is not a lowercase slug")
        elif bid in seen:
            problems.append(f"board id {bid!r} is used twice")
        seen.add(bid)
        for field in ("label", "blurb", "config", "source"):
            if not b.get(field):
                problems.append(f"{bid}: no {field}")
        for field in ("config", "demo"):
            if b.get(field) and not os.path.isfile(os.path.join(repo, b[field])):
                problems.append(f"{bid}: {field} {b[field]} is not in the repo")
    return problems


def publish(message, board):
    """The keymap message as the page gets it: named by the board's public source rather than by a
    path under the home folder of whoever built it, and on a solid panel, since what is behind it is
    the page and not an editor."""
    out = dict(message)
    out["source"] = board["source"]
    out["hud"] = dict(message.get("hud") or {}, opacity=100)
    return out


def missing_glyphs(message):
    """Glyphs the keymap draws that the message does not carry, whose names the page shows as text."""
    wanted = keymap_mod.glyph_names(message["layers"], message["combos"])
    return sorted(wanted - set(message.get("glyphs") or {}))


def capture(script, compiled):
    """A compiled demo in the shape `host/play.py --capture` prints, which hud.js replayTo reads."""
    return {"device": script.get("device"), "opacity": script.get("opacity"),
            "duration_ms": compiled.duration_ms, "timeline": compiled.timeline}


def hud_files(index_html):
    """What hud/index.html loads, from its own src and href attributes, and the page itself."""
    names = re.findall(r'(?:src|href)="([^"#?]+)"', index_html)
    return ["index.html"] + [n for n in names if "://" not in n and not n.startswith("/")]


def build_board(board, strict):
    """(keymap message, demo capture or None) for one board. None when its files are not on this
    machine and that is allowed."""
    def cannot(reason):
        if strict:
            raise BuildError(f"{board['id']}: {reason}")
        log(f"site: {board['id']} skipped: {reason}")

    config = os.path.join(REPO, board["config"])
    if not os.path.isfile(config):   # find_config would fall back to the user's own config
        return cannot(f"no {board['config']}")
    try:
        message = keymap_mod.KeymapSource(config, log=log).load()
    except (keymap_mod.KeymapError, OSError) as e:
        return cannot(str(e))
    missing = missing_glyphs(message)
    if missing:
        said = f"{len(missing)} glyphs would show as text ({', '.join(missing[:4])})"
        if strict:
            raise BuildError(f"{board['id']}: {said}")
        log(f"site: {board['id']}: {said}")
    message = publish(message, board)

    demo = None
    if board.get("demo"):
        script = play.load(os.path.join(REPO, board["demo"]))
        compiled = play.compile(script, message)
        for problem in compiled.problems:
            log("site: " + play.describe(problem, board["demo"]))
        skipped = [p for p in compiled.problems if p.get("level") == "skip"]
        if skipped and strict:
            raise BuildError(f"{board['demo']}: {len(skipped)} characters the {board['id']} keymap cannot type")
        demo = capture(script, compiled)
    return message, demo


def prepare(out):
    """An empty output directory. What is in it is removed only if it is a page built before, so a
    mistaken --out cannot take anything else with it."""
    if os.path.isdir(out) and os.listdir(out):
        if not os.path.isdir(os.path.join(out, "boards")):
            raise BuildError(f"{out} is not empty and is not a page built before; not clearing it")
        shutil.rmtree(out)
    os.makedirs(os.path.join(out, "boards"))
    os.makedirs(os.path.join(out, "hud"))


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")


def commit():
    try:
        return subprocess.run(["git", "-C", REPO, "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def build(out, strict=False, boards=None):
    """Build the page into `out`; returns the boards built, as boards/index.json lists them."""
    try:
        import keymap_drawer  # noqa: F401
    except ImportError:
        # Without it keymap.py falls back to layouts of its own, which draw differently from the
        # panel a visitor would install.
        raise BuildError("keymap-drawer is not installed for this Python: make venv, then make site")
    boards = load_manifest() if boards is None else boards
    problems = manifest_problems(boards)
    if problems:
        raise BuildError("boards.json: " + "; ".join(problems))
    prepare(out)

    built = []
    for board in boards:
        result = build_board(board, strict)
        if result is None:
            continue
        message, demo = result
        entry = {"id": board["id"], "label": board["label"], "blurb": board["blurb"],
                 "keymap": f"boards/{board['id']}.json", "demo": None}
        write_json(os.path.join(out, entry["keymap"]), message)
        if demo:
            entry["demo"] = f"boards/{board['id']}-demo.json"
            write_json(os.path.join(out, entry["demo"]), demo)
        built.append(entry)
    if not built:
        raise BuildError("no board could be built")
    write_json(os.path.join(out, "boards", "index.json"), {"boards": built})

    with open(os.path.join(REPO, "hud", "index.html"), encoding="utf-8") as f:
        for name in hud_files(f.read()):
            shutil.copyfile(os.path.join(REPO, "hud", name), os.path.join(out, "hud", name))
    for name in SITE_FILES:
        shutil.copyfile(os.path.join(SITE, name), os.path.join(out, name))
    for name in OPTIONAL_FILES:
        if os.path.isfile(os.path.join(SITE, name)):
            shutil.copyfile(os.path.join(SITE, name), os.path.join(out, name))
        else:
            log(f"site: no site/{name} yet (docs/development.md says how to render it)")
    shutil.copyfile(GIF, os.path.join(out, "hud.gif"))

    sha = commit()
    stamp = f'<a href="{REPO_URL}/commit/{sha}">{sha}</a>' if sha else "a working copy"
    index = os.path.join(out, "index.html")
    with open(index, encoding="utf-8") as f:
        html = f.read()
    with open(index, "w", encoding="utf-8") as f:
        f.write(html.replace("<!--commit-->", stamp))
    return built


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--strict", action="store_true",
                   help="fail, rather than skip, on anything that would not be drawn as it is at home (CI)")
    p.add_argument("--out", default=os.path.join(REPO, "build", "site"), help="where the page goes (default: build/site)")
    args = p.parse_args(argv)
    try:
        built = build(os.path.abspath(args.out), strict=args.strict)
    except (BuildError, play.PlayError) as e:
        log(f"site: {e}")
        return 1
    log(f"site: {', '.join(b['id'] for b in built)} in {os.path.relpath(os.path.abspath(args.out))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
