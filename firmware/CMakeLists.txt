# Copyright (c) 2026 Rafael Romão
# SPDX-License-Identifier: MIT

# The Kconfig symbol already carries the central-or-unibody condition.
target_sources_ifdef(
  CONFIG_ZMK_LAYER_SIGNAL
  app
  PRIVATE
  src/layer_signal.c
)

target_sources_ifdef(
  CONFIG_ZMK_LAYER_SIGNAL_UART
  app
  PRIVATE
  src/signal_uart.c
)

target_sources_ifdef(
  CONFIG_ZMK_LAYER_SIGNAL_GATT
  app
  PRIVATE
  src/signal_gatt.c
)
