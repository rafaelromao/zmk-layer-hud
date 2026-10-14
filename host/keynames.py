"""The key vocabulary: every named key and modifier with its key name, glyph and spellings, and the
characters each key of a US layout types. One statement of it, read by every side.

The feed decodes the keyboard's reports with it (host/hiddecode.py); host/uskeys.py inverts it to
send what a keyboard would; the touch keyboard's compiler (host/actions.py), the demo compiler
(host/play.py) and the test oracle (host/ways.py) read a legend's named key from it; and the pages
get it as hud/keynames.js, written by host/keynames_gen.py, so the board, the strip and the
tablets' keyboards spell keys as the feed does. Standard library only, no I/O.

A named key is one record below: naming another is one line here, then `make keynames`.
"""

# The named keys: (key name, usages, glyph, control character, spellings).
#   key name    the feed's word for it, as Hammerspoon's hs.keycodes.map spells it (the macOS panel
#               shares the table): `delete` is Backspace, `forwarddelete` the key that deletes forward
#   usages      its keyboard-page usages; the first is the one a keyboard reaches for
#   glyph       what the HUD draws for it, on the board and the strip; None: no glyph, the name
#   control     the character a terminal reads for it, if any
#   spellings   how drawings write its legend, glyphs and words, matched lowercased and without
#               spaces: keymap-drawer's parse writes Tab, BSpace, Page Down; drawings by hand ↹, BSPC.
#               A drawing's Delete is ZMK's DEL, the key that deletes forward.
NAMED_KEYS = [
    ("space", (0x2C,), "␣", None, "␣ spc space"),
    ("return", (0x28, 0x58), "↵", "\r", "↵ ⏎ ret enter return"),
    ("escape", (0x29,), "⎋", "\x1b", "⎋ esc escape"),
    ("delete", (0x2A,), "⌫", "\x7f", "⌫ bspc bspace backspace"),
    ("forwarddelete", (0x4C,), "⌦", None, "⌦ del delete"),
    ("tab", (0x2B,), "⇥", "\t", "⇥ ↹ tab"),
    ("left", (0x50,), "←", None, "← left"),
    ("right", (0x4F,), "→", None, "→ right"),
    ("up", (0x52,), "↑", None, "↑ up"),
    ("down", (0x51,), "↓", None, "↓ down"),
    ("home", (0x4A,), "⇱", None, "⇱ home"),
    ("end", (0x4D,), "⇲", None, "⇲ end"),
    ("pageup", (0x4B,), "⇞", None, "⇞ pgup pageup"),
    ("pagedown", (0x4E,), "⇟", None, "⇟ pgdn pagedown"),
    ("insert", (0x49,), "⎀", None, "⎀ ins insert"),
    ("capslock", (0x39,), "⇪", None, "⇪ caps capslock"),
    ("printscreen", (0x46,), None, None, ""),
    ("scrolllock", (0x47,), None, None, ""),
    ("pause", (0x48,), None, None, ""),
    ("menu", (0x65,), None, None, ""),
    # Keyboard Cancel. No Mac key sends it, so Hammerspoon has no name for it, but a cancel combo
    # does (K_CANCEL), and without the keyboard's positions nothing else names it.
    ("cancel", (0x9B,), "cancel", None, "cancel"),
]
for _i in range(24):
    NAMED_KEYS.append((f"f{_i + 1}", (0x3A + _i if _i < 12 else 0x68 + _i - 12,), f"F{_i + 1}", None, f"f{_i + 1}"))

# The modifiers: (flag, the HID modifier byte's bits for it, left and right, glyph), in the order a
# chord is drawn: ⌘⌃⌥⇧.
MODIFIERS = [
    ("cmd", (0x08, 0x80), "⌘"),
    ("ctrl", (0x01, 0x10), "⌃"),
    ("alt", (0x04, 0x40), "⌥"),
    ("shift", (0x02, 0x20), "⇧"),
]

# ---------- what the records say, as the readers look it up ----------

NAMED = {}          # usage -> key name
CONTROL_CHARS = {}  # usage -> the control character it types
GLYPHS = {}         # key name -> glyph
SPELLINGS = {}      # spelling -> key name
for _name, _usages, _glyph, _control, _spelled in NAMED_KEYS:
    for _usage in _usages:
        NAMED[_usage] = _name
        if _control:
            CONTROL_CHARS[_usage] = _control
    if _glyph:
        GLYPHS[_name] = _glyph
    for _s in _spelled.split():
        SPELLINGS[_s] = _name
MOD_BITS = {}       # modifier byte bit -> flag (left and right collapse)
MOD_GLYPHS = {}     # flag -> glyph, in the order a chord is drawn
for _flag, _bits, _glyph in MODIFIERS:
    for _bit in _bits:
        MOD_BITS[_bit] = _flag
    MOD_GLYPHS[_flag] = _glyph
MOD_BITS = dict(sorted(MOD_BITS.items()))


def _spelling(legend):
    return "".join(legend.split()).lower()


def name_of(legend):
    """The named key a legend stands for, however the drawing spells it, or None."""
    return SPELLINGS.get(_spelling(legend)) if isinstance(legend, str) else None


