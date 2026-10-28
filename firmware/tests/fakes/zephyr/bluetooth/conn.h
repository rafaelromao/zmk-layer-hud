/* Fake for the host tests: a connection is one host, and says what the module did with it. */
#pragma once

#include <zephyr/kernel.h>

struct bt_conn {
    bool subscribed; /* to the signal characteristic */
    int refs;        /* taken by zmk_ble_active_profile_conn, given back by bt_conn_unref */
    int notified;    /* frames sent to this host alone */
};

void bt_conn_unref(struct bt_conn *conn);
