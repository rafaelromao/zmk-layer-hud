# Security review

A review of the whole tree on 2026-10-02: the firmware module, the Python host, the pages and the
installer. Every finding below is either fixed, in the commit named, or stated as a limit of the
design, so that anyone running the HUD knows what it protects and what it cannot. The threat that
matters most here is simple: the feed carries what is typed on the keyboard, as text, and a
keyboard HUD is the kind of thing a password gets typed in front of.

## What was looked at, and how

- Every tracked source file, read for the things a reviewer looks for: what listens on the
  network and who may connect, what is run as a subprocess and with what, what is parsed from
  the keyboard or a file or the network and whether its size is bounded, what is written to disk
  and with what permissions, what runs as root, and what ends up as HTML in a page.
- `shellcheck -S style` on `install.sh`, `bin/zmk-layer-hud`, `host/linux/hud.sh` and
  `host/macos/start.sh`: clean, before and after.
- `bandit -r host site` (excluding the tests): 0 high, 5 medium, 64 low. The lows are its
  notes that `subprocess` is used at all; every call passes an argument list and no shell. The
  mediums were the tarball extraction and the `urlopen` calls below, both addressed, and two
  `chmod 0755` on the command's own shim, which is an executable and keeps its bit on purpose.
- `pip-audit` on the virtualenv's 23 packages, and the same list against the OSV database: no
  known vulnerabilities on the day.
- Git history, for secrets and for files that were later removed: none. There are no keys,
  tokens or `.env` files in the tree or its history, and the only absolute home path is a test
  fixture's `/home/me`.

Not run: semgrep, gitleaks, trufflehog, cppcheck. None was installed, and the manual read
covered what they look for in a tree this size.

## Findings

