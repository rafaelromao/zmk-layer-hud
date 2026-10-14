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

### Layers

**Drawer layer**:
A layer as the drawing shows it. One drawer layer may stand for several ZMK layers that the drawing
draws alike.
_Avoid_: page layer

**ZMK layer**:
A layer as the firmware numbers it, reported by its id.
_Avoid_: firmware layer, layer id (when the layer itself is meant)
