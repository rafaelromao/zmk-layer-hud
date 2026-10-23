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
#include <zmk/endpoints.h>

#if IS_ENABLED(CONFIG_ZMK_LAYER_SIGNAL_UART)
void zls_uart_send(const uint8_t *frame, size_t len);
#endif

#if IS_ENABLED(CONFIG_ZMK_LAYER_SIGNAL_GATT)
/* To the selected BLE profile's host alone: see signal_gatt.c. */
void zls_gatt_send(const uint8_t *frame, size_t len);
#endif

/* The signal goes where the typing goes: on the carrier of the endpoint ZMK has selected, so to
 * the one host the keyboard is typing to, and to no other. Key positions are the typing once the
 * keymap is known, and a HUD on a host that is not being typed to cannot see a password field on
 * the one that is. The serial port is the machine on the cable, which is typed to over USB; BLE is
 * the selected profile's host. ZMK stays connected to the hosts on its other profiles, and the
 * signal used to reach every one of them that subscribed, and the cable whatever the keyboard
 * typed to.
 *
 * Changing endpoint raises zmk_endpoint_changed, which layer_signal.c answers by sending the layer
 * set again, so the host now typed to is told at once. */
static inline void zls_transport_send(const uint8_t *frame, size_t len) {
    if (len == 0) {
        return;
    }
    switch (zmk_endpoint_get_selected().transport) {
#if IS_ENABLED(CONFIG_ZMK_LAYER_SIGNAL_UART)
    case ZMK_TRANSPORT_USB:
        zls_uart_send(frame, len);
        break;
#endif
#if IS_ENABLED(CONFIG_ZMK_LAYER_SIGNAL_GATT)
    case ZMK_TRANSPORT_BLE:
        zls_gatt_send(frame, len);
        break;
#endif
    default:
        break; /* typing nowhere, or on a carrier this build does not have */
    }
}

#endif /* ZMK_LAYER_HUD_SIGNAL_TRANSPORT_H */
