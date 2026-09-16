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
| `positions` | off | announce key positions too (two extra reports per press); needs `base-usage` ≥ 0xC0 |
| `heartbeat-ms` | 0 (off) | re-send the current set while idle, so a HUD started mid-session converges; 2000 is good |
| `tap-ms` | 10 | how long the usages stay in the report before release |
| `settle-ms` | 3 | coalesce a burst of layer changes into one announcement |
| `base-usage` | 0xC0 | usage for layer id 0; layer L is sent as base + L |
| `commit-usage` | 0xDF | usage marking the report that carries the full set |

With the defaults, layer ids 1–30 are representable (layer 0 is always active and never sent).
If your keymap has more layers, lower `base-usage` (0xA5 is the lowest reserved usage, and
0xA5–0xBF carry the positions when `positions;` is set) and keep the host config's `signal:` in
sync. Positions 0–135 are representable; a key's position is its index in the keymap's binding
list, which is also the drawer's key order for a YAML from `keymap parse`. A curated drawer file
with a different key order lists each drawer key's position in the host config (`positions:`).

## 3. Make room in the keyboard report

In the `.conf` of the central/dongle:

```
CONFIG_ZMK_HID_KEYBOARD_REPORT_SIZE=12
```

The default report holds 6 keys. An announcement takes one slot per active layer plus one for
the commit usage, on top of whatever real keys are held, and a burst that does not fit is
skipped rather than sent truncated. 12 is the ceiling: the USB report becomes 15 bytes, which is
what Zephyr's default 16-byte HID interrupt endpoint carries. Hosts in boot protocol (BIOS) still
get the 6-key boot report.

The module needs the default HKRO report type. With `CONFIG_ZMK_HID_REPORT_TYPE_NKRO=y` it does
not build, because the NKRO bitmap stops at usage 0x67.

## 4. Build and flash

```bash
west build -s zmk/app -b <board> -- -DSHIELD=<shield> -DZMK_CONFIG=/path/to/config
```

or push and let the GitHub Actions workflow build it. Flash the central (or dongle) `.uf2`.

## 5. Verify from the host

With the keyboard connected and the HUD's Python environment set up (`make venv` in
zmk-layer-hud, see the README):

```bash
.venv/bin/python3 host/hudfeed.py --stdout --no-ws --no-keymap --no-keys --debug
```

Within two seconds (heartbeat) it prints `{"kind":"layers","ids":[]}`; holding a layer key
prints that layer's id, releasing prints `[]` again. Drop `--no-keys` to also see every key press
as `{"kind":"key",…}`. Type into a terminal and a text field meanwhile: nothing stray appears. If it prints `cannot open`, see the README's troubleshooting;
if it prints nothing, add `--raw` to see the raw reports (15 bytes with 12 slots; a `df` byte
marks an announcement).

## Why reserved usages

The HID Usage Tables reserve keyboard-page usages 0xA5–0xDF. No operating system maps them to a
key: macOS produces no key event for them, Linux delivers them to evdev as `KEY_UNKNOWN` with no
keysym, Windows ignores them. The two exceptions are 0xB6 and 0xB7, which Linux maps to Keypad
`(` and `)`; the module never uses them. Linux still sees `KEY_UNKNOWN` events, which is why the
heartbeat pauses while a key is held: a compositor stops auto-repeating a key when any other key
event arrives. So a report that carries them reaches raw-HID readers and nothing
else, over USB and BLE alike, without drivers. The module writes them straight into the
keyboard report (not as key events on ZMK's bus), so behaviours that watch key presses, such as
auto-layer, adaptive keys, caps word and sticky keys, never notice.
