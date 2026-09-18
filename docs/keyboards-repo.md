# Integrating with the author's keyboards repo

The generic guide is [zmk-setup.md](zmk-setup.md). This page is the same three changes applied to
`~/projects/keyboards` (rafaelromao/keyboards), whose build script manages modules itself instead
of `west.yml`. Then build and flash the central (or dongle). Peripherals need nothing.

## 1. Add the module

`scripts/build.sh` adds any module listed in `DEF_MODULES` as a git submodule under `modules/`
and passes it to `west build` through `ZMK_EXTRA_MODULES`, so one line is enough
(`scripts/build.sh:17`):

```diff
-DEF_MODULES=(urob/zmk-leader-key,urob/zmk-auto-layer,urob/zmk-adaptive-key,rafaelromao/zmk-layer-morph,rafaelromao/zmk-vim-mode)
+DEF_MODULES=(urob/zmk-leader-key,urob/zmk-auto-layer,urob/zmk-adaptive-key,rafaelromao/zmk-layer-morph,rafaelromao/zmk-vim-mode,rafaelromao/zmk-layer-hud)
```

Until the repo is on GitHub, point the submodule at the local clone instead:

```bash
cd ~/projects/keyboards
git submodule add ~/projects/zmk-layer-hud modules/rafaelromao/zmk-layer-hud
```

## 2. Add the node in `src/features/hud.dtsi`

A general-purpose feature file (the signal is not vim-related), included from
`src/definitions/includes.h` after `vim.dtsi`:

```c
/ {
    layer_signal {
        compatible = "zmk,layer-signal";
        heartbeat-ms = <2000>;   /* a HUD started mid-session converges within 2 s */
        positions;               /* announce key positions: exact highlighting */
        /* default: settle-ms 3 */
    };
};
```

```c
#include "../features/hud.dtsi"
```

What it does: on every layer change (any mechanism: `&mo`, `&lt`, `&sl`, `&tog`, the auto-layers,
`vim_sync` applying a host code), the module sends the active-layer bitmap as a small framed
message on its own channel. With `positions;` each key press and release goes out the same way,
and `CONFIG_ZMK_LAYER_SIGNAL_KEYS` (default y) adds a snapshot of the keyboard report so the HUD
can show what you type. `hid_indicator_code_listener` (zmk-vim-mode) and this module do not
interact: one listens to LED reports, the other to layer changes.

Earlier versions smuggled all this through the keyboard report as the reserved keyboard-page
usages 0xA5–0xDF, on the premise that no OS maps them. Linux does — `hid_keyboard[]` fills every
unmapped slot with `KEY_UNKNOWN` rather than zero — so each layer change reached the compositor as
a phantom key press carrying the held modifiers, and Gui + a layer change switched workspace. That
is why `CONFIG_ZMK_HID_KEYBOARD_REPORT_SIZE=12` is gone from the `.conf` files: nothing needs the
extra slots any more, and the module no longer requires the HKRO report type either.

## 3. Give the signal its carrier

Over USB the channel is a CDC-ACM interface, which the module's snippet adds:

```
-n layer-hud-usb-uart
```

The snippet puts a `cdc-acm-uart` under `&zephyr_udc0` and points the `zmk,layer-hud-uart` chosen
node at it; `CONFIG_ZMK_LAYER_SIGNAL_UART` then defaults on. Over BLE the module defines its own
GATT service and needs no snippet, only `CONFIG_BT_PERIPHERAL` (any wireless ZMK build).

A board can already have a CDC-ACM interface for USB logging or ZMK Studio. They are
indistinguishable from their descriptors, so `hudfeed.py` tries each port in turn and keeps the
one that produces valid frames.

## 4. Build and flash

```bash
cd ~/projects/keyboards
./init.sh
# inside the container:
b rommana cl -n layer-hud-usb-uart      # central left
b rommana cd -n layer-hud-usb-uart      # dongle, if used
```

## 5. Verify without the HUD

With the keyboard connected, on the machine that will run the HUD:

```bash
python3 ~/projects/zmk-layer-hud/host/hudfeed.py --stdout --no-keys --no-ws --debug
```

Within 2 s (heartbeat) it prints the current set, e.g. `{"kind":"layers","ids":[]}`. Hold the
numbers thumb → `[14]`; release → `[]`; `zmk-vim-mode set normal` → `[2]`; a held shortcut layer
over vim → `[2,10]`. Type into a terminal and a text field meanwhile: nothing stray appears.

macOS: nothing to grant. The serial port needs no Input Monitoring, and Karabiner-Elements cannot
seize it the way it could seize the HID device.

Linux: tty access comes from `contrib/udev/60-zmk-layer-hud.rules`, or from the `dialout` group.
`wev` and `libinput debug-events` should stay silent through a layer change — a `KEY_UNKNOWN` (240)
there means the firmware predates this channel and is still sending the signal as HID usages.

## Layer ids

`hud/keymap/build.py` reads the `// Layers` block of `src/definitions/config.dtsi` and maps every
define to the drawer layer that shows it (`ZMK_LAYERS` in that script). Adding a layer to
`config.dtsi` fails the build of `keymap.json` until it gets an entry there, on purpose. Ids must
stay below 31 (30 with the default usages); the module's `BUILD_ASSERT` enforces it.
