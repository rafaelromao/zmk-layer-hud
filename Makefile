# zmk-layer-hud: host-side tests and helpers. The firmware itself is built by the
# keyboards repo (west, inside its container); see docs/keyboards-repo.md.

CC ?= cc
# keymap-drawer needs Python >= 3.10; Apple's /usr/bin/python3 is 3.9, so prefer Homebrew's or
# a versioned interpreter. Override with PYTHON=/path/to/python3.
PYTHON ?= $(shell command -v /opt/homebrew/bin/python3 || command -v python3.13 || command -v python3.12 || command -v python3.11 || command -v python3.10 || command -v python3)
# The HUD page's own suite runs hud.js under node with a small DOM shim; no npm, no package.json.
NODE ?= $(shell command -v node)

.PHONY: all test test-firmware test-host test-hud fixture keymap venv clean help

all: test

UNAME_S := $(shell uname -s)
VENV_PKGS := pyserial keymap-drawer websockets bleak
ifeq ($(UNAME_S),Darwin)
VENV_PKGS += pyobjc-framework-Cocoa pyobjc-framework-WebKit
endif

venv: ## create .venv with pyserial, keymap-drawer, websockets, bleak (+ pyobjc on macOS)
	@$(PYTHON) -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || \
	  { echo "need Python >= 3.10 (found $$($(PYTHON) --version) at $(PYTHON)); brew install python or pass PYTHON=" >&2; exit 1; }
	$(PYTHON) -m venv --clear .venv
	.venv/bin/python3 -m pip install --quiet --upgrade pip
	.venv/bin/python3 -m pip install --quiet $(VENV_PKGS)
	@echo "venv ready: .venv/bin/python3 ($$(.venv/bin/python3 --version)); the host scripts pick it up"

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
