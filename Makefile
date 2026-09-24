# zmk-layer-hud: `make install` sets this machine up from a clone, and the rest is the test
# suites. Everything a user does is a verb on the command itself -- `zmk-layer-hud setup`
# prepares a machine, `zmk-layer-hud keymap` checks the configured YAML -- so the targets here
# delegate to it rather than carry a second implementation.
#
# The firmware itself is built by the keyboards repo (west, inside its container); see
# docs/keyboards-repo.md.

# Every target here is a verb, never a file. Without this, GNU make's built-in `%: %.sh` rule
# turns `make install` into `cat install.sh >install; chmod a+x install` -- it prints those two
# lines, leaves an executable copy of the installer named `install` in the tree, and from then on
# reports `install' is up to date` without ever installing anything.
MAKEFLAGS += --no-builtin-rules
.SUFFIXES:

CC ?= cc
# keymap-drawer needs Python >= 3.10; Apple's /usr/bin/python3 is 3.9, so prefer Homebrew's or
# a versioned interpreter. Override with PYTHON=/path/to/python3.
PYTHON ?= $(shell command -v /opt/homebrew/bin/python3 || command -v python3.13 || command -v python3.12 || command -v python3.11 || command -v python3.10 || command -v python3)
# The HUD page's own suite runs hud.js under node with a small DOM shim; no npm, no package.json.
NODE ?= $(shell command -v node)

# The audit reads the keyboard's keymap with keymap-drawer, which lives in the venv.
VENV_PYTHON := $(wildcard .venv/bin/python3)

.PHONY: all install venv test test-firmware test-host test-hud audit fixture clean help

all: test

install: ## set this machine up and put zmk-layer-hud on your PATH, pointing at this clone
	bin/zmk-layer-hud setup --link

venv: ## just the virtualenv the feed and the tests run under
	bin/zmk-layer-hud setup --venv-only

test: test-firmware test-host test-hud ## run every test suite

test-firmware: ## host-side tests for the module's pure encode/decode policy
	@mkdir -p build
	$(CC) -std=c11 -Wall -Wextra -Werror -O1 -o build/test_layer_signal firmware/tests/test_layer_signal.c
	./build/test_layer_signal

test-host: ## Python tests: the wire format, the signal decoder, the keymap-drawer conversion
	$(PYTHON) -m unittest discover -s host -p '*_test.py' -v

test-hud: ## the HUD page: every key and combo, the strip, and every way to type every legend on every channel
	@if [ -z "$(NODE)" ]; then \
	  echo "test-hud: node not found, skipping (brew install node)"; \
	else \
	  $(NODE) hud/tests/hud_test.js $(if $(KEYMAP),--keymap $(KEYMAP)) && \
	  PYTHON="$(PYTHON)" $(NODE) hud/tests/strip_test.js $(if $(KEYMAP),--keymap $(KEYMAP)) && \
	  PYTHON="$(PYTHON)" $(NODE) hud/tests/words_test.js $(if $(KEYMAP),--keymap $(KEYMAP)); \
	fi

audit: ## the drawing against the keyboard's own keymap (SOURCE=<working copy>, else the one import recorded)
	$(or $(VENV_PYTHON),$(PYTHON)) host/ways.py --audit $(if $(CONFIG),--config $(CONFIG)) $(if $(SOURCE),--source $(SOURCE))

fixture: ## rebuild hud/tests/fixtures/diamond.json from the configured keymap (glyphs placeheld)
	$(PYTHON) host/keymap.py --dump | $(PYTHON) hud/tests/fixtures/make.py > hud/tests/fixtures/diamond.json

clean:
	rm -rf build host/__pycache__

help:
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-16s %s\n", $$1, $$2}'
