"""docs/cli.md, written from the parsers themselves: every verb's usage, arguments and flags.

The reference is generated rather than written because the parsers already say all of it, and a
second copy by hand would only drift. `poke` and `feed` keep their own parsers (cli.py
PASSTHROUGH), so their help is asked of them. cli_test.py holds the file equal to what this makes;
`make cli-docs` writes it again.

    python3 host/clidocs.py           # print it
    python3 host/clidocs.py --write   # write docs/cli.md
"""

import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cli  # noqa: E402

PATH = os.path.join(cli.ROOT, "docs", "cli.md")
WIDTH = "100"   # argparse wraps to $COLUMNS; fixed, so the file does not depend on the terminal


def verbs(parser):
    for action in parser._subparsers._group_actions:
        if hasattr(action, "choices"):
            return dict(action.choices)
    return {}


def passthrough_help(verb):
    import importlib
    mod = importlib.import_module(cli.PASSTHROUGH[verb])
    argv0, sys.argv[0] = sys.argv[0], f"zmk-layer-hud {verb}"
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.suppress(SystemExit):
            mod.parse_args(["--help"])
    finally:
        sys.argv[0] = argv0
    return out.getvalue()


def render():
    old = os.environ.get("COLUMNS")
    os.environ["COLUMNS"] = WIDTH
    try:
        parser = cli.build_parser()
        subs = verbs(parser)
        parts = ["# Command line reference", "",
                 "Every verb of `zmk-layer-hud`, as its own `--help` prints it. Written by "
                 "`make cli-docs` from the parsers in `host/cli.py` (and `host/hudpoke.py`, "
                 "`host/hudfeed.py` for `poke` and `feed`); do not edit by hand.", ""]
        parts += [f"- [`{name}`](#{name}) — {sub.description}" for name, sub in subs.items()]
        for name, sub in subs.items():
            text = passthrough_help(name) if name in cli.PASSTHROUGH else sub.format_help()
            parts += ["", f"## {name}", "", "```text", text.rstrip(), "```"]
    finally:
        if old is None:
            os.environ.pop("COLUMNS", None)
        else:
            os.environ["COLUMNS"] = old
    # Defaults that name this machine's home (the feed's config path) are written as ~.
    return "\n".join(parts).replace(os.path.expanduser("~"), "~") + "\n"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    text = render()
    if "--write" in argv:
        with open(PATH, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {os.path.relpath(PATH)}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
