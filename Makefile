# zmk-layer-hud: host-side tests and helpers. The firmware itself is built by the
# keyboards repo (west, inside its container); see docs/keyboards-repo.md.

CC ?= cc
# keymap-drawer needs Python >= 3.10; Apple's /usr/bin/python3 is 3.9, so prefer Homebrew's or
# a versioned interpreter. Override with PYTHON=/path/to/python3.
PYTHON ?= $(shell command -v /opt/homebrew/bin/python3 || command -v python3.13 || command -v python3.12 || command -v python3.11 || command -v python3.10 || command -v python3)
# The HUD page's own suite runs hud.js under node with a small DOM shim; no npm, no package.json.
NODE ?= $(shell command -v node)

.PHONY: all install install-Darwin install-Linux install-config link-config test test-firmware test-host test-hud fixture keymap venv clean help

all: test

UNAME_S := $(shell uname -s)
VENV_PKGS := pyserial keymap-drawer websockets bleak
ifeq ($(UNAME_S),Darwin)
# hidapi is macOS only: Linux reads /dev/hidrawN itself, and the wheel there bundles the libusb
# backend, which wants an access no udev rule here grants and detaches the kernel HID driver.
VENV_PKGS += hidapi pyobjc-framework-Cocoa pyobjc-framework-WebKit
endif

venv: ## create .venv with pyserial, hidapi, keymap-drawer, websockets, bleak (+ pyobjc on macOS; brew install hidapi first)
	@$(PYTHON) -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || \
	  { echo "need Python >= 3.10 (found $$($(PYTHON) --version) at $(PYTHON)); brew install python or pass PYTHON=" >&2; exit 1; }
	$(PYTHON) -m venv --clear .venv
	.venv/bin/python3 -m pip install --quiet --upgrade pip
	.venv/bin/python3 -m pip install --quiet $(VENV_PKGS)
	@echo "venv ready: .venv/bin/python3 ($$(.venv/bin/python3 --version)); the host scripts pick it up"

# Everything a machine needs before ./start.sh works, in the order it needs it. Split by host
# because only the system packages and the permissions differ; the venv is the same either way.
# SUDO= skips the privileged steps and prints them instead, for a machine where you would rather
# run them yourself.
SUDO ?= sudo

install: ## set this machine up for the HUD (system packages, venv, config, permissions)
	@$(MAKE) --no-print-directory install-$(UNAME_S)
	@$(MAKE) --no-print-directory install-config
	@echo
	@echo "ready: ./start.sh"

install-Darwin:
	@echo "==> hidapi (the Python wheel links against it)"
	@command -v brew >/dev/null || { echo "install.sh needs Homebrew for hidapi: https://brew.sh" >&2; exit 1; }
	brew list hidapi >/dev/null 2>&1 || brew install hidapi
	@$(MAKE) --no-print-directory venv
	@echo
	@echo "==> one thing this cannot do for you"
	@echo "    The typed-keys strip reads the keyboard's HID reports, which macOS gates behind"
	@echo "    Input Monitoring. Grant it to whatever you start the HUD from — your terminal, or"
	@echo "    Hammerspoon — in System Settings > Privacy & Security > Input Monitoring, and"
	@echo "    untick the keyboard under Karabiner-Elements > Devices if you run it, because a"
	@echo "    keyboard whose events it modifies is seized and we get no reports."
	@echo "    Layers and positions need none of that; --no-hid-keys drops the strip and the grant."

install-Linux:
	@echo "==> system packages (Arch/Omarchy; other distros: the same four by their own names)"
	@if command -v pacman >/dev/null; then \
	  echo "    $(SUDO) pacman -S --needed python-gobject webkit2gtk-4.1 gtk-layer-shell"; \
	  [ -n "$(SUDO)" ] && $(SUDO) pacman -S --needed python-gobject webkit2gtk-4.1 gtk-layer-shell || true; \
	else \
	  echo "    no pacman here: install python-gobject, webkit2gtk-4.1 and gtk-layer-shell yourself"; \
	fi
	@$(MAKE) --no-print-directory venv
	@echo
	@echo "==> udev rule: the tty for the layer signal, hidraw for the typed-keys strip"
	@echo "    $(SUDO) cp contrib/udev/60-zmk-layer-hud.rules /etc/udev/rules.d/"
	@if [ -n "$(SUDO)" ]; then \
	  $(SUDO) cp contrib/udev/60-zmk-layer-hud.rules /etc/udev/rules.d/ && \
	  $(SUDO) udevadm control --reload-rules && $(SUDO) udevadm trigger && \
	  echo "    installed; replug the keyboard (a rule applies to nodes created after it)"; \
	else \
	  echo "    $(SUDO) udevadm control --reload-rules && $(SUDO) udevadm trigger"; \
	fi

