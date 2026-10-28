"""The arithmetic of a ZMK binding's parameters: `LS(LGUI)`, `(MOD_LSFT|MOD_RSFT)`, `BIT(1)`.

A keymap's bindings reach the HUD as text. keymap-drawer's preprocessor expands the keymap's own
`#define`s but passes ZMK's headers through, so `&kp LS(LGUI)` arrives spelled that way, and what
it means has to be worked out here: the name's value from ZMK's keys.h, the modifier from
modifiers.h, the two put together the way the header's macros do. The same few lines evaluate the
headers themselves when host/zmk_tables_gen.py turns them into tables.

This is a C preprocessor in miniature, for the subset those headers use: integers, names,
function-like macros with simple arguments, and the operators | & ^ << >> + - * / % ~ and
parentheses. Anything outside it -- a name no table knows, a stray token -- is an Unknown, which the
caller reports rather than guesses.

Stdlib only, and Python 3.9: the Linux panel runs under the system interpreter.
"""

import re

TOKEN = re.compile(r"\s*(0[xX][0-9a-fA-F]+|\d+|[A-Za-z_]\w*|<<|>>|[()|&^~+\-*/%,])")
NUMBER = re.compile(r"0[xX][0-9a-fA-F]+$|\d+$")
OPERATORS = {"(", ")", "|", "&", "^", "~", "+", "-", "*", "/", "%", "<<", ">>"}
MAX_DEPTH = 32


class Unknown(ValueError):
    """An expression this cannot evaluate: a name with no value, or text that is not arithmetic."""


def tokenize(text):
    text = text.strip()
    out, at = [], 0
    while at < len(text):
        m = TOKEN.match(text, at)
        if not m or m.end() == at:
            raise Unknown(f"cannot read {text[at:]!r}")
        out.append(m.group(1))
        at = m.end()
        while at < len(text) and text[at].isspace():
            at += 1
    return out


def _arguments(tokens, at):
    """The arguments of a call whose "(" is at tokens[at - 1]: a list of token lists, and the index
    just past the closing ")"."""
    args, current, depth = [], [], 0
    while at < len(tokens):
        t = tokens[at]
        if t == "(":
            depth += 1
        elif t == ")":
            if depth == 0:
                args.append(current)
                return args, at + 1
            depth -= 1
        elif t == "," and depth == 0:
            args.append(current)
            current = []
            at += 1
            continue
        current.append(t)
        at += 1
    raise Unknown("an unclosed (")


def expand(tokens, values, functions, depth=0):
    """Tokens with every known name replaced: `values` maps an object-like macro to its value (an int,
    or the text of its body), `functions` maps a function-like one to (params, body)."""
    if depth > MAX_DEPTH:
        raise Unknown("macros nest too deep (a macro that names itself?)")
    out, at = [], 0
    while at < len(tokens):
        t = tokens[at]
        if t in functions and at + 1 < len(tokens) and tokens[at + 1] == "(":
            params, body = functions[t]
            args, at = _arguments(tokens, at + 2)
            if args == [[]] and not params:
                args = []
            if len(args) != len(params):
                raise Unknown(f"{t} takes {len(params)} argument(s), got {len(args)}")
            # Arguments are expanded first, then the body around them is scanned again: enough for
            # the headers' nesting (LS(LC(A)), APPLY_MODS(MOD_LSFT, ZMK_HID_USAGE(7, 4))).
            bound = {p: ["("] + expand(a, values, functions, depth + 1) + [")"] for p, a in zip(params, args)}
            body_tokens = []
            for b in tokenize(body):
                body_tokens += bound.get(b, [b])
            out += expand(body_tokens, values, functions, depth + 1)
            continue
        if t in values:
            v = values[t]
            out += ["(", str(v), ")"] if isinstance(v, int) else ["("] + expand(tokenize(v), values, functions, depth + 1) + [")"]
        else:
            out.append(t)
        at += 1
    return out


def evaluate(text, values, functions=None):
    """The integer `text` stands for, or Unknown."""
    tokens = expand(tokenize(text), values, functions or {})
    if not tokens:
        raise Unknown("an empty expression")
    pieces = []
    for t in tokens:
        if NUMBER.match(t):
            pieces.append(str(int(t, 0)))
        elif t in OPERATORS:
            pieces.append("//" if t == "/" else t)
        else:
            raise Unknown(f"no value for {t}")
    try:
        # Only digits, operators and parentheses are left: nothing here can name or call anything.
        value = eval(" ".join(pieces), {"__builtins__": {}}, {})  # noqa: S307
    except Exception as e:
        raise Unknown(f"cannot evaluate {text!r}: {e}") from None
    if not isinstance(value, int):
        raise Unknown(f"{text!r} is not an integer")
    return value


BINARY = {"|", "&", "^", "+", "-", "*", "/", "%", "<<", ">>"}


def group(tokens):
    """Tokens cut into whole expressions, one per devicetree cell or binding parameter. Cells are
    only ever separated by whitespace, so an expression runs on while an operator joins it to the
    next operand, a name is called (`LS` then `(`), or a parenthesis is open."""
    out, at = [], 0
    while at < len(tokens):
        start = at
        at = _operand(tokens, at)
        while at < len(tokens) and tokens[at] in BINARY:
            at = _operand(tokens, at + 1)
        out.append("".join(tokens[start:at]))
    return out


def _operand(tokens, at):
    while at < len(tokens) and tokens[at] in ("~", "-", "+"):
        at += 1
    if at >= len(tokens):
        raise Unknown("an operator with nothing after it")
    t = tokens[at]
    if t == "(":
        return _closed(tokens, at)
    if t in OPERATORS or t == ",":
        raise Unknown(f"unexpected {t!r}")
    if at + 1 < len(tokens) and tokens[at + 1] == "(" and not NUMBER.match(t):
        return _closed(tokens, at + 1)
    return at + 1


def _closed(tokens, at):
    """The index just past the ")" that closes the "(" at tokens[at]."""
    depth = 0
    for i in range(at, len(tokens)):
        depth += tokens[i] == "("
        depth -= tokens[i] == ")"
        if depth == 0:
            return i + 1
    raise Unknown("an unclosed (")


def cells(tokens):
    """A devicetree array's cells as keymap-drawer hands them over -- split at whitespace, so
    `<(MOD_LSFT | MOD_RSFT)>` arrives as three tokens -- put back together, one expression a cell."""
    return group(tokenize(" ".join(tokens))) if tokens else []


def split_binding(binding):
    """`&kp LS(A)` -> ("kp", ["LS(A)"]); `&lt 1 SPACE` -> ("lt", ["1", "SPACE"])."""
    binding = binding.strip()
    if not binding.startswith("&") or len(binding) < 2:
        raise Unknown(f"not a binding: {binding!r}")
    head, _, rest = binding[1:].partition(" ")
    return head, group(tokenize(rest)) if rest.strip() else []
