#!/usr/bin/env python3
"""The landing page, put together: site/'s own files, the HUD page as it ships, and for each sample
board its keymap message and its demo, played ahead of time.

    site/build.py [--strict] [--out docs]      build it (GitHub Pages serves docs/)
    site/build.py --check [--out docs]         say which published files are behind hud/ or site/

A board's keymap message is built the way the panel builds it, by host/keymap.py, from the
config and the definitions committed beside it (config/*.definitions.json, which `make samples`
writes again with `zmk-layer-hud import`). So nothing here needs keymap-drawer, a keyboard's repo
or the network. A demo is compiled by host/play.py into the messages a keyboard would send and
when, which the page plays back on its own clock.

What it writes into --out is the page and nothing else: the names it is made of are removed and
written afresh, and everything else there -- in docs/, the docs, the demo scripts and hud.gif,
which the page uses where it is -- is left alone. The output is committed, so a build of
unchanged sources changes nothing.

Without --strict, a board whose definitions are missing is skipped with a line saying so. With
it, a board that cannot be built, a glyph that fell back to text or a demo with a character its
keymap cannot type fails the build: what is published is every board, drawn as it draws at home,
or nothing.
"""

import argparse
import filecmp
import json
import os
import re
import shutil
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
DOCS = os.path.join(REPO, "docs")


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
    path under the home folder of whoever built it, on a solid panel, since what is behind it is the
    page and not an editor, and with dark keys, the heat in indigo, as the page around it is dark."""
    out = dict(message)
    out["source"] = board["source"]
    out["hud"] = dict(message.get("hud") or {}, opacity=100, dark=1)
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


def owned(out):
    """The names in `out` the page is made of. hud.gif is one of them only where it is a copy: in
    docs/ it is the GIF itself."""
    names = SITE_FILES + OPTIONAL_FILES + ["hud", "boards"]
    if os.path.abspath(os.path.join(out, "hud.gif")) != os.path.abspath(GIF):
        names.append("hud.gif")
    return names


def prepare(out):
    """`out` with none of the page left in it, ready to be written: the names the page is made of
    are removed, and nothing else is touched. A directory outside this repo that already holds
    other things and no page is refused, so a mistaken --out cannot lose anything."""
    inside = os.path.commonpath([os.path.abspath(out), REPO]) == REPO
    if (not inside and os.path.isdir(out) and os.listdir(out)
            and not os.path.isfile(os.path.join(out, "boards", "index.json"))):
        raise BuildError(f"{out} holds other things and no page built before; not writing into it")
    os.makedirs(out, exist_ok=True)
    for name in owned(out):
        path = os.path.join(out, name)
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        elif os.path.lexists(path):
            os.remove(path)
    os.makedirs(os.path.join(out, "boards"))
    os.makedirs(os.path.join(out, "hud"))


def stale(out):
    """The published files that are not what a build would copy now -- the page's own and the
    HUD's -- so a page left behind by a change to hud/ or site/ is noticed. The boards are not
    compared: converting them needs the venv."""
    with open(os.path.join(REPO, "hud", "index.html"), encoding="utf-8") as f:
        pairs = [(os.path.join(REPO, "hud", n), os.path.join(out, "hud", n)) for n in hud_files(f.read())]
    pairs += [(os.path.join(SITE, n), os.path.join(out, n)) for n in SITE_FILES + OPTIONAL_FILES
              if os.path.isfile(os.path.join(SITE, n))]
    return [os.path.relpath(dst, REPO) for src, dst in pairs
            if not (os.path.isfile(dst) and filecmp.cmp(src, dst, shallow=False))]


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")


def build(out, strict=False, boards=None):
    """Build the page into `out`; returns the boards built, as boards/index.json lists them."""
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
    if "hud.gif" in owned(out):
        shutil.copyfile(GIF, os.path.join(out, "hud.gif"))
    return built


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--strict", action="store_true",
                   help="fail, rather than skip, on anything that would not be drawn as it is at home")
    p.add_argument("--out", default=DOCS, help="where the page goes (default: docs, which GitHub Pages serves)")
    p.add_argument("--check", action="store_true",
                   help="build nothing; say which published files are behind hud/ or site/")
    args = p.parse_args(argv)
    out = os.path.abspath(args.out)
    if args.check:
        # A note rather than a failure: the page is published by committing it, and a change to
        # the HUD need not wait for that.
        if os.path.isfile(os.path.join(out, "index.html")):
            behind = stale(out)
            if behind:
                log(f"site: the published page is behind: {', '.join(behind)} (make site, then commit {os.path.relpath(out, REPO)}/)")
        return 0
    try:
        built = build(out, strict=args.strict)
    except (BuildError, play.PlayError) as e:
        log(f"site: {e}")
        return 1
    log(f"site: {', '.join(b['id'] for b in built)} in {os.path.relpath(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
