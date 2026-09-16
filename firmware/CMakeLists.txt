# Copyright (c) 2026 Rafael Romão
# SPDX-License-Identifier: MIT

# The Kconfig symbol already carries the central-or-unibody and HKRO conditions.
target_sources_ifdef(
  CONFIG_ZMK_LAYER_SIGNAL
  app
  PRIVATE
  src/layer_signal.c
)
