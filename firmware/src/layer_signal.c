/*
 * Copyright (c) 2026 Rafael Romão
 *
 * SPDX-License-Identifier: MIT
 *
 * Announce the active layers to the host inside the keyboard HID report.
 *
 * Every layer change is coalesced for settle-ms, then the set of active layers
 * is written into the report as reserved keyboard-page usages (base + id) with
 * a commit usage last, sent once, and released after tap-ms with a second send.
 * The host (zmk-layer-hud/host/hudfeed.py) decodes only the report carrying the
 * commit usage, so partial reports caused by real keys pressed meanwhile are
 * harmless: they still contain the full set or no commit at all.
 *
 * The report is written directly through zmk_hid_keyboard_press/release and
 * zmk_endpoint_send_report, the same calls hid_listener.c makes. Raising
 * zmk_keycode_state_changed instead would show the fake usages to every keycode
 * listener: auto-layer would end num-word, adaptive keys would record them as
 * antecedent, caps word and sticky keys would react.
 *
 * Everything runs on the system workqueue: the listener only marks state and
 * reschedules one k_work_delayable that alternates between press and release.
 */

#define DT_DRV_COMPAT zmk_layer_signal

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/util.h>

#include <zmk/endpoints.h>
#include <zmk/event_manager.h>
#include <zmk/events/endpoint_changed.h>
#include <zmk/events/layer_state_changed.h>
#include <zmk/events/position_state_changed.h>
#include <zmk/hid.h>
#include <zmk/keymap.h>

#include "layer_signal_policy.h"

LOG_MODULE_DECLARE(zmk, CONFIG_ZMK_LOG_LEVEL);

#if DT_HAS_COMPAT_STATUS_OKAY(DT_DRV_COMPAT)

#define BASE_USAGE DT_INST_PROP(0, base_usage)
#define COMMIT_USAGE DT_INST_PROP(0, commit_usage)
#define TAP_MS DT_INST_PROP(0, tap_ms)
#define SETTLE_MS DT_INST_PROP(0, settle_ms)
#define HEARTBEAT_MS DT_INST_PROP(0, heartbeat_ms)
#define POSITIONS DT_INST_PROP(0, positions)

BUILD_ASSERT(BASE_USAGE >= 0xA5 && COMMIT_USAGE > BASE_USAGE && COMMIT_USAGE <= 0xFF,
             "base-usage must be >= 0xA5 and below commit-usage (<= 0xFF)");
BUILD_ASSERT(!POSITIONS || BASE_USAGE >= ZLS_POS_LO + ZLS_POS_LO_N,
             "with positions, base-usage must be >= 0xC0 (0xA5..0xBF carry the positions)");
BUILD_ASSERT(ZMK_KEYMAP_LAYERS_LEN <= 31, "layer ids above 31 cannot be signalled");
BUILD_ASSERT(CONFIG_ZMK_HID_KEYBOARD_REPORT_SIZE >= 2,
             "the report must hold at least one layer usage and the commit usage");

/* Usages currently in the report (press phase) and how many. */
static uint8_t usages[CONFIG_ZMK_HID_KEYBOARD_REPORT_SIZE];
static int n_usages;
static bool pressed; /* usages are in the report; next work run releases them */
static bool dirty;   /* a layer change arrived since the last burst started */

static void work_cb(struct k_work *work);
static K_WORK_DELAYABLE_DEFINE(work, work_cb);

static void rearm_idle(void) {
    if (dirty) {
        k_work_reschedule(&work, K_MSEC(SETTLE_MS));
    } else if (HEARTBEAT_MS > 0) {
        k_work_reschedule(&work, K_MSEC(HEARTBEAT_MS));
    }
}

static void release_all(bool send) {
    for (int i = 0; i < n_usages; i++) {
        zmk_hid_keyboard_release(usages[i]);
    }
    n_usages = 0;
    pressed = false;
    if (send) {
        int err = zmk_endpoint_send_report(HID_USAGE_KEY);
        if (err < 0) {
            LOG_WRN("layer signal release report failed (%d)", err);
        }
    }
}

static void burst(void) {
    dirty = false;
    int n = zls_encode(zmk_keymap_layer_state(), BASE_USAGE, COMMIT_USAGE, usages,
                       ARRAY_SIZE(usages));
    if (n < 0) {
        LOG_WRN("layer signal skipped: active layers do not fit a %d-key report; raise "
                "CONFIG_ZMK_HID_KEYBOARD_REPORT_SIZE",
                CONFIG_ZMK_HID_KEYBOARD_REPORT_SIZE);
        return;
    }
    for (int i = 0; i < n; i++) {
        int err = zmk_hid_keyboard_press(usages[i]);
        if (err < 0) {
            /* Real keys already fill the report. Undo without sending: nothing
             * left the keyboard, and a report without the commit usage would be
             * ignored by the host anyway. Try again on the next change. */
            LOG_DBG("layer signal deferred: report full (%d)", err);
            n_usages = i;
            release_all(false);
            dirty = true;
            return;
        }
    }
    n_usages = n;
    pressed = true;
    int err = zmk_endpoint_send_report(HID_USAGE_KEY);
    if (err < 0) {
        LOG_WRN("layer signal report failed (%d)", err);
    }
    LOG_DBG("layer signal: %d usages, state 0x%08x", n, zmk_keymap_layer_state());
    k_work_reschedule(&work, K_MSEC(TAP_MS));
}