def spellings(name):
    """Every spelling of a named key, as SPELLINGS keeps them."""
    return [s for s, n in SPELLINGS.items() if n == name]


# ---------- the characters ----------

# Keyboard-page usage -> (char, shifted char), US layout.
CHARS = {}
for i, c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    CHARS[0x04 + i] = (c, c.upper())
for i, (c, s) in enumerate(zip("1234567890", "!@#$%^&*()")):
    CHARS[0x1E + i] = (c, s)
CHARS.update({0x2C: (" ", " "), 0x2D: ("-", "_"), 0x2E: ("=", "+"), 0x2F: ("[", "{"), 0x30: ("]", "}"),
              0x31: ("\\", "|"), 0x32: ("#", "~"), 0x33: (";", ":"), 0x34: ("'", '"'), 0x35: ("`", "~"),
              0x36: (",", "<"), 0x37: (".", ">"), 0x38: ("/", "?"), 0x64: ("\\", "|"),
              0x54: ("/", "/"), 0x55: ("*", "*"), 0x56: ("-", "-"), 0x57: ("+", "+"), 0x67: ("=", "=")})
for i, c in enumerate("1234567890"):
    CHARS[0x59 + i] = (c, c)  # keypad 1..9, 0 (0x59..0x62)
CHARS[0x63] = (".", ".")
# macOS Option layer of the US layout: (usage, shifted) -> character. Macros like &kp LS(LA(N2))
# for € land here. Dead keys of that layer (⌥e ⌥` ⌥i ⌥u ⌥n) are left out on purpose.
ALT_CHARS = {
    (0x1E, False): "¡", (0x1E, True): "⁄", (0x1F, False): "™", (0x1F, True): "€", (0x20, False): "£", (0x20, True): "‹",
    (0x21, False): "¢", (0x21, True): "›", (0x22, False): "∞", (0x22, True): "ﬁ", (0x23, False): "§", (0x23, True): "ﬂ",
    (0x24, False): "¶", (0x24, True): "‡", (0x25, False): "•", (0x25, True): "°", (0x26, False): "ª", (0x26, True): "·",
    (0x27, False): "º", (0x27, True): "‚", (0x2D, False): "–", (0x2D, True): "—", (0x2E, False): "≠", (0x2E, True): "±",
    (0x2F, False): "“", (0x2F, True): "”", (0x30, False): "‘", (0x30, True): "’", (0x31, False): "«", (0x31, True): "»",
    (0x33, False): "…", (0x33, True): "Ú", (0x34, False): "æ", (0x34, True): "Æ", (0x36, False): "≤", (0x36, True): "¯",
    (0x37, False): "≥", (0x37, True): "˘", (0x38, False): "÷", (0x38, True): "¿",
    (0x04, False): "å", (0x04, True): "Å", (0x05, False): "∫", (0x05, True): "ı", (0x06, False): "ç", (0x06, True): "Ç",
    (0x07, False): "∂", (0x07, True): "Î", (0x09, False): "ƒ", (0x09, True): "Ï", (0x0A, False): "©", (0x0A, True): "˝",
    (0x0B, False): "˙", (0x0B, True): "Ó", (0x0D, False): "∆", (0x0D, True): "Ô", (0x0E, False): "˚", (0x0E, True): "",
    (0x0F, False): "¬", (0x0F, True): "Ò", (0x10, False): "µ", (0x10, True): "Â", (0x12, False): "ø", (0x12, True): "Ø",
    (0x13, False): "π", (0x13, True): "∏", (0x14, False): "œ", (0x14, True): "Œ", (0x15, False): "®", (0x15, True): "‰",
    (0x16, False): "ß", (0x16, True): "Í", (0x17, False): "†", (0x17, True): "ˇ", (0x19, False): "√", (0x19, True): "◊",
    (0x1A, False): "∑", (0x1A, True): "„", (0x1B, False): "≈", (0x1B, True): "˛", (0x1C, False): "¥", (0x1C, True): "Á",
    (0x1D, False): "Ω", (0x1D, True): "¸",
}


# The right Alt as AltGr, on a Linux host with the US-International layout: what a keymap that
# morphs by OS sends there instead of macOS's Option (rafaelromao/keyboards, the Linux side of its
# OS_MORPHs: ç is &kp RA(COMMA)). Only those, and only with `feed.altgr` on: without it the right Alt
# is Option, as on a Mac.
RALT = 0x40
ALTGR_CHARS = {(0x36, False): "ç", (0x36, True): "Ç", (0x33, True): "°", (0x22, False): "€"}


# Dead keys of the US-International layout, which is how accent macros type on the host:
# the dead key, then the letter, back to back (ZMK macro with wait-ms 0).
DEAD_KEYS = {"`": "̀", "'": "́", "^": "̂", "~": "̃", '"': "̈"}
# US-International special cases that are not the combining mark: ' + c is ç, not ć.
DEAD_KEY_SPECIAL = {("'", "c"): "ç", ("'", "C"): "Ç"}
