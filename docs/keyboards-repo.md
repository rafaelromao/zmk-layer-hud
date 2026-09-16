# Integrating with the keyboards repo

Exact changes for `~/projects/keyboards` (rafaelromao/keyboards). Three edits, then build and
flash the central (or dongle). Peripherals need nothing.

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

## 2. Add the node to `src/features/vim.dtsi`

Next to `vim_sync`. It is not vim-specific, but that file is already the module integration
point; a separate `hud.dtsi` would also need a line in `src/definitions/includes.h`.

```c
/ {
    layer_signal {
        compatible = "zmk,layer-signal";
        heartbeat-ms = <2000>;   /* a HUD started mid-session converges within 2 s */
        /* defaults: base-usage 0xC0, commit-usage 0xDF, tap-ms 10, settle-ms 3 */
    };
};
```

What it does: on every layer change (any mechanism: `&mo`, `&lt`, `&sl`, `&tog`, the auto-layers,
`vim_sync` applying a host code), the module writes usage `0xC0 + layer id` for each active layer
plus `0xDF` into the keyboard HID report, sends it, and releases them 10 ms later. The usages are
reserved in the HID spec: macOS produces no key event for them and Linux gives them no keysym,
so applications never see them; `host/hudfeed.py` decodes them from the raw report. Layer 0 is
never sent. `hid_indicator_code_listener` (zmk-vim-mode) and this module do not interact: one
listens to LED reports, the other to layer changes.

## 3. Make room in the keyboard report

The HKRO report holds `CONFIG_ZMK_HID_KEYBOARD_REPORT_SIZE` keys (default 6). The announcement
needs one slot per active layer plus one for the commit usage, on top of whatever real keys are
held. VIM_NORMAL + VIM_VISUAL + NAV_CP + commit + two held keys already fill six, and a burst
that does not fit is skipped (logged at debug level), so raise it on every central/dongle `.conf`
that carries `CONFIG_ZMK_HID_INDICATORS=y`:

```
CONFIG_ZMK_HID_KEYBOARD_REPORT_SIZE=12
```

e.g. `src/keyboards/rafaelromao/diamond/boards/shields/diamond/diamond_central_left.conf` and
`diamond_central_dongle.conf`. 12 is the ceiling: the USB report becomes 15 bytes, which is the
most Zephyr's default 16-byte HID interrupt endpoint carries; BLE sends 14 bytes, under the
20-byte ATT payload. Boot-protocol hosts (BIOS) still get the 6-key boot report.

The module needs the HKRO report type (the default); with `CONFIG_ZMK_HID_REPORT_TYPE_NKRO=y`
it does not build, because the NKRO bitmap stops at usage 0x67.

## 4. Build and flash

```bash
cd ~/projects/keyboards
./init.sh
# inside the container:
b rommana cl      # central left
b rommana cd      # dongle, if used
```

## 5. Verify without the HUD

With the keyboard connected, on the machine that will run the HUD:

```bash
python3 ~/projects/zmk-layer-hud/host/hudfeed.py --stdout --layers-only --no-ws --debug
```

Within 2 s (heartbeat) it prints the current set, e.g. `{"kind":"layers","ids":[]}`. Hold the
numbers thumb → `[14]`; release → `[]`; `zmk-vim-mode set normal` → `[2]`; a held shortcut layer
over vim → `[2,10]`. Type into a terminal and a text field meanwhile: nothing stray appears.

macOS: the first run asks for Input Monitoring for the terminal app running Python (the
Hammerspoon host inherits Hammerspoon's grant instead). If nothing prints, check
Karabiner-Elements → Devices: a keyboard whose events Karabiner modifies is seized by it and a
raw-HID reader gets no reports; untick the Diamond there.

Linux: hidraw access comes from `contrib/udev/60-zmk-layer-hud.rules` (the zmk-vim-mode daemon
installs an identical rule). `libinput debug-events` shows the usages as `KEY_UNKNOWN`; that is
expected and harmless.

## Layer ids

`hud/keymap/build.py` reads the `// Layers` block of `src/definitions/config.dtsi` and maps every
define to the drawer layer that shows it (`ZMK_LAYERS` in that script). Adding a layer to
`config.dtsi` fails the build of `keymap.json` until it gets an entry there, on purpose. Ids must
stay below 31 (30 with the default usages); the module's `BUILD_ASSERT` enforces it.