/* Any real key currently in the report? Hosts stop auto-repeating a held key when another key
 * event arrives, and Linux turns our usages into key events, so the heartbeat waits. */
static bool real_key_held(void) {
    const struct zmk_hid_keyboard_report *report = zmk_hid_get_keyboard_report();
    for (size_t i = 0; i < ARRAY_SIZE(report->body.keys); i++) {
        uint8_t k = report->body.keys[i];
        if (k != 0 && k < ZLS_POS_HI) {
            return true;
        }
    }
    return false;
}

static void work_cb(struct k_work *work_item) {
    ARG_UNUSED(work_item);
    if (pressed) {
        release_all(true);
        rearm_idle();
        return;
    }
    if (!dirty && real_key_held()) {
        k_work_reschedule(&work, K_MSEC(250)); /* heartbeat: try again once the key is up */
        return;
    }
    burst();
    if (!pressed) {
        rearm_idle();
    }
}

/* Key presses: each position goes out as a hi+lo usage pair in one report and is released in
 * the next. The pairs are sent from the system work queue, not from the key-press event
 * handler: a USB send can block for tens of milliseconds, which would delay the keymap's own
 * processing of the press (combo terms, tapping terms). The queue is drained one position at a
 * time, so no two positions ever share a report. */
#define POS_QUEUE_LEN 16
#define POS_RELEASE_FLAG 0x80000000u
static uint32_t pos_queue[POS_QUEUE_LEN];
static uint8_t pos_head, pos_tail; /* head: next to send; tail: next free */

static void pos_work_cb(struct k_work *work_item) {
    ARG_UNUSED(work_item);
    while (pos_head != pos_tail) {
        uint32_t entry = pos_queue[pos_head];
        pos_head = (pos_head + 1) % POS_QUEUE_LEN;
        bool released = (entry & POS_RELEASE_FLAG) != 0;
        uint8_t pair[2];
        if (!zls_encode_position(entry & ~POS_RELEASE_FLAG, pair)) {
            continue;
        }
        /* ZMK fills the report's key slots in press order, so the order of the two usages is
         * the press/release bit: hi then lo for a press, lo then hi for a release. */
        uint8_t first = released ? pair[1] : pair[0];
        uint8_t second = released ? pair[0] : pair[1];
        if (zmk_hid_keyboard_press(first) < 0) {
            continue; /* report full of real keys: skip this one */
        }
        if (zmk_hid_keyboard_press(second) < 0) {
            zmk_hid_keyboard_release(first);
            continue;
        }
        zmk_endpoint_send_report(HID_USAGE_KEY);
        zmk_hid_keyboard_release(pair[0]);
        zmk_hid_keyboard_release(pair[1]);
        zmk_endpoint_send_report(HID_USAGE_KEY);
    }
}
static K_WORK_DEFINE(pos_work, pos_work_cb);

static void announce_position(uint32_t position, bool released) {
    uint8_t next = (pos_tail + 1) % POS_QUEUE_LEN;
    if (next == pos_head) {
        return; /* queue full (host not draining reports): drop rather than stall the keyboard */
    }
    pos_queue[pos_tail] = position | (released ? POS_RELEASE_FLAG : 0);
    pos_tail = next;
    k_work_submit(&pos_work);
}

static int layer_signal_listener(const zmk_event_t *eh) {
    if (as_zmk_layer_state_changed(eh) != NULL || as_zmk_endpoint_changed(eh) != NULL) {
        dirty = true;
        /* Mid-burst the release run picks the change up (rearm_idle). */
        if (!pressed) {
            k_work_reschedule(&work, K_MSEC(SETTLE_MS));
        }
        return ZMK_EV_EVENT_BUBBLE;
    }
#if POSITIONS
    const struct zmk_position_state_changed *pos_ev = as_zmk_position_state_changed(eh);
    if (pos_ev != NULL) {
        announce_position(pos_ev->position, !pos_ev->state);
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
    /* Announce the boot state once the endpoints are up; the heartbeat, when
     * enabled, keeps repeating it. */
    dirty = true;
    k_work_reschedule(&work, K_MSEC(1000));
    LOG_DBG("layer signal: usages 0x%02x+id, commit 0x%02x, report size %d", BASE_USAGE,
            COMMIT_USAGE, CONFIG_ZMK_HID_KEYBOARD_REPORT_SIZE);
    return 0;
}

SYS_INIT(layer_signal_init, APPLICATION, CONFIG_APPLICATION_INIT_PRIORITY);

#endif /* DT_HAS_COMPAT_STATUS_OKAY */
