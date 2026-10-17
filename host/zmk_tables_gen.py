"""host/zmk_tables.py, written from a ZMK checkout: what a keymap's names stand for.

The touch keyboard types what a key does on the real keyboard, so it has to know what `&kp N1`,
`&mt LSHIFT A` and `&sk LS(LGUI)` mean -- the values ZMK's headers give the names, the defaults its
built-in behaviours are defined with (`&mt` is hold-preferred with a 200 ms term, `&sl` releases
quickly), and the defaults its bindings and sources fall back to. Those are ZMK's facts, so they are
read from ZMK's own files rather than copied by hand, and a generated module keeps the runtime free
of any of this parsing:

  app/include/dt-bindings/zmk/{hid_usage_pages,hid_usage,modifiers,keys,pointing}.h  names
  app/dts/behaviors/*.dtsi                       the built-in behaviours (&kp, &mt, &lt, &sk, ...)
  app/dts/bindings/behaviors/*.yaml              property defaults the bindings declare
  app/src/behaviors/*.c, app/Kconfig             defaults the sources fall back to

ZMK is MIT licensed; host/vendor/zmk/NOTICE carries its notice, and the generated module records
the commit it was made from. host/zmk_tables_test.py checks the facts the rest of the HUD leans on.

    python3 host/zmk_tables_gen.py --zmk DIR           print it
    python3 host/zmk_tables_gen.py --zmk DIR --write   write host/zmk_tables.py
    python3 host/zmk_tables_gen.py --zmk DIR --check   fail when the committed one is behind

DIR defaults to $ZMK_DIR: a ZMK checkout, the directory holding app/.
"""

import glob
import os
import pprint
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import zmk_expr  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "zmk_tables.py")
HEADERS = ("hid_usage_pages.h", "hid_usage.h", "modifiers.h", "keys.h", "pointing.h")
# Function-like macros a keymap's parameters may call. BIT comes from Zephyr's dt-util.h, which the
# keymap includes through pointing.h; it is the one definition written here rather than read.
CALLABLE = ("ZMK_HID_USAGE", "APPLY_MODS", "LC", "LS", "LA", "LG", "RC", "RS", "RA", "RG",
            "MOVE", "MOVE_X", "MOVE_Y", "BIT")
ZEPHYR = {"BIT": (["n"], "(1 << (n))")}
NOT_PROPS = {"#binding-cells", "display-name", "label"}
PREPROCESSOR = re.compile(r"\s*#\s*(if|ifdef|ifndef|elif|else|endif|include|define|undef|pragma)\b")


def defines(text):
    """`#define`s of a header: ({name: body}, {name: (params, body)})."""
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"\\\n", " ", text)
    values, functions = {}, {}
    for line in text.splitlines():
        line = re.sub(r"//.*", "", line)
        m = re.match(r"\s*#\s*define\s+(\w+)(\(([^)]*)\))?\s*(.*)$", line)
        if not m:
            continue
        name, params, body = m.group(1), m.group(3), m.group(4).strip()
        if m.group(2) is not None:
            functions[name] = ([p.strip() for p in params.split(",") if p.strip()], body)
        else:
            values[name] = body
    return values, functions


def header_tables(zmk):
    """Every name a keymap may use as a parameter, with its value: the keycodes of keys.h (pages,
    ids and implicit modifiers, encoded the ZMK way), the modifier bits, and pointing's buttons and
    moves. Names whose value is not a number (a function-like macro's helpers) are left out."""
    base = os.path.join(zmk, "app", "include", "dt-bindings", "zmk")
    values, functions = {}, dict(ZEPHYR)
    per_file = {}
    for h in HEADERS:
        with open(os.path.join(base, h), encoding="utf-8") as f:
            v, fn = defines(f.read())
        per_file[h] = v
        values.update(v)
        functions.update(fn)

    def ints(names):
        out = {}
        for name in names:
            try:
                out[name] = zmk_expr.evaluate(name, values, functions)
            except zmk_expr.Unknown:
                pass
        return out

    keycodes = ints(per_file["keys.h"])
    modifiers = {k: v for k, v in ints(per_file["modifiers.h"]).items() if k.startswith("MOD_")}
    pointing = {k: v for k, v in ints(per_file["pointing.h"]).items() if not k.startswith("ZMK_POINTING_DEFAULT")}
    pages = {k: v for k, v in ints(per_file["hid_usage_pages.h"]).items() if k.startswith("HID_USAGE_")}
    callable_ = {name: functions[name] for name in CALLABLE}
    return keycodes, modifiers, pointing, pages, callable_


