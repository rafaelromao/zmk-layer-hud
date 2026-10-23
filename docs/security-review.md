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

## Review of 2026-10-13: a password in front of the HUD

A second pass over the tree, with one question: where can a password typed on the keyboard end
up somewhere the user did not intend? The feed reads the keyboard's own HID reports, so it is a
keylogger by construction, and what holds it back is the secure-input gate. This pass traced the
gate end to end -- the probes, what they gate, the race windows, what fails silently -- and then
the page's buffers, every file the HUD writes, every listener, the demo's and `poke`'s token and
the git history, and read the Linux lockers' sources for what a lock screen tells anyone.

- Every tracked source read again along the typing's path: firmware, carriers, readers, decoder,
  gate, Hub, page, panels, session files, logs.
- `shellcheck -S style` on the five shell scripts: clean before and after.
- `bandit -r host site` (tests excluded): 0 high, 7 medium, 74 low, against 5 medium and 64 low
  on 2026-10-02. The two mediums since are `ET.fromstring` on a glyph (`host/keymap.py`
  glyph_problem, which refuses a doctype, entity, CDATA section or processing instruction before
  the parser sees the text, so there is no entity to expand) and the release lookup's `urlopen`
  (`host/cli.py` latest_release: https, GitHub's API, from `update` alone). The rest are the
  earlier review's.
- `pip-audit --local` on the virtualenv's 51 packages: no known vulnerabilities on the day.
- Git history: no log, recording or session file was ever committed; `run/` and the page's
  dumps are ignored.

Every finding is fixed in the commit named.

| # | Severity | What | Where | Status |
|---|---|---|---|---|
| 19 | High (Linux) | The session lock was not seen. hyprlock, Omarchy's shell and swaylock are no toolkit's widget tree and no terminal, and none sets logind's `LockedHint` (their sources say so), so the unlock password reached the strip, which showed it for about two seconds after the screen came back, the counts and the socket's clients. macOS's lock screen turns secure input on and is covered by the switch. | `host/a11y.py`, `hud/keys.js`, `host/linux/panel.py` | Fixed in `24d6457`: `host/sessionlock.py` asks Hyprland's control socket whether a session lock holds a monitor (`solitaryBlockedBy` with `LOCK`, what Omarchy's own lock script reads), the answer kept 100 ms; a Hyprland that does not say falls back to the lockers' process names. Another compositor's lock is a limit (below). |
| 20 | Medium | Layer changes passed the gate: which layer each password character came from (digits, symbols, a shift layer) and when, to the board and every client. | `host/hudfeed.py` SecureInput.allows | Fixed in `4b78d1b`: held with the keys; the latest of each keyboard follows the off message. |
| 21 | Medium (Linux) | The accessibility probe answered from a flag set after the focus signal was taken in and the app had said what the widget is (a round trip, up to 2 s for one that hangs), while SecureInput's docstring promised the first key could not slip through. Keys typed meanwhile went on. | `host/a11y.py` PasswordFocus, `host/hudfeed.py` SecureInput.check | Fixed in `b4e03bf`: the probe says it does not know yet from the signal to the answer, and the feed holds keys, positions and layers silently meanwhile; a flood that fills the signal queue is said. |
| 22 | Medium (Linux) | A password field focused before the watch looked -- the feed started or the bus came back with a login form up, a window brought back to the front with its field still focused -- was never seen. | `host/a11y.py` PasswordFocus._watch | Fixed in `a12c4c4`: the active window is asked for a focused password field at the start, on `window:activate` and after a flood (Collection `GetMatches`; a bounded walk where the app has no Collection). The Collection constants are to be confirmed on a live bus (below). |
| 23 | Medium | `feed --raw` logged every decoded HID report before the gate, passwords included, and the README's troubleshooting and docs/zmk-setup.md send users there. | `host/hudfeed.py` Stream.message | Fixed in `5206c1f`: the readers ask the gate before each frame is logged; the help and the docs say the raw log is the typing and goes quiet while a secret is typed. |
| 24 | Low-Med | `--no-keys`, documented as "layers only", dropped only `key`: press and release positions still went out, and with the keymap they are the typing. | `host/hudfeed.py` Feed._emit | Fixed in `da3bb87`: the positions go with the keys; `--no-hid-keys` keeps them. |
| 25 | Low-Med | Fail-open was silent: Carbon not loading, jeepney missing, no bus, no Hyprland, a role lookup failing and `feed.secure_input: 0` each left keys flowing with nothing said, and `doctor` reported the watch on from `IsEnabled` alone, without jeepney in the venv. | `host/hudfeed.py` Feed, `host/cli.py` check_accessibility | Fixed in `924018c`: the feed names its probes when it starts, or says nothing watches; every `secure` message carries `watched` and one goes out at the start; the page says `typing not watched`; `feed --probes` reports each probe and `doctor` reads it. |
| 26 | Low | On `secure on` the page kept what it had not yet counted or sent (the ledger, the strokes, the unsent counts, the WPM windows, the recent positions): a key that slipped in as the probe caught up was counted, and the test tolerated one. | `hud/hud.js` setSecure | Fixed in `d49b4e6`: all of it goes, as when a keymap is replaced; the test counts exactly. |
| 27 | Low | `demo` printed `poke --url ws://127.0.0.1:8767/<token>`, which puts the socket's token in a command line that every user on the machine reads with `ps`. | `host/cli.py` demo_socket | Fixed in `060da91`: `ZMKHUD_TOKEN=… ZMKHUD_PORT=… zmk-layer-hud poke`, the environment being the user's alone; `--url` still works. |
| 28 | Low | `docs/make-gif.sh` served `hud/` on every interface for as long as a render ran; the review above said every listener binds loopback. | `docs/make-gif.sh` | Fixed in `9876565`: `--bind 127.0.0.1`. |
| 29 | Low | A `panel.log` or `menubar.log` from before `umask 077` kept its 0644 (`>` keeps a file's mode); cli.py opened `menubar.log` with the default umask; launchd makes `autostart.log` 0644 from the plist. Status lines only, today. | `host/linux/hud.sh`, `host/macos/start.sh`, `host/cli.py` | Fixed in `dd87dde`: chmod 600 before the redirect; `panelstate.open_private` for cli.py's logs, `autostart.log` made 0600 first for launchd to append to. |
| 30 | Info | `:memory:.ses`, a tool's session marker, sat untracked and not ignored at the root. | `.gitignore` | `c5198e4`: `*.ses` ignored. |

Found sound, again: the socket's token and Origin gate; no character and no order on disk (a
session is counts per layer and position, 0600 in 0700); no telemetry, no remote script, font or
analytics on any page; the glyph allowlist; the Linux panel passes the token in the WebView's
URL and the feed's environment, never on a command line; the macOS panel keeps the Hub
in-process; the firmware sends positions and a layer bitmap, never usages or characters.

### Limits added

- **The terminal probe sees a prompt that reads a line with echo off**, which is what `sudo`,
  `ssh`, `su`, `passwd` and `getpass` do. One that takes the keys raw, one at a time -- `sudo`
  with `pwfeedback`, `systemd-ask-password`, a curses or tty pinentry, a prompt that masks with
  stars -- looks like an editor and is not seen; nor is a prompt on the far side of `ssh`,
  `mosh`, a container or a VM, whose pty is not this machine's (the local one is raw for the
  whole session). Local tmux and screen are seen, and the ssh client's own passphrase prompt.
- **The session lock is seen under Hyprland alone.** Another compositor's lock screen is not:
  the lockers set no logind hint, so there is nothing to ask. Stop the HUD before locking there.
- **When the gate misses, a session keeps the keys as a multiset**: a count per key and layer,
  no order, which for a short secret typed on an otherwise quiet day says which keys it used.
  `feed.sessions: 0` keeps no files.
- **A keyboard cabled to one machine while typing over BLE to another** sent its positions to
  the cabled machine's serial port too (`positions;`), with no prompt of its own to tell it to
  stop. No longer: the signal goes only where the typing goes (finding 31 below).
- **Typing sent in (`poke`) bypasses the gate**, and the Linux strip window shows it even while
  secure input is on: only a holder of the run's token can, which is the user's own process.
- **`hide` keeps counting and broadcasting**: it is for recording with the HUD off screen, not a
  privacy switch. The off switch is `stop`.
- **Not confirmed here.** This pass ran in a sandbox with no display, no ptys, no D-Bus and no
  Hyprland; the Linux probes are tested against fakes (`a11y_test`, `sessionlock_test`). To
  confirm on Omarchy: `hyprctl -j monitors | jq '[.[].solitaryBlockedBy]'` carries `"LOCK"`
  while locked (`omarchy-hyprland-session-locked` exits 0 then); `grep -n ATSPI_Collection_
  /usr/include/at-spi-2.0/atspi/atspi-constants.h` gives MATCH_ALL 1, MATCH_ANY 2 and
  SORT_ORDER_CANONICAL 1; a browser's login form up before `zmk-layer-hud start` hides the
  typing at once; `zmk-layer-hud doctor` names the four probes. On macOS: the lock screen's
  password field leaves `secure input · typing hidden` on a HUD left visible.

## 2026-10-21: the signal reached hosts the keyboard was not typing to

Found while testing the open PRs together: with the Diamond's selected BLE profile on an Omarchy
laptop, a Mac bonded on another profile stayed connected, and its HUD lit the key positions of
what was being typed into the laptop. Its strip stayed empty, because ZMK sends the key reports to
the selected profile alone.

| # | Severity | What | Where | Status |
|---|---|---|---|---|
| 31 | Medium | The signal went to every host that subscribed and to the serial port, whatever ZMK was typing to: the GATT carrier notified with a NULL connection, which is every subscriber, ZMK stays connected to the hosts on its other profiles, and the port carried it while the keyboard typed over BLE. With `positions;` on and the keymap known, that is the typing, on a host whose secure-input gate cannot see a password field on the one being typed to. | `firmware/src/signal_transport.h`, `firmware/src/signal_gatt.c` | Fixed in `1a1b809`: the signal goes on the carrier of the endpoint ZMK has selected, and over BLE to the selected profile's connection alone, when that host has subscribed. `firmware/tests/test_signal_route.c` compiles the carriers against fakes of Zephyr and ZMK. Not yet flashed and tried on a board. |

## What was looked for and found sound

- Every listener binds 127.0.0.1 only (since `9876565` the GIF script's page server too, finding
  28). No server serves files from disk except the demo's page server and that one, which serve
  `hud/` and nothing else.
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
- **Linux has no Secure Input.** A terminal's password prompt is seen (`hudfeed.terminal_probe`:
  the terminal reads a line with echo off, which is what `sudo`, `ssh` and `getpass` do), and a
  password field with focus is seen on the accessibility bus (`host/a11y.py`: AT-SPI's `password
  text` role) in every app that exposes its widgets. For that the feed sets `org.a11y.Status
  IsEnabled` on the session bus, as a screen reader does, which makes every app of the session
  keep an accessibility tree that any process of the same user may read: that user already reads
  the keyboard's own reports through hidraw, so no new party sees the typing, but window contents
  become readable too. `feed.accessibility: 0` leaves the setting alone. An app that exposes
  nothing gives no sign, and what is typed into it reaches the feed and its page. The README
  says so.
- **The Origin gate trusts `null`.** A `file:` page and a sandboxed frame both say `null`, which
  is why the token, not the Origin, is what admits a client.

## The touch keyboard (2026-10-11)

Since v1.2 the HUD types: tap a drawn key and the operating system gets that key
([docs/touch.md](touch.md)). The page goes from something that reads the typing to something that
can do it, so what may reach the page now matters both ways. This was reviewed by reading what was
added, with the suite below; the tablets' apps are reviewed as code, since neither has been built
on a device yet.

**What can type, and through what.**

- Only the panel's own page types on the machine it runs on, and only through the panel's native
  bridge. The macOS panel takes `hid` only from the main frame, and its web view loads nothing but
  the HUD's own files (`decidePolicyForNavigationAction`); the Linux panel does the same through
  `decide-policy` and opens no new window. The feed's socket never takes `hid`
  (`hudfeed.INJECTABLE`): its token sits in every page URL, so it is no key to typing.
- What a page may press is a keyboard's worth (`host/inject.py`): ten keys at most, modifiers
  folded out of them, never the Power key, messages of 512 bytes at most and 100 a second. A page
  silent for 1.5 s has its keys let go, and so does one that reloads, hides or goes away.
- **Any script that runs in the page can now type into the operating system.** The glyph allowlist
  (finding 4) and the local-only URL parameters (finding 3) kept the page from being made to read
  the typing; they now also keep it from being made to type, and must stay as strict.

**The leave it takes.**

- macOS: posting key events needs Accessibility, granted to the launcher that logs in, which lends
  it to the tree's command as it lends Input Monitoring (below). `typing setup` asks for it; nothing
  is posted until it is granted.
- Linux: a virtual keyboard through `/dev/uinput`, which `typing setup` lets the user at the seat
  make, with a `uaccess` udev rule (`contrib/udev/61-zmk-layer-hud-uinput.rules`), asking before
  each sudo step. Opt-in; deleting the rule takes it back.

**Remote mode** (`zmk-layer-hud remote on`) serves the page to a tablet on the local network, and
the tablet's taps are typed here. Off until asked for; open 30 minutes, longer while a tablet is
connected; on the default route's address only when it is a private, link-local or Tailscale one.

| Who | What they can do | What stops the rest |
|---|---|---|
| Someone listening on the same Wi-Fi | see which keys the tablet presses: the page and its messages are plain HTTP | nothing in remote mode; serve on a Tailscale address (`--bind`), or do not use it on that network |
| Someone on the network connecting | nothing | the key: 256 random bits in the link's fragment, which a browser never sends, proved by HMAC-SHA256 and never sent; five failed proofs turn remote mode off |
| Someone replaying a captured connection | nothing | a key for each connection from both sides' nonces; every report MAC'd, its number only going up |
| Someone who changes what the tablet loads while it loads the page | take the key from the page, and type | a Tailscale address; `remote unpair` after |
| A web page, in any browser, reaching for the socket | nothing | the socket opens for the page's own Origin alone, and only to a Host header naming this address (no DNS rebinding) |
| A page framing the remote page | nothing | `frame-ancestors 'none'`, `X-Frame-Options: DENY`; a CSP that runs the page's own scripts alone |
| Someone with the tablet in hand | type while remote mode is on: the key is in its browser's storage | `remote unpair`, or remote mode off |
| Someone flooding it | slow it down | four connections waiting to pair at most, 10 s to pair, 60 reports a second |

A paired tablet is told the keymap, whether a secret is being typed and whether the computer can
type. It is never sent what the physical keyboard types.

**The tablets' keyboards.** The Android keyboard is the HUD's page in a WebView, served from the
app's own assets; every other request is refused, it never navigates, and its bridge takes messages
from the main frame alone. The app has no network permission (`apps/apps_test.py` says so of its
manifest). The iPad keyboard asks for no Full Access and has no network; its app reaches the local
network only to fetch a keymap from a computer in remote mode, pairing as the remote page does.

**Limits this adds.**

- **The uinput rule reaches further than the HUD.** With it, any process of the user at the seat
  can make a virtual keyboard and type into any window, which Wayland otherwise keeps apps from
  doing to one another. That is the leave a virtual keyboard needs; `typing setup` says so before
  it installs the rule.
- **Remote mode is plain HTTP** (the table above). TLS and sealed reports would close the
  listener's view; they are left for later.
- **A panel killed with a key held** (SIGKILL, a crash) cannot let it go: on macOS a modifier stays
  down until it is pressed and released on the keyboard. A clean quit, a hide or a reload lets
  everything go.

## Running the checks again

```bash
make test
shellcheck -S style install.sh bin/zmk-layer-hud host/linux/hud.sh host/macos/start.sh docs/make-gif.sh
.venv/bin/python -m pip_audit --local
bandit -q -r host site -x '*_test.py' -ll
```

The suite covers the gate (`hudfeed_test.Gate`, `Socket`), the token file (`panelstate_test.Token`),
`poke`'s URL (`hudpoke_test.Url`), the glyph allowlist on both sides (`keymap_test.Glyphs`,
`hud_test.js` `checkGlyphs`), the page's local-only parameters (`checkLocalOnly`), the tarball
(`cli_test.Update`), the port (`hudfeed_test.PortTaken`) and the Hyprland backup. Since
2026-10-13 also secure input end to end: `hudfeed_test.Secure` (layers held, the raw log held, a
probe that does not know yet, what watches said), the accessibility watch against a bus of the
test's own (`a11y_test`, which runs where jeepney is on the path; it is not in the macOS venv),
the session lock (`sessionlock_test`), the page's secure block (`stats_test.js`), the log modes
(`panelstate_test.PrivateFile`, `cli_test.Autostart`) and the doctor's rows (`cli_test.Doctor`).
The three `Socket` tests and `sessionlock_test.Wire` open a real socket and skip where the
process may not; run `make test-host` from a terminal to see them.

For the touch keyboard: what a page may press (`inject_test`), remote mode's pairing, reports, pages,
headers and address (`remote_test`; its `Handler` tests need the venv's websockets, and `Visit` a
port), the page's side of the pairing against the same vectors (`hud/tests/remote_test.js`), the
iPad app's (`make test-ios`), and the tablets' builds (`make test-apps`).
