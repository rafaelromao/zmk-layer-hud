# Adding the layer signal to your ZMK firmware

Three changes to a ZMK config repo, then build and flash the side that talks to the host (the
central half, or the dongle). Peripheral halves need nothing.

## 1. Add the module

`config/west.yml` of a standard zmk-config repo (the one built by GitHub Actions or a local
`west build`):

```yaml
manifest:
  remotes:
    - name: zmkfirmware
      url-base: https://github.com/zmkfirmware
    - name: rafaelromao
      url-base: https://github.com/rafaelromao
  projects:
    - name: zmk
      remote: zmkfirmware
      revision: main
      import: app/west.yml
    - name: zmk-layer-hud
      remote: rafaelromao
      revision: main
  self:
    path: config
```

Any other way of passing a Zephyr module works too: `-DZMK_EXTRA_MODULES=/path/to/zmk-layer-hud`
on the `west build` command line, or a git submodule your build script hands to that flag. The
module is the repo itself: `zephyr/module.yml` at its root points cmake, Kconfig and the
devicetree bindings at `firmware/`.

## 2. Add the node to your keymap

Anywhere at the root of your `.keymap` (or a `.dtsi` it includes):

```c
/ {
    layer_signal {
        compatible = "zmk,layer-signal";
        heartbeat-ms = <2000>;
        positions;
    };
};
```

That is the whole integration: the module listens to ZMK's layer-state events, so every way of
switching layers (`&mo`, `&lt`, `&sl`, `&to`, `&tog`, conditional layers, auto-layers, other
modules) is reported. With `positions;` it also announces the physical position of every key
press, so the HUD lights the exact key whatever it produced (a chord, a combo, a macro, a
modifier or layer key). No binding changes.

Properties (all optional):

| property | default | meaning |
|---|---|---|
| `positions` | off | announce key positions too (one extra message per press and release) |
| `heartbeat-ms` | 0 (off) | re-send the current set while idle, so a HUD started mid-session converges; 2000 is good |
| `settle-ms` | 3 | coalesce a burst of layer changes into one announcement |

Layer ids 0–31 are representable: the set travels as a bitmap, so there is no per-layer cost and
no ceiling to raise. Positions 0–255 are representable; a key's position is its index in the
keymap's binding list, which is also the drawer's key order for a YAML from `keymap parse`. A
curated drawer file with a different key order lists each drawer key's position in the host config
(`positions:`).

`CONFIG_ZMK_LAYER_SIGNAL_GATT` carries the signal over BLE and defaults on.

The module does not send what you type. It could once, and it lost keystrokes doing it: the
keyboard report can only be read after ZMK has updated it, so the read was deferred to a work item
that coalesces, and a key pressed and released between two runs was never reported held. The HUD
host reads the HID reports instead — ZMK emits one per change, so nothing can be lost — at the cost
of Input Monitoring on macOS.

## 3. Give the signal its carrier

Over USB the signal goes out on a CDC-ACM interface. The module ships a snippet that creates one:

```
west build ... -S layer-hud-usb-uart
```

It adds a `cdc-acm-uart` under `&zephyr_udc0` and sets the `zmk,layer-hud-uart` chosen node, which
is what turns `CONFIG_ZMK_LAYER_SIGNAL_UART` on. Point that chosen node at a UART yourself if you
would rather use a physical one.

Over BLE the module defines its own GATT service and needs nothing but `CONFIG_BT_PERIPHERAL`.
Subscribing requires encryption, so the signal only flows to a host the keyboard is bonded with.

## 4. Build and flash

```bash
west build -s zmk/app -b <board> -S layer-hud-usb-uart -- -DSHIELD=<shield> -DZMK_CONFIG=/path/to/config
```

or push and let the GitHub Actions workflow build it. Flash the central (or dongle) `.uf2`.

## 5. Verify from the host

With the keyboard connected and the host set up (`zmk-layer-hud setup`, see the README):

```bash
zmk-layer-hud feed --stdout --no-ws --no-keymap --no-keys --debug
```

Within two seconds (heartbeat) it prints `{"kind":"layers","ids":[]}`; holding a layer key
prints that layer's id, releasing prints `[]` again. Drop `--no-keys` to also see every key press
as `{"kind":"key",…}`. Type into a terminal and a text field meanwhile: nothing stray appears. If
it prints `cannot open`, see the README's troubleshooting; if it prints nothing, add `--raw` to log
every frame that decodes, which separates a silent keyboard from a host that cannot read it.

## Why a channel of its own

Because the obvious alternative does not work, and the way it fails is quiet.

The HID Usage Tables reserve keyboard-page usages 0xA5–0xDF, and earlier versions of this module
put the signal there: the report reaches a raw-HID reader, and no OS was supposed to map the
usages to anything. Linux maps them. The kernel's `hid_keyboard[]` table fills every unassigned
slot with `KEY_UNKNOWN` (240) rather than with zero, and `hidinput` only drops a usage that maps to
zero — so each of those reports arrived as a real key event, carrying whatever modifiers were held
at the time. Holding Gui and touching a layer was enough to make a Wayland compositor act on it and
change workspace. `wev` and `libinput debug-events` show it plainly once you know to look, and
nothing shows it at all if you do not: applications receive a keycode with no keysym and mostly
ignore it.

A second charge was laid against writing into the report, and it turned out to be false, which is
worth recording because it was believed for a while and acted on twice: combos stopped firing at
about the same time, and the blocking sends were blamed. The cause was in the keymap — eight combos
listed the base layer without the alt-OS layer beside it, and OS detection had just started raising
that layer by itself, so whether a combo worked depended on which host was plugged in. Removing
`positions;` appeared to fix it only because the layer happened to differ across those flashes.

So the case for a transport of its own rests on the phantom key events alone, which is enough. A
frame is a frame there and nothing can be read as a key: a CDC-ACM interface over USB,
notifications on the module's own GATT service over BLE. Both drop rather than block, so the module
cannot delay a keystroke however slow the host is — a property worth keeping on its own merits,
whatever it was once thought to have fixed. The
listener still works off ZMK's layer-state events, so every way of switching layers is reported and
behaviours that watch key presses — auto-layer, adaptive keys, caps word, sticky keys — never see
anything.
