/*
 * Copyright (c) 2026 Rafael Romão
 *
 * SPDX-License-Identifier: MIT
 *
 * Announce the active layers, and optionally each key press, to the HUD host.
 *
 * The signal travels on a carrier of this module's own -- a CDC-ACM serial
 * interface over USB, notifications on our own GATT service over BLE -- as the
 * frames defined in signal_frame.h. It used to ride inside the keyboard HID
 * report as reserved keyboard-page usages, on the premise that no OS maps
 * 0xA5-0xDF. Linux does: every unmapped slot in the kernel's hid_keyboard[]
 * table holds KEY_UNKNOWN rather than zero, so the whole range arrives as
 * keycode 240 and every layer change became a phantom key press carrying
 * whatever modifiers were held. Holding Gui and touching a layer was enough to
 * make a Wayland compositor change workspace. Nothing on these carriers can be
 * read as a key.
 *
 * Both transports drop rather than block, so frames are sent straight from the
 * event listener and this file has no work queue of its own. The one piece of
 * deferred work left is the settle timer, which exists only to coalesce a run
 * of layer changes into a single frame.
 */

#define DT_DRV_COMPAT zmk_layer_signal

#include <zephyr/devicetree.h>
#include <zephyr/init.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

#include <zmk/event_manager.h>
#include <zmk/events/endpoint_changed.h>
#include <zmk/events/layer_state_changed.h>
#include <zmk/events/position_state_changed.h>
#include <zmk/keymap.h>

#include "signal_frame.h"
#include "signal_transport.h"

LOG_MODULE_DECLARE(zmk, CONFIG_ZMK_LOG_LEVEL);

#if DT_HAS_COMPAT_STATUS_OKAY(DT_DRV_COMPAT)

#define SETTLE_MS DT_INST_PROP(0, settle_ms)
#define HEARTBEAT_MS DT_INST_PROP(0, heartbeat_ms)
#define POSITIONS DT_INST_PROP(0, positions)

BUILD_ASSERT(ZMK_KEYMAP_LAYERS_LEN <= 32, "the layer bitmap carries 32 layers");

static void send_layers(void) {
    uint8_t frame[ZLS_FRAME_MAX_LEN];
    size_t len = zls_frame_layers((uint32_t)zmk_keymap_layer_state(), frame, sizeof(frame));

    zls_transport_send(frame, len);
    LOG_DBG("layer signal: state 0x%08x", (uint32_t)zmk_keymap_layer_state());
}

static void work_cb(struct k_work *work);
static K_WORK_DELAYABLE_DEFINE(work, work_cb);

static void work_cb(struct k_work *work_item) {
    ARG_UNUSED(work_item);

    send_layers();

    /* The heartbeat lets a host that starts mid-session converge without a
     * resync channel. It no longer has to wait for held keys: the old code
     * deferred it because its usages reached the host as key events and
     * interrupted auto-repeat, and nothing on these carriers does. */
    if (HEARTBEAT_MS > 0) {
        k_work_reschedule(&work, K_MSEC(HEARTBEAT_MS));
    }
}

static int layer_signal_listener(const zmk_event_t *eh) {
    if (as_zmk_layer_state_changed(eh) != NULL || as_zmk_endpoint_changed(eh) != NULL) {
        /* Coalesce: a host-driven vim mode switch or a layer-tap roll moves
         * several layers at once, and the HUD only wants the settled set. */
        k_work_reschedule(&work, K_MSEC(SETTLE_MS));
        return ZMK_EV_EVENT_BUBBLE;
    }

#if POSITIONS
    const struct zmk_position_state_changed *pos_ev = as_zmk_position_state_changed(eh);
    if (pos_ev != NULL && pos_ev->position <= UINT8_MAX) {
        uint8_t frame[ZLS_FRAME_MAX_LEN];
        size_t len =
            zls_frame_position((uint8_t)pos_ev->position, pos_ev->state, frame, sizeof(frame));

        /* Straight from the listener: the transports cannot block, so this
         * cannot delay the keymap's own handling of the press the way the
         * report-based version did. */
        zls_transport_send(frame, len);
    }
#endif

    return ZMK_EV_EVENT_BUBBLE;
}

ZMK_LISTENER(layer_signal, layer_signal_listener);
ZMK_SUBSCRIPTION(layer_signal, zmk_layer_state_changed);
ZMK_SUBSCRIPTION(layer_signal, zmk_endpoint_changed);
#if POSITIONS
ZMK_SUBSCRIPTION(layer_signal, zmk_position_state_changed);
#endif

static int layer_signal_init(void) {
    /* Announce the boot state once the carriers are up. */
    k_work_reschedule(&work, K_MSEC(1000));
    return 0;
}

SYS_INIT(layer_signal_init, APPLICATION, CONFIG_APPLICATION_INIT_PRIORITY);

#endif /* DT_HAS_COMPAT_STATUS_OKAY */