def _body_props(body):
    """A node body's properties, in the shapes keymap-drawer's DTNode hands them over: a phandle
    array as ["&kp ESC", ...], an array as its whitespace-split cells, a string, or True."""
    props = {}
    for stmt in body.split(";"):
        stmt = stmt.strip()
        if not stmt:
            continue
        name, eq, value = stmt.partition("=")
        name, value = name.strip(), value.strip()
        if not eq:
            props[name] = True
        elif value.startswith('"'):
            props[name] = value.strip('"')
        else:
            arrays = re.findall(r"<([^>]*)>", value)
            joined = " ".join(arrays)
            if "&" in joined:
                props[name] = [f"&{b.strip()}" for b in joined.split("&") if b.strip()]
            else:
                props[name] = joined.split()
    return props


def behaviors(zmk):
    """The built-in behaviours by label: {compatible, cells, props}."""
    out = {}
    for path in sorted(glob.glob(os.path.join(zmk, "app", "dts", "behaviors", "*.dtsi"))):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
        text = re.sub(r"\\\n", " ", text)
        text = "\n".join(line for line in text.splitlines() if not PREPROCESSOR.match(line))
        text = re.sub(r"//.*", "", text).replace("/omit-if-no-ref/", "")
        for label, _name, body in re.findall(r"(\w+)\s*:\s*([\w,-]+)\s*\{([^{}]*)\};", text):
            props = _body_props(body)
            compatible = props.get("compatible")
            # The macro controls (&macro_tap, &macro_wait_time 50, &macro_param_1to1) are not
            # behaviours of their own, but a macro's bindings are read with them.
            if not isinstance(compatible, str) or not compatible.startswith(("zmk,behavior-", "zmk,macro-")):
                continue
            cells = props.get("#binding-cells", ["0"])
            out[label] = {"compatible": compatible, "cells": int(cells[0]),
                          "props": {k: v for k, v in props.items() if k not in NOT_PROPS and k != "compatible"}}
    return out


def yaml_defaults(zmk):
    """compatible -> {property: default} from the bindings' YAML: just the `default:` lines under
    `properties:`, which is all of YAML these files need read."""
    out = {}
    for path in sorted(glob.glob(os.path.join(zmk, "app", "dts", "bindings", "behaviors", "zmk,behavior-*.yaml"))):
        compatible, prop, inside, found = None, None, False, {}
        with open(path, encoding="utf-8") as f:
            for line in f:
                if m := re.match(r'compatible:\s*"([^"]+)"', line):
                    compatible = m.group(1)
                elif re.match(r"properties:\s*$", line):
                    inside = True
                elif inside and (m := re.match(r'  ("?)([\w#,-]+)\1:\s*$', line)):
                    prop = m.group(2)
                elif inside and prop and (m := re.match(r"    default:\s*(.+?)\s*$", line)):
                    found[prop] = _yaml_value(m.group(1))
                elif line.strip() and not line.startswith(" "):
                    inside = False
        if compatible and found:
            out[compatible] = found
    return out


def _yaml_value(text):
    if text.startswith('"'):
        return text.strip('"')
    if text == "[]":
        return []
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    return text


def kconfig_defaults(zmk):
    with open(os.path.join(zmk, "app", "Kconfig"), encoding="utf-8") as f:
        text = f.read()
    return {f"CONFIG_{name}": int(value)
            for name, value in re.findall(r"config (\w+)\n(?:[ \t]+[^\n]*\n)*?[ \t]+default (\d+)\n", text)}


