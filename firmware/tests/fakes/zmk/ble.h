/* Fake for the host tests: ZMK's BLE profiles, as in app/include/zmk/ble.h. */
#pragma once

#include <zephyr/bluetooth/conn.h>

/* The selected profile's host with a reference taken, or NULL when it is not connected. */
struct bt_conn *zmk_ble_active_profile_conn(void);
