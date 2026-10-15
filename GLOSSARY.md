# ZMK Layer HUD

An overlay that draws a ZMK keyboard's keymap as it is typed on: the layers that are up, the keys
and combos struck, and what was typed.

## Language

### Typing

**Keystroke**:
One strike the board draws and counts once: a key, a combo, or a hold-tap that was tapped. A key
that was held typed nothing and is not one.
_Avoid_: stroke, keypress

**Combo**:
Keys struck together within the combo term that type as one, declared on the drawer layers it fires
on. It fires only when one of those layers is the highest one up.
_Avoid_: chord (for the keymap's combo; a chord is any keys struck together)

**Combo pill**:
A combo's legend, drawn beside its keys while it is struck.
_Avoid_: badge, bubble

**Activator**:
A key that brings a layer up: while it is held, or for the next keystroke (a one-shot).
_Avoid_: layer key, trigger

**Inference**:
Placing a typed character on the board from what the keyboard typed, when no fresh key position
stands behind it.
_Avoid_: guessing, emulation

**Typing sent in**:
Typing that did not come from the keyboard itself but from the command line, a socket client or the
landing page. It is drawn by how it would be typed and never counted.
_Avoid_: injected typing, fake keys

**Strip**:
The typed-keys strip below the board: what was typed, as chips.

**Strip label**:
The board's name for a key the feed cannot name, or a combo's legend, shown on the strip.

### Keys

**Named key**:
A key that types no character of its own and is known by its name: Return, Escape, F5, Cancel.
_Avoid_: special key, control key

**Key name**:
The feed's word for a named key. `delete` is Backspace, the key that deletes backwards;
`forwarddelete` is the one that deletes forwards.
_Avoid_: key code, keysym

**Glyph**:
The symbol the HUD draws for a named key or a modifier: ↵ for Return, ⌘ for Command.
_Avoid_: icon, symbol

**Spelling**:
One way a drawing writes a named key's legend, as a glyph or a word: ↵, `ret`, `Enter`. A drawing's
`Delete` is the key that deletes forwards.
_Avoid_: alias

**Key vocabulary**:
Every named key and modifier with its key name, glyph and spellings, and the characters each key
types: one statement of it, which the feed, the pages and the tools all read.
_Avoid_: key table, key map

### Layers

**Drawer layer**:
A layer as the drawing shows it. One drawer layer may stand for several ZMK layers that the drawing
draws alike.
_Avoid_: page layer

**ZMK layer**:
A layer as the firmware numbers it, reported by its id.
_Avoid_: firmware layer, layer id (when the layer itself is meant)

### Panels

**Panel**:
The native window that hosts the HUD page on a desktop: floating where it was put, or docked along
the bottom of a screen as a touch keyboard.
_Avoid_: overlay window, host window

**Page message**:
What the HUD page tells its panel: that it was hidden, how big it is, where its controls are, its
stats, that it wants to be docked or floating, the keys its touch keyboard pressed.
_Avoid_: bridge message, script message

### Keymaps

**Config**:
The user's YAML file: which keyboard, how it is drawn, and the HUD's settings for it.
_Avoid_: settings file

**Definitions**:
What `import` and `sync` take from the keyboard's repo and its drawing, kept beside the config, so
the HUD runs with neither keymap-drawer nor the repo at hand.
_Avoid_: cache, imported file

**Keymap message**:
Everything the page needs to draw a keyboard and type on it: its layers, combos and layout, made
from the config and its definitions.
_Avoid_: keymap JSON
