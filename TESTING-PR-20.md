# Testing PR #20 before it merges

This branch, `test-pr-20`, is [#20](https://github.com/rafaelromao/zmk-layer-hud/pull/20) with this
file and two scripts added. It is not for merging: when #20 moves, it is rebuilt from it.

#20 is the review's stack without the touch keyboard: the work of #6 and #8–#13, one commit each.
The touch keyboard (#1) becomes a separate product, so nothing of it should be here: no `dock`,
`typing` or `remote`, no tablet apps. Each script checks that first.

| Was | What to look at |
|-----|-----------------|
| [#6](https://github.com/rafaelromao/zmk-layer-hud/pull/6) | keystrokes through `hud/keystrokes.js`: keys lit, combo pills, activators, a secret blanking the board; a combo fires on the highest layer up |
| [#8](https://github.com/rafaelromao/zmk-layer-hud/pull/8) | one key vocabulary: a named key drawn as a word lights; the strip's glyphs |
| [#11](https://github.com/rafaelromao/zmk-layer-hud/pull/11) | the Python tools read a keymap message as the page does: `demo`, `make audit` |
| [#12](https://github.com/rafaelromao/zmk-layer-hud/pull/12) | every wired keyboard read through one loop; a lost keyboard lets its modifiers go; BLE gone and stopped at once |
| [#9](https://github.com/rafaelromao/zmk-layer-hud/pull/9) | both panels as one module: show, hide, size, where it was left |
| [#13](https://github.com/rafaelromao/zmk-layer-hud/pull/13) | one reading of the HUD's state for every verb and both icons; `status --json`; a second copy |
| [#10](https://github.com/rafaelromao/zmk-layer-hud/pull/10) | `sync --watch` and `doctor` see a drawing's layout file; a missing `--config` is an error |

## On macOS

In a clone:

```bash
git fetch && git switch test-pr-20 && ./test-pr-20-macos.sh
```

## On Omarchy

```bash
zmk-layer-hud update --ref test-pr-20
```

Then from the installed tree:

```bash
cd "$(dirname "$(dirname "$(readlink -f "$(command -v zmk-layer-hud)")")")" && ./test-pr-20-omarchy.sh
```

A clone works too, as on macOS. Afterwards, `zmk-layer-hud update --ref latest` goes back to the release.

## What the scripts do

Each runs what a script can check by itself, and asks you about the rest, one stage at a time:

- **Setup:**
  - nothing of the touch keyboard is in the tree;
  - the test suites;
  - the branch's HUD in place of the installed one.
- **The panel and the CLI:**
  - the CLI verbs against `status --json`;
  - show, hide, Ctrl+Alt+L and the page's own buttons;
  - size and place;
  - a second copy of the HUD;
  - the icon: the menubar on macOS, the bar on Omarchy.
- **Typing:**
  - keys and combos on the board;
  - the keyboard over USB, then over BLE;
  - the keymap tools.
- **Omarchy only:** Hyprland's layer surface and WebKitGTK's drawing.

Each answer goes to `run/test-pr-20-<os>/results.txt`: pass, fail with what happened, or skip. The
logs go beside it. Ctrl-C stops a script, and a run again offers the answers already given. At the end
it puts things back: the branch's HUD and icon stop, and the installed copy's start again if they
were running.

To share a run on the PR:

```bash
gh pr comment 20 --body-file run/test-pr-20-<os>/results.txt
```
