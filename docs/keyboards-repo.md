# Worked example: the author's keyboards repo

The generic guide is [zmk-setup.md](zmk-setup.md). This page is the same three changes applied to
[rafaelromao/keyboards](https://github.com/rafaelromao/keyboards), the author's ZMK config, whose
build script manages modules itself instead of `west.yml`: a reference for configs built that way,
not something zmk-layer-hud needs. Then build and flash the central (or dongle). Peripherals need
nothing.

## 1. Add the module

`scripts/build.sh` adds any module listed in `DEF_MODULES` as a git submodule under `modules/`
and passes it to `west build` through `ZMK_EXTRA_MODULES`, so one line is enough
(`scripts/build.sh:17`):

```diff
-DEF_MODULES=(urob/zmk-leader-key,urob/zmk-auto-layer,urob/zmk-adaptive-key,rafaelromao/zmk-layer-morph,rafaelromao/zmk-vim-mode)
+DEF_MODULES=(urob/zmk-leader-key,urob/zmk-auto-layer,urob/zmk-adaptive-key,rafaelromao/zmk-layer-morph,rafaelromao/zmk-vim-mode,rafaelromao/zmk-layer-hud)
```

To build against a local clone of this repo instead of the published one, add the submodule by
path:

```bash
cd keyboards   # a checkout of rafaelromao/keyboards
git submodule add /path/to/zmk-layer-hud modules/rafaelromao/zmk-layer-hud
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
message on its own channel. With `positions;` each key press and release goes out the same way.

What you type is not sent here. The HUD host reads the keyboard's HID reports for that: ZMK emits
one per change, so none can be lost.

Nothing travels in the keyboard report, so the module needs neither the HKRO report type nor extra
report slots — no `CONFIG_ZMK_HID_KEYBOARD_REPORT_SIZE` in the `.conf` files.
[Why a channel of its own](zmk-setup.md#why-a-channel-of-its-own) explains the reasoning.

`hid_indicator_code_listener` (zmk-vim-mode) and this module do not interact: one listens to LED
reports, the other to layer changes.

## 3. Give the signal its carrier

Over USB the channel is a CDC-ACM interface, which the module's snippet adds:

```
-n layer-hud-usb-uart
```

The snippet puts a `cdc-acm-uart` under `&zephyr_udc0` and points the `zmk,layer-hud-uart` chosen
node at it; `CONFIG_ZMK_LAYER_SIGNAL_UART` then defaults on. Over BLE the module defines its own
GATT service and needs no snippet, only `CONFIG_BT_PERIPHERAL` (any wireless ZMK build).

A board can already have a CDC-ACM interface for USB logging or ZMK Studio. They are
indistinguishable from their descriptors, so `host/hudfeed.py` tries each port in turn and keeps the
one that produces valid frames.

## 4. Build and flash

```bash
cd keyboards
./init.sh
# inside the container:
b rommana cl -n layer-hud-usb-uart      # central left
b rommana cd -n layer-hud-usb-uart      # dongle, if used
```

## 5. Verify without the HUD

With the keyboard connected, on the machine that will run the HUD:

```bash
zmk-layer-hud feed --stdout --no-keys --no-ws --debug
```

Within 2 s (heartbeat) it prints the current set, e.g. `{"kind":"layers","ids":[]}`. Hold the
numbers thumb → `[14]`; release → `[]`; `zmk-vim-mode set normal` → `[2]`; a held shortcut layer
over vim → `[2,10]`. Type into a terminal and a text field meanwhile: nothing stray appears.

macOS: nothing to grant. The serial port needs no Input Monitoring, and Karabiner-Elements cannot
seize it the way it could seize the HID device.

Linux: tty access comes from `contrib/udev/60-zmk-layer-hud.rules`, or from the `dialout` group.
`wev` and `libinput debug-events` should stay silent through a layer change — a `KEY_UNKNOWN` (240)
there means something is sending the signal as HID usages rather than on this channel.

## Layer ids

`hud/keymap/build.py` reads the `// Layers` block of `src/definitions/config.dtsi` and maps every
define to the drawer layer that shows it (`ZMK_LAYERS` in that script). Adding a layer to
`config.dtsi` fails the build of `keymap.json` until it gets an entry there, on purpose. Ids must
be 0–31; the module's `BUILD_ASSERT` enforces it.
