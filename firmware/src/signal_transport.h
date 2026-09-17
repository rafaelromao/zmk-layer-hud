/*
 * Copyright (c) 2026 Rafael Romão
 *
 * SPDX-License-Identifier: MIT
 *
 * Keyboard -> host carriers for the signal frames.
 *
 * Every send is best effort and never blocks: a frame that does not fit the
 * outgoing buffer is dropped, not waited on. That is deliberate. The signal is
 * cosmetic, and the version of this module that wrote into the keyboard HID
 * report blocked in zmk_endpoint_send_report on the system work queue -- the
 * queue ZMK debounces the matrix and raises position events on -- which pushed
 * the next key press's timestamp past the combo term and stopped combos firing.
 * Nothing here may ever be able to delay a keystroke, so the transports drop
 * frames instead. A dropped layer frame is corrected by the next change or the
 * heartbeat; a dropped position frame is one missed highlight.
 *
 * Because they cannot block, they are safe to call straight from the event
 * listener, so there is no work queue in this module at all any more.
 */

#ifndef ZMK_LAYER_HUD_SIGNAL_TRANSPORT_H
#define ZMK_LAYER_HUD_SIGNAL_TRANSPORT_H

#include <stddef.h>
#include <stdint.h>

#include <zephyr/kernel.h>

#if IS_ENABLED(CONFIG_ZMK_LAYER_SIGNAL_UART)
void zls_uart_send(const uint8_t *frame, size_t len);
#endif

#if IS_ENABLED(CONFIG_ZMK_LAYER_SIGNAL_GATT)
void zls_gatt_send(const uint8_t *frame, size_t len);
#endif

static inline void zls_transport_send(const uint8_t *frame, size_t len) {
    if (len == 0) {
        return;
    }
#if IS_ENABLED(CONFIG_ZMK_LAYER_SIGNAL_UART)
    zls_uart_send(frame, len);
#endif
#if IS_ENABLED(CONFIG_ZMK_LAYER_SIGNAL_GATT)
    zls_gatt_send(frame, len);
#endif
}

#endif /* ZMK_LAYER_HUD_SIGNAL_TRANSPORT_H */
