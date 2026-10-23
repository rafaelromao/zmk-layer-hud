/* Fake for the host tests: ZMK's endpoints, as in app/include/zmk/endpoints{,_types}.h. */
#pragma once

#include <zephyr/kernel.h>

enum zmk_transport {
    ZMK_TRANSPORT_NONE = 0,
    ZMK_TRANSPORT_USB = 1,
    ZMK_TRANSPORT_BLE = 2,
};

struct zmk_endpoint_instance {
    enum zmk_transport transport;
    union {
        struct {
            int profile_index;
        } ble;
    };
};

struct zmk_endpoint_instance zmk_endpoint_get_selected(void);
