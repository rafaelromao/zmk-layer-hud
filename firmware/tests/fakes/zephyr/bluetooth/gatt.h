/* Fake for the host tests: Zephyr's GATT, as far as signal_gatt.c uses it. */
#pragma once

#include <zephyr/bluetooth/conn.h>

#define BT_GATT_CHRC_NOTIFY 0x10
#define BT_GATT_PERM_NONE 0
#define BT_GATT_PERM_READ_ENCRYPT 0x04
#define BT_GATT_PERM_WRITE_ENCRYPT 0x08
#define BT_GATT_CCC_NOTIFY 0x0001

struct bt_gatt_attr {
    const void *uuid;
    void (*ccc_changed)(const struct bt_gatt_attr *attr, uint16_t value);
};

struct bt_gatt_service_static {
    struct bt_gatt_attr *attrs;
};

#define BT_GATT_SERVICE_DEFINE(name, ...)                                                          \
    static struct bt_gatt_attr name##_attrs[] = {__VA_ARGS__};                                     \
    static const struct bt_gatt_service_static name = {.attrs = name##_attrs}
#define BT_GATT_PRIMARY_SERVICE(_uuid) {.uuid = (_uuid)}
/* Two attributes, as in Zephyr: the declaration, then the value. */
#define BT_GATT_CHARACTERISTIC(_uuid, _props, _perm, _read, _write, _value)                       \
    {.uuid = NULL}, {.uuid = (_uuid)}
#define BT_GATT_CCC(_changed, _perm) {.ccc_changed = (_changed)}

/* A NULL connection is every subscribed host, as in Zephyr. */
int bt_gatt_notify(struct bt_conn *conn, const struct bt_gatt_attr *attr, const void *data,
                   uint16_t len);
bool bt_gatt_is_subscribed(struct bt_conn *conn, const struct bt_gatt_attr *attr,
                           uint16_t ccc_type);
