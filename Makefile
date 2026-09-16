# zmk-layer-hud: host-side tests and helpers. The firmware itself is built by the
# keyboards repo (west, inside its container); see docs/keyboards-repo.md.

CC ?= cc
PYTHON ?= python3

.PHONY: all test test-firmware test-host keymap clean help

all: test

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