install-config:
	@echo
	@echo "==> config"
	@if [ -f "$(HOME)/.config/zmk-layer-hud/config.yaml" ]; then \
	  echo "    $(HOME)/.config/zmk-layer-hud/config.yaml is yours already, left alone"; \
	else \
	  mkdir -p "$(HOME)/.config/zmk-layer-hud" && \
	  cp config/example.yaml "$(HOME)/.config/zmk-layer-hud/config.yaml" && \
	  echo "    wrote $(HOME)/.config/zmk-layer-hud/config.yaml from config/example.yaml"; \
	  echo "    set \`keymap:\` to your keymap-drawer YAML, then: $(PYTHON) host/keymap.py"; \
	fi

# For a config you keep in this repo rather than one you started from example.yaml: link it
# instead of copying it, so `git pull` is the whole of syncing a machine. Deliberately not part of
# `install` -- someone who copied example.yaml and edited it would otherwise find themselves
# editing a tracked file, dirtying their tree and colliding on every pull.
#
# Both names are linked. `<config>.imported.yaml` is found beside the config *path*, not beside
# whatever that path points at (host/keymap.py's imported_path normalises with abspath and does
# not resolve symlinks), so linking only config.yaml would leave the machine's stale imported file
# in play -- and the two files disagree about combo_term_ms in ways that cancel out only while
# they travel together.
link-config: ## link ~/.config/zmk-layer-hud at a config kept in this repo (CONFIG=config/diamond.yaml)
	@[ -n "$(CONFIG)" ] || { echo "link-config: pass CONFIG=<a config in this repo>, e.g. make link-config CONFIG=config/diamond.yaml" >&2; exit 1; }
	@[ -f "$(CONFIG)" ] || { echo "link-config: no such file: $(CONFIG)" >&2; exit 1; }
	@mkdir -p "$(HOME)/.config/zmk-layer-hud"
	@link() { \
	  if [ ! -e "$$1" ]; then echo "    no $$1, skipped"; return 0; fi; \
	  if [ -e "$$2" ] && [ ! -L "$$2" ]; then mv "$$2" "$$2.bak"; echo "    kept yours as $$2.bak"; fi; \
	  ln -sfn "$$1" "$$2"; echo "    $$2 -> $$1"; \
	}; \
	link "$(abspath $(CONFIG))" "$(HOME)/.config/zmk-layer-hud/config.yaml"; \
	link "$(basename $(abspath $(CONFIG))).imported.yaml" "$(HOME)/.config/zmk-layer-hud/config.imported.yaml"
	@$(PYTHON) host/keymap.py

test: test-firmware test-host test-hud ## run every test suite

test-firmware: ## host-side tests for the module's pure encode/decode policy
	@mkdir -p build
	$(CC) -std=c11 -Wall -Wextra -Werror -O1 -o build/test_layer_signal firmware/tests/test_layer_signal.c
	./build/test_layer_signal

test-host: ## Python tests: the wire format, the signal decoder, the keymap-drawer conversion
	$(PYTHON) -m unittest discover -s host -p '*_test.py' -v

test-hud: ## the HUD page: every key on every layer, every combo, the typed-keys strip
	@if [ -z "$(NODE)" ]; then \
	  echo "test-hud: node not found, skipping (brew install node)"; \
	else \
	  $(NODE) hud/tests/hud_test.js $(if $(KEYMAP),--keymap $(KEYMAP)) && \
	  PYTHON="$(PYTHON)" $(NODE) hud/tests/strip_test.js $(if $(KEYMAP),--keymap $(KEYMAP)); \
	fi

fixture: ## rebuild hud/tests/fixtures/diamond.json from the configured keymap (glyphs placeheld)
	$(PYTHON) host/keymap.py --dump | $(PYTHON) hud/tests/fixtures/make.py > hud/tests/fixtures/diamond.json

keymap: ## check the configured keymap-drawer YAML converts (ZMKHUD_CONFIG or ~/.config/zmk-layer-hud/config.yaml)
	$(PYTHON) host/keymap.py

clean:
	rm -rf build host/__pycache__

help:
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-16s %s\n", $$1, $$2}'
