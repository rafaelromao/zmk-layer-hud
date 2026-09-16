# zmk-layer-hud: host-side tests and helpers. The firmware itself is built by the
# keyboards repo (west, inside its container); see docs/keyboards-repo.md.

CC ?= cc
PYTHON ?= python3

.PHONY: all test test-firmware test-host keymap venv clean help

all: test

UNAME_S := $(shell uname -s)
VENV_PKGS := hidapi keymap-drawer websockets
ifeq ($(UNAME_S),Darwin)
VENV_PKGS += pyobjc-framework-Cocoa pyobjc-framework-WebKit
endif

venv: ## create .venv with hidapi, keymap-drawer, websockets (+ pyobjc on macOS; brew install hidapi first)
	$(PYTHON) -m venv .venv
	.venv/bin/python3 -m pip install --quiet --upgrade pip $(VENV_PKGS)
	@echo "venv ready: .venv/bin/python3 (the host scripts pick it up)"

test: test-firmware test-host ## run every test suite

test-firmware: ## host-side tests for the module's pure encode/decode policy
	@mkdir -p build
	$(CC) -std=c11 -Wall -Wextra -Werror -O1 -o build/test_layer_signal firmware/tests/test_layer_signal.c
	./build/test_layer_signal

test-host: ## Python tests: raw-HID decoder and keymap-drawer conversion
	$(PYTHON) -m unittest discover -s host -p '*_test.py' -v

keymap: ## check the configured keymap-drawer YAML converts (ZMKHUD_CONFIG or ~/.config/zmk-layer-hud/config.yaml)
	$(PYTHON) host/keymap.py

clean:
	rm -rf build host/__pycache__

help:
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-16s %s\n", $$1, $$2}'
