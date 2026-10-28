/*
 * Copyright (c) 2026 Rafael Romão
 *
 * SPDX-License-Identifier: MIT
 *
 * BLE carrier: the frames go out as notifications on a service of our own,
 * sitting beside ZMK's HID-over-GATT rather than inside it. A host that does
 * not subscribe never sees them, and nothing here can be mistaken for a key,
 * which is the point.
 *
 * Same shape as ZMK Studio's gatt_rpc_transport.c: a primary service with one
 * notify characteristic, encryption required on the CCC so the signal only
 * flows to a bonded host.
 *
 * And only to the selected profile's host, the one ZMK sends its key reports
 * to (hog.c asks zmk_ble_active_profile_conn the same thing). ZMK stays
 * connected to the hosts bonded on its other profiles, and a HUD on any of them
 * may subscribe: a NULL connection, as this used to send with, notifies every
 * subscriber, and a Mac on one profile drew the key positions of what was
 * being typed into a laptop on another.
 *
 * bt_gatt_notify returns an error rather than waiting when the stack has no
 * buffer, which is the behaviour this module wants: a dropped frame costs one
 * stale HUD update, and the next layer change or heartbeat corrects it.
 */

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zmk/ble.h>

#include "signal_transport.h"

LOG_MODULE_DECLARE(zmk, CONFIG_ZMK_LOG_LEVEL);

/* d1f0a7c2-6b3e-4f8a-9c21-5e7b4a0d9f31 service
 * d1f0a7c3-6b3e-4f8a-9c21-5e7b4a0d9f31 signal characteristic
 * The host asks for these two: BLE_SERVICE_UUID and BLE_SIGNAL_UUID in host/hudfeed.py, which
 * host/gatt_uuid_test.py holds equal to the macros below. */
#define ZLS_BT_SERVICE_UUID BT_UUID_128_ENCODE(0xd1f0a7c2, 0x6b3e, 0x4f8a, 0x9c21, 0x5e7b4a0d9f31)
#define ZLS_BT_SIGNAL_UUID BT_UUID_128_ENCODE(0xd1f0a7c3, 0x6b3e, 0x4f8a, 0x9c21, 0x5e7b4a0d9f31)

static bool notify_enabled;

static void signal_ccc_changed(const struct bt_gatt_attr *attr, uint16_t value) {
    ARG_UNUSED(attr);
    notify_enabled = (value == BT_GATT_CCC_NOTIFY);
    LOG_DBG("layer signal notifications %s", notify_enabled ? "on" : "off");
}

BT_GATT_SERVICE_DEFINE(layer_signal_svc,
                       BT_GATT_PRIMARY_SERVICE(BT_UUID_DECLARE_128(ZLS_BT_SERVICE_UUID)),
                       BT_GATT_CHARACTERISTIC(BT_UUID_DECLARE_128(ZLS_BT_SIGNAL_UUID),
                                              BT_GATT_CHRC_NOTIFY, BT_GATT_PERM_NONE, NULL, NULL,
                                              NULL),
                       BT_GATT_CCC(signal_ccc_changed,
                                   BT_GATT_PERM_READ_ENCRYPT | BT_GATT_PERM_WRITE_ENCRYPT));

void zls_gatt_send(const uint8_t *frame, size_t len) {
    if (!notify_enabled) {
        return; /* no host subscribed at all: nobody to look up */
    }

    struct bt_conn *conn = zmk_ble_active_profile_conn();
    if (conn == NULL) {
        return;
    }

    /* attrs[1] is the characteristic's declaration (attrs[0] is the service's); bt_gatt_notify
     * and bt_gatt_is_subscribed both take it for the value that follows. Asked first, because
     * the host typed to need not run the HUD while another one does, and notify_enabled only
     * says that someone subscribed. */
    if (bt_gatt_is_subscribed(conn, &layer_signal_svc.attrs[1], BT_GATT_CCC_NOTIFY)) {
        int err = bt_gatt_notify(conn, &layer_signal_svc.attrs[1], frame, len);
        if (err < 0) {
            LOG_DBG("layer signal frame dropped: notify failed (%d)", err);
        }
    }
    bt_conn_unref(conn);
}