| # | Severity | What | Where | Status |
|---|---|---|---|---|
| 1 | High | The WebSocket on 127.0.0.1:8766 took any client: no token, no Origin check. A browser applies no same-origin rule to WebSockets, so any web page open on the machine, or any other local user, could read every keystroke as it was typed, kill the HUD with `{"kind":"close"}`, or draw on it. On Linux there is no Secure Input, so passwords went too. | `host/hudfeed.py` Hub, HubThread, main; `host/cli.py` demo | Fixed in `aa7f2a9`: a per-run token in the URL path, checked before the upgrade (HTTP 403), with an Origin allowlist (none, `file://`, `null`, loopback hosts) as a second gate. Kept in `$STATE/token`, 0600. `poke` reads it; the demo prints its own. Tested live and in the suite. |
| 2 | Medium | The Linux panel always started the feed with `--debug`, which logged every key press with a millisecond timestamp to a 0644 file. With the public keymap that is the typing. | `host/linux/panel.py`, `host/hudfeed.py` Hub.send | Fixed in `aa7f2a9`: `--debug` only under `ZMKHUD_DEBUG=1`, the log is 0600, both panels' debug logs keep layer messages only, the launchers run under `umask 077`. |
| 3 | Medium | The published HUD page fetched whatever `?keymap=`, `?script=`, `?timeline=` named and connected to any `?ws=`; the keymap's glyph SVGs go into the page as markup. A link could make `rafaelromao.github.io` run a stranger's script. | `hud/hud.js` | Fixed in `caeb428`: those parameters are honoured only from `file:` or a loopback host, which is where the hosts, the demo and development load the page. The landing page never used them. |
| 4 | Medium | Glyph SVGs came from icon repositories on moving branches, from a drawer config's own `glyph_urls` (any URL scheme), and from keymap-drawer's cache, and were put into the panels' pages, which see every keystroke, without a look. | `host/keymap.py` resolve_glyphs, build_message; `hud/hud.js` legendHTML | Fixed in `caeb428`: one allowlist on both sides (the elements a drawing is made of, no `on*` or `style`, no `href` outside the glyph, no doctype or entity), applied to inline, cached and fetched glyphs and again when the message is built. A refused glyph shows its text spelling and is not cached. Only `https` sources are fetched. |
| 5 | Medium | `install.sh` ran as far as a cut-short download got, and `rm -rf`'d `$ZMKHUD_HOME`, `.old` and `.new` with no check that they were ours. | `install.sh` | Fixed in `63456b6`: one `main` function called last; nothing removed or moved unless it has the command in it; staging under a `mktemp` name; the trap quoted. |
| 6 | Medium | With `positions;` on, the keyboard emits every key position on its serial port. On macOS `/dev/cu.*` is 0666, and the feed opened it shared, so any local process could read the stream (the firmware knows nothing of Secure Input). | `host/hudfeed.py` SerialReader | Mitigated in `fdf29bf`: once a port has produced a frame, the feed takes it with `TIOCEXCL`; later opens get EBUSY until the feed closes it. A composite board's other CDC ports are left alone. The window before the first frame remains, as does a process that already held the port. |
| 7 | Medium | The udev rule granted the seat user the keyboard's evdev node, which this HUD never reads and which bypasses the compositor's input isolation for every stock ZMK board. | `contrib/udev/60-zmk-layer-hud.rules` | Fixed in `fdf29bf`: that line is an opt-in comment for zmk-vim-mode's users. hidraw and the tty keep `uaccess`. |
| 8 | Medium | `update` extracted a branch tarball with its own checks for `..` and `/`, but not for symlink or hardlink members, and without the `data` filter. | `host/cli.py` fetch_tree | Fixed in `63456b6`: links and devices refused, `filter="data"` where the interpreter has it. The download itself is unsigned; see the limits below. |
| 9 | Low | `git clone` without `--` before a URL that may have come from a definitions file. | `host/sync.py` | Fixed in `63456b6`. |
| 10 | Low | The virtualenv's packages were unpinned, and `update` ran `pip install --upgrade` on every run. | `host/cli.py` | Fixed in `63456b6`: `host/requirements.txt`, pinned, installed by setup and update. Transitive packages stay unpinned. |
| 11 | Low | State files (`panel.json`, `panel.want`, `menubar.json`, the panel's position) were written through a predictable `<file>.<pid>.tmp` name with the default umask. | `host/panelstate.py`, `host/shortcuts.py`, `host/linux/panel.py` | Fixed in `fdf29bf` for panelstate and the panel; the shortcuts writer and the Hyprland backup are in the working tree, pending another change to that file. |
| 12 | Low | Edits to the user's `hyprland.conf` kept no copy of it. | `host/shortcuts.py` | Done in the working tree: the first edit keeps `hyprland.conf.bak-zmk-layer-hud`, never written over. |
| 13 | Low | The tally token was compared with `==`. | `host/hudfeed.py` | Fixed in `aa7f2a9`: `hmac.compare_digest`. Hygiene; the token is 128 random bits. |
| 14 | Low | The demo socket took messages of any size. | `host/cli.py` | Fixed in `aa7f2a9`: websockets' default limit. |
| 15 | Low | `.gitignore` did not name `.env` or `*.log`. | `.gitignore` | Fixed in `fdf29bf`. |
| 16 | Low | Ten commits on the public `main` from 2026-09-18 and -19 carry a work e-mail address as author, where the rest carry a personal one. | git history | Not changed: rewriting shared history takes a force-push, which is the author's call. A per-repository `git config user.email` keeps it from happening again. |
| 17 | Info | `pgrep -f`/`pkill -f` find processes by command line; the Omarchy launcher's `pkill -f -- "--class=zmkhud"` is broad. | `host/cli.py`, `host/linux/hud.sh` | Noted. Signals reach only the user's own processes, and `panel.json`'s pid is checked against `pgrep`. |
| 18 | Info | The firmware's UART free-space check and write are not one operation. | `firmware/src/signal_uart.c` | Noted. Every caller runs on ZMK's system work queue thread, so there is no second writer; a frame goes whole or not at all. |

## What was looked for and found sound

- Every listener binds 127.0.0.1 only. No server serves files from disk except the demo's
  page server, which serves `hud/` and nothing else.
- The firmware parses no input: UART receive is disabled, the GATT characteristic is notify-only
  with no writable attribute, and nothing reaches the keyboard from the host. There is no
  `memcpy`, `strcpy` or `sprintf` in the module; the frame encoder checks its lengths; positions
  above 255 are dropped, not truncated. The signal carries a layer bitmap and, if enabled, key
  positions, never HID usages or characters. The CCC requires an encrypted link.
- The host's frame decoder bounds the payload, caps its buffer at 4 KiB and checks the CRC;
  hidraw reads are fixed at 64 bytes.
- Session files hold counts only, never text, order or timing of individual keys: presses per
  layer and position, combos, latency sums, totals, per-day totals. They live in a 0700
  directory as 0600 files, written through `mkstemp`, `fsync` and `os.replace`. Session names,
  preferences and tally batches from the page are validated and bounded. Typed text is never
  reconstructed or persisted anywhere; the strip keeps at most five chips in memory.
- No `shell=True`, `os.system`, `eval`, `exec` or `pickle` anywhere. Every subprocess takes an
  argument list. YAML is read with `safe_load`. `sudo` runs only for the Arch packages, the udev
  rule and `udevadm`, each command printed and confirmed on a TTY first; `--no-sudo` prints them
  and stops.
- The pages use `textContent` for every legend, device name and chip; the one `innerHTML` with
  content is the glyph (finding 4). No `eval`, `document.write`, `insertAdjacentHTML`, no
  `window.postMessage` or `message` listener (the page talks to its panel through a native
  bridge), no external script, font or CDN. The landing page builder strips the home path from
  the published boards and copies only same-origin files; the published copies match their
  sources.
- The macOS launcher is not setuid and spawns an absolute path; the LaunchAgent and the XDG
  autostart entry use argument vectors; the Omarchy plugin runs no commands and renders text as
  plain text; `setup --link` writes one symlink and edits no shell file.

## Limits that remain, by design

- **`curl ... | sh` and `update` run what the branch holds.** There is no signature or checksum,
  and a tarball from `codeload.github.com` is trusted as GitHub serves it. The installer now says
  so at its top and runs nothing when cut short, but whoever controls the repository or the
  branch controls what runs. Clone and `make install` if that matters; `git` then verifies what
  it fetches.
- **The macOS login launcher lends its grants.** The app that logs in is the process macOS holds
  responsible for Input Monitoring and Bluetooth, and it runs the tree's command under the tree's
  virtualenv. Anything that can write `~/.local/share/zmk-layer-hud`, its `.venv` or the
  LaunchAgent plist runs with those grants. That is how such grants work on macOS, and the same
  is true of any app the user installs; it is said here so that the tree's permissions are
  understood as part of the HUD's.
- **The serial port on macOS is world-readable until the feed takes it**, and a process that
  held it first keeps it. With `positions;` on, that process sees the key positions. Leave
  `positions;` off if the HUD's heatmap and strip are not wanted.
- **Bluetooth: encryption, not authentication.** ZMK pairs with Just Works, so the GATT link is
  encrypted but a capture of the pairing can be decrypted, and any application on the bonded
  host with Bluetooth access may subscribe. The same is true of the keyboard's HID reports over
  the same link.
- **Linux has no Secure Input.** What is typed into a password field reaches the feed and its
  page there. The README says so; stop or hide the HUD when it matters.
- **The Origin gate trusts `null`.** A `file:` page and a sandboxed frame both say `null`, which
  is why the token, not the Origin, is what admits a client.

## Running the checks again

```bash
make test
shellcheck -S style install.sh bin/zmk-layer-hud host/linux/hud.sh host/macos/start.sh
.venv/bin/pip freeze | pip-audit -r /dev/stdin --no-deps
bandit -q -r host site -x '*_test.py' -ll
```

The suite covers the gate (`hudfeed_test.Gate`, `Socket`), the token file (`panelstate_test.Token`),
`poke`'s URL (`hudpoke_test.Url`), the glyph allowlist on both sides (`keymap_test.Glyphs`,
`hud_test.js` `checkGlyphs`), the page's local-only parameters (`checkLocalOnly`), the tarball
(`cli_test.Update`), the port (`hudfeed_test.PortTaken`) and the Hyprland backup. The three
`Socket` tests open a real port and skip where the process may not; run `make test-host` from a
terminal to see them.
