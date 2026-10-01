# Command line reference

Every verb of `zmk-layer-hud`, as its own `--help` prints it. Written by `make cli-docs` from the parsers in `host/cli.py` (and `host/hudpoke.py`, `host/hudfeed.py` for `poke` and `feed`); do not edit by hand.

- [`start`](#start) — start the HUD
- [`stop`](#stop) — stop the HUD
- [`restart`](#restart) — stop the HUD, then start it
- [`show`](#show) — bring the HUD back on screen
- [`hide`](#hide) — take the HUD off screen; it goes on running and counting
- [`toggle`](#toggle) — show the HUD if it is hidden, hide it if it is shown
- [`status`](#status) — is it running, and what is it reading
- [`session`](#session) — the typing sessions: the active one, naming it, starting or loading another
- [`heatmap`](#heatmap) — what the keys glow with: the live heatmap, the session's, or none
- [`log`](#log) — follow the panel and feed logs
- [`doctor`](#doctor) — check this machine and say what is missing
- [`autostart`](#autostart) — start the HUD hidden at login, counting from the first keystroke, or stop doing so
- [`menubar`](#menubar) — the icon that shows, hides or starts the HUD, with its live WPM: macOS's menubar, Omarchy's bar
- [`setup`](#setup) — set this machine up (packages, venv, config, permissions)
- [`update`](#update) — fetch a newer tree over this one
- [`uninstall`](#uninstall) — remove the tree and the command
- [`import`](#import) — write the HUD's definitions: the drawing, and what a ZMK repo's keymap says of it
- [`sync`](#sync) — write the definitions again from the same sources, and say what changed
- [`keymap`](#keymap) — check the HUD's own files load: the config and the definitions import wrote
- [`config`](#config) — where the config is, and what is in it
- [`demo`](#demo) — serve the pages against a sample keymap, with no keyboard; --play types a script on them
- [`poke`](#poke) — drive the HUD by sending the feed what a keyboard would have sent
- [`feed`](#feed) — run the feed alone, serving its WebSocket
- [`version`](#version) — what this is and where it lives

## start

```text
usage: zmk-layer-hud start [-h] [--reserve] [--hidden] [--foreground]

start the HUD

options:
  -h, --help    show this help message and exit
  --reserve     (Linux) give the HUD an exclusive zone so windows tile beside it rather than under
                it; for recording
  --hidden      start it off screen, counting; `zmk-layer-hud show` or its icon brings it up
  --foreground  stay until the HUD stops, as the HUD itself (its output still goes to the log);
                what a login item runs
```

## stop

```text
usage: zmk-layer-hud stop [-h]

stop the HUD

options:
  -h, --help  show this help message and exit
```

## restart

```text
usage: zmk-layer-hud restart [-h] [--reserve] [--hidden]

stop the HUD, then start it

options:
  -h, --help  show this help message and exit
  --reserve   as for `start`
  --hidden    as for `start`
```

## show

```text
usage: zmk-layer-hud show [-h]

bring the HUD back on screen

options:
  -h, --help  show this help message and exit
```

## hide

```text
usage: zmk-layer-hud hide [-h]

take the HUD off screen; it goes on running and counting

options:
  -h, --help  show this help message and exit
```

## toggle

```text
usage: zmk-layer-hud toggle [-h]

show the HUD if it is hidden, hide it if it is shown

options:
  -h, --help  show this help message and exit
```

## status

```text
usage: zmk-layer-hud status [-h]

is it running, and what is it reading

options:
  -h, --help  show this help message and exit
```

## session

```text
usage: zmk-layer-hud session [-h] [--all] [--yes] [--mode {session,physical,speed}]
                             [--layers LAYERS] [-o OUTPUT] [--config CONFIG]
                             [{status,list,new,save,load,reset,delete,rename-layer,export,history,compare}]
                             [name] [other]

the typing sessions: the active one, naming it, starting or loading another

positional arguments:
  {status,list,new,save,load,reset,delete,rename-layer,export,history,compare}
                        status (default), list, new [NAME], save NAME, load NAME, reset, delete
                        NAME, rename-layer OLD NEW, export [NAME], history [NAME], compare NAME
                        [OTHER]
  name                  the session, for new, save, load, delete, export, history and compare
                        (default: the active one); the layer, for rename-layer
  other                 the layer's new name, for rename-layer; the session to compare with
                        (default: the active one)

options:
  -h, --help            show this help message and exit
  --all                 rename-layer in every session, not only the active one; history: every
                        session's days added up
  --yes                 do not ask before reset or delete
  --mode {session,physical,speed}
                        export: the presses on each layer (default), every layer's together, or
                        each key's time
  --layers LAYERS       export: the layers to draw, comma-separated (default: every one with heat)
  -o, --output OUTPUT   export: the SVG file to write (default: NAME-MODE.svg here; - for stdout)
  --config CONFIG       export: the config whose keymap to draw (default: the one the HUD reads)
```

## heatmap

```text
usage: zmk-layer-hud heatmap [-h] [{live,session,physical,speed,off}]

what the keys glow with: the live heatmap, the session's, or none

positional arguments:
  {live,session,physical,speed,off}
                        live (what was just typed), session (every press counted, on the layer on
                        screen), physical (every press, all layers together), speed (the time each
                        key takes), off; none: say which

options:
  -h, --help            show this help message and exit
```

## log

```text
usage: zmk-layer-hud log [-h] [-n LINES] [--no-follow]

follow the panel and feed logs

options:
  -h, --help         show this help message and exit
  -n, --lines LINES  lines of history (default 40)
  --no-follow        print and exit instead of following
```

## doctor

```text
usage: zmk-layer-hud doctor [-h]

check this machine and say what is missing

options:
  -h, --help  show this help message and exit
```

## autostart

```text
usage: zmk-layer-hud autostart [-h] [{status,enable,disable}]

start the HUD hidden at login, counting from the first keystroke, or stop doing so

positional arguments:
  {status,enable,disable}
                        status (default), enable, disable; disable leaves a running HUD running

options:
  -h, --help            show this help message and exit
```

## menubar

```text
usage: zmk-layer-hud menubar [-h] [{status,enable,disable}]

the icon that shows, hides or starts the HUD, with its live WPM: macOS's menubar, Omarchy's bar

positional arguments:
  {status,enable,disable}
                        status (default), enable, disable

options:
  -h, --help            show this help message and exit
```

## setup

```text
usage: zmk-layer-hud setup [-h] [--no-sudo] [--yes] [--link] [--link-only] [--venv-only]

set this machine up (packages, venv, config, permissions)

options:
  -h, --help   show this help message and exit
  --no-sudo    print the privileged steps instead of running them
  --yes        do not ask before a privileged step
  --link       also put zmk-layer-hud on PATH from this tree
  --link-only  with --link, do nothing else
  --venv-only  build the virtualenv and do nothing else
```

## update

```text
usage: zmk-layer-hud update [-h] [--ref REF]

fetch a newer tree over this one

options:
  -h, --help  show this help message and exit
  --ref REF   branch to fetch (default: main, or $ZMKHUD_REF)
```

## uninstall

```text
usage: zmk-layer-hud uninstall [-h] [--purge] [--yes]

remove the tree and the command

options:
  -h, --help  show this help message and exit
  --purge     also remove the config, cache and logs
  --yes       do not ask
```

## import

```text
usage: zmk-layer-hud import [-h] [--keyboard KEYBOARD] [--config CONFIG] [--quiet] [--no-fetch]
                            [--pristine] [--keep-custom [N,M]] [--drop-custom]
                            [REPO]

write the HUD's definitions: the drawing, and what a ZMK repo's keymap says of it

positional arguments:
  REPO                 a GitHub URL, a path to a working copy, or the keymap-drawer YAML the
                       config names (none: draw from `keymap:` alone)

options:
  -h, --help           show this help message and exit
  --keyboard KEYBOARD  which keyboard in that repo
  --config CONFIG      config file (default: $ZMKHUD_CONFIG or ~/.config/...)
  --quiet              say nothing but errors
  --no-fetch           fetch no glyphs: use keymap-drawer's cache only
  --pristine           from scratch: ignore what earlier imports left (the repo they recorded,
                       drafted layer mappings, a cached clone) and read only what is given now
  --keep-custom [N,M]  with --pristine: keep what only an earlier import had and redo the rest --
                       all of it, or just the items numbered N,M in the list it prints
  --drop-custom        with --pristine: drop that too, without asking
```

## sync

```text
usage: zmk-layer-hud sync [-h] [--config CONFIG] [--quiet] [--no-fetch] [--watch]

write the definitions again from the same sources, and say what changed

options:
  -h, --help       show this help message and exit
  --config CONFIG  config file
  --quiet          say nothing but errors
  --no-fetch       fetch no glyphs: use keymap-drawer's cache only
  --watch          stay, and sync again each time the config, the keymap-drawer files or a working
                   copy's keymap is edited; the running HUD redraws (Ctrl-C stops)
```

## keymap

```text
usage: zmk-layer-hud keymap [-h] [--config CONFIG] [--dump]

check the HUD's own files load: the config and the definitions import wrote

options:
  -h, --help       show this help message and exit
  --config CONFIG  config file
  --dump           print the full JSON message
```

## config

```text
usage: zmk-layer-hud config [-h] [{path,show,edit,link}] [file]

where the config is, and what is in it

positional arguments:
  {path,show,edit,link}
                        path (default), show, edit, or link a config kept in a repo
  file                  with `link`: the config to link at

options:
  -h, --help            show this help message and exit
```

## demo

```text
usage: zmk-layer-hud demo [-h] [--config CONFIG] [--keymap FILE] [--play SCRIPT] [--loop]
                          [--speed SPEED] [--strict] [--port PORT] [--ws-port WS_PORT]
                          [--no-browser]

serve the pages against a sample keymap, with no keyboard; --play types a script on them

options:
  -h, --help         show this help message and exit
  --config CONFIG    a config to draw (default: config/example-3x5.yaml)
  --keymap FILE      a keymap message to draw instead (zmk-layer-hud keymap --dump)
  --play SCRIPT      type a demo script on it, in real time (docs/demo-scripts.md)
  --loop             with --play: again and again
  --speed SPEED      with --play: this many times as fast (default 1)
  --strict           with --play: refuse a script this keymap cannot type all of
  --port PORT        the pages' port (default 8765)
  --ws-port WS_PORT  the socket that feeds them (default 8767; a running HUD's feed has 8766)
  --no-browser       do not open a browser
```

## poke

```text
usage: zmk-layer-hud poke [-h] [--url URL] [--type TEXT] [--legend L] [--combos] [--layers IDS]
                          [--press POS] [--gap-ms GAP_MS] [--hold-ms HOLD_MS] [--stdin]
                          [--play SCRIPT] [--keymap FILE] [--loop] [--speed SPEED] [--strict] [-v]

Drive the HUD without a keyboard, by sending hudfeed the messages one would have produced.

options:
  -h, --help         show this help message and exit
  --url URL          hudfeed's WebSocket (default: ws://127.0.0.1:$ZMKHUD_PORT or 8766)
  --type TEXT        type TEXT one character at a time
  --legend L         type one legend (repeatable)
  --combos           draw what is typed with the keymap's combos where it has them (default:
                     without, a letter on its layer's own key)
  --layers IDS       set the active layer ids, comma separated ('' clears)
  --press POS        press and release a position (repeatable)
  --gap-ms GAP_MS    wait between keystrokes (default 90)
  --hold-ms HOLD_MS  how long a position stays pressed (default 120)
  --stdin            read raw messages from stdin, one JSON per line
  --play SCRIPT      play a demo script in real time (docs/demo-scripts.md)
  --keymap FILE      with --play: the keymap to type on (default: the feed's)
  --loop             with --play: play it again and again
  --speed SPEED      with --play: this many times as fast
  --strict           with --play: stop at a character the keymap cannot type
  -v, --verbose      print each message as it is sent
```

## feed

```text
usage: zmk-layer-hud feed [-h] [--config CONFIG] [--stdout] [--no-ws] [--port PORT] [--no-keymap]
                          [--no-keys] [--vid VID] [--pid PID] [--name NAME] [--serial DEV]
                          [--no-ble] [--ble-address BLE_ADDRESS] [--no-hid-keys] [--no-inject]
                          [--no-sessions] [--debug] [--raw]

Host feed for the zmk-layer-hud pages. One source: the keyboard's own signal channel.

options:
  -h, --help            show this help message and exit
  --config CONFIG       zmk-layer-hud config (default: $ZMKHUD_CONFIG or
                        ~/.config/zmk-layer-hud/config.yaml)
  --stdout              print messages as JSON lines (macOS host)
  --no-ws               do not serve the WebSocket
  --port PORT
  --no-keymap           skip the keymap feed (pages keep whatever they have)
  --no-keys             send layers only, no key events
  --vid VID             keyboard vendor id (default: config `keyboard.vid`, else ZMK's)
  --pid PID             keyboard product id (default: config `keyboard.pid`, else ZMK's)
  --name NAME           substring of the product name to select one keyboard (default: config
                        `keyboard.name`)
  --serial DEV          the keyboard's serial port, instead of finding it by vid/pid (default:
                        config `serial.port`)
  --no-ble              do not look for BLE keyboards
  --ble-address BLE_ADDRESS
                        address of the BLE keyboard (default: config `ble.address`); needed where
                        a connected keyboard no longer advertises
  --no-hid-keys         do not read the keyboard's HID reports for the typed-keys strip (needs
                        Input Monitoring on macOS); layers and positions are unaffected
  --no-inject           refuse messages sent in by a WebSocket client (layers, press, release,
                        key, device), which host/hudpoke.py uses to drive the page without a
                        keyboard
  --no-sessions         keep no session files (config `feed.sessions: 0`): the counts last as long
                        as the page
  --debug               log layer messages to stderr
  --raw                 DEBUG ONLY: log every decoded frame (includes your typing)
```

## version

```text
usage: zmk-layer-hud version [-h]

what this is and where it lives

options:
  -h, --help  show this help message and exit
```