def source_defaults(zmk, constants):
    """compatible -> {property: default} from `DT_INST_PROP_OR(n, prop, value)` and `DT_PROP_OR(inst,
    prop, value)` in the behaviours' sources: what an unset property is, where no YAML says."""
    out = {}
    for path in sorted(glob.glob(os.path.join(zmk, "app", "src", "behaviors", "*.c"))):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        compats = re.findall(r"#define DT_DRV_COMPAT (\w+)", text) or \
            re.findall(r"DT_FOREACH_STATUS_OKAY\((zmk_behavior_\w+)", text)
        found = {}
        for prop, value in re.findall(r"DT_(?:INST_)?PROP_OR\(\w+,\s*(\w+),\s*([^)]+?)\)", text):
            value = value.strip()
            if value in ("true", "false"):
                found[prop.replace("_", "-")] = value == "true"
                continue
            try:
                found[prop.replace("_", "-")] = zmk_expr.evaluate(value, constants)
            except zmk_expr.Unknown:
                continue
        for c in set(compats):
            if found:
                out.setdefault(c.replace("_", ",", 1).replace("_", "-"), {}).update(found)
    return out


def commit(zmk):
    try:
        return subprocess.run(["git", "-C", zmk, "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def generate(zmk):
    keycodes, modifiers, pointing, pages, callable_ = header_tables(zmk)
    builtins = behaviors(zmk)
    defaults = yaml_defaults(zmk)
    constants = dict(modifiers, **kconfig_defaults(zmk))
    for compatible, found in source_defaults(zmk, constants).items():
        for prop, value in found.items():
            defaults.setdefault(compatible, {}).setdefault(prop, value)
    lines = [
        '"""What a ZMK keymap\'s names and built-in behaviours stand for. Generated by',
        "host/zmk_tables_gen.py from ZMK's headers, behaviours, bindings and sources -- do not edit; run",
        "`make zmk-tables ZMK_DIR=...` against a ZMK checkout instead. ZMK is MIT licensed:",
        'host/vendor/zmk/NOTICE.',
        "",
        "KEYCODES     name -> the ZMK encoding: implicit modifiers << 24 | usage page << 16 | usage id",
        "MODIFIERS    MOD_LCTL ... MOD_RGUI -> their bit in the HID modifier byte",
        "POINTING     mouse buttons and moves (&mkp LCLK, &mmv MOVE_UP)",
        "PAGES        HID_USAGE_KEY and the other usage pages",
        "FUNCTIONS    the macros a parameter may call (LS(A), BIT(2), MOVE(1, 2)): name -> (params, body)",
        "BEHAVIORS    the built-in behaviours by label: {compatible, cells, props}, props as DTNode gives them",
        "DEFAULTS     compatible -> {property: the value an unset property has}",
        '"""',
        "",
        f"ZMK_COMMIT = {commit(zmk)!r}",
        "",
    ]
    for name, table in (("KEYCODES", keycodes), ("MODIFIERS", modifiers), ("POINTING", pointing),
                        ("PAGES", pages)):
        lines.append(f"{name} = {{")
        for k in sorted(table):
            lines.append(f"    {k!r}: {table[k]:#x},")
        lines.append("}")
        lines.append("")
    for name, table in (("FUNCTIONS", {k: tuple(v) for k, v in callable_.items()}),
                        ("BEHAVIORS", builtins), ("DEFAULTS", defaults)):
        lines.append(f"{name} = " + pprint.pformat(table, width=110, sort_dicts=True))
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    zmk = os.environ.get("ZMK_DIR")
    if "--zmk" in argv:
        zmk = argv[argv.index("--zmk") + 1]
    if not zmk or not os.path.isdir(os.path.join(zmk, "app")):
        print("zmk_tables_gen: give a ZMK checkout (--zmk DIR or $ZMK_DIR), the directory holding app/",
              file=sys.stderr)
        return 2
    text = generate(os.path.expanduser(zmk))
    if "--check" in argv:
        with open(OUT, encoding="utf-8") as f:
            if f.read() != text:
                print(f"zmk_tables_gen: {os.path.relpath(OUT)} is behind {zmk}; run with --write", file=sys.stderr)
                return 1
        return 0
    if "--write" in argv:
        with open(OUT, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {os.path.relpath(OUT)}")
        return 0
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
