/*
 * Copyright (c) 2026 Rafael Romão
 * SPDX-License-Identifier: MIT
 *
 * Host-side tests for where the signal goes: to the host the keyboard is typing to, and to no
 * other. Build and run:  make test-firmware
 *
 * ZMK stays connected to the hosts bonded on its other BLE profiles, and a HUD on any of them may
 * subscribe. The signal used to be notified to every subscriber, and the USB port carried it
 * whatever the keyboard typed to: a Mac on one profile drew the key positions of what was being
 * typed into an Omarchy laptop on another, while its strip stayed empty -- no key report came,
 * because ZMK sends those to the selected profile alone. The positions are the typing, and the
 * HUD on the host that is not being typed to cannot see a password field on the other one.
 *
 * signal_gatt.c and signal_transport.h are compiled as they are, against the fakes in fakes/,
 * which stand in for just enough of Zephyr and ZMK and record what the module did.
 */

#define CONFIG_ZMK_LAYER_SIGNAL_UART 1
#define CONFIG_ZMK_LAYER_SIGNAL_GATT 1

#include <zephyr/bluetooth/conn.h>
#include <zmk/ble.h>
#include <zmk/endpoints.h>

#include "../src/signal_gatt.c"

#include <stdio.h>
#include <string.h>

static int failures;
static int checks;

static void check(bool ok, const char *what) {
    checks++;
    if (!ok) {
        failures++;
        printf("FAIL %s\n", what);
    }
}

/* ---- ZMK and Zephyr as each test sets them up ---- */

static struct zmk_endpoint_instance selected;
static struct bt_conn *active; /* the selected profile's host; NULL while it is not connected */
static int notified_all;       /* frames sent with a NULL connection: to every subscribed host */
static int uart_frames;        /* frames written to the CDC-ACM port: the machine on the cable */

struct zmk_endpoint_instance zmk_endpoint_get_selected(void) { return selected; }

struct bt_conn *zmk_ble_active_profile_conn(void) {
    if (active != NULL) {
        active->refs++;
    }
    return active;
}

void bt_conn_unref(struct bt_conn *conn) { conn->refs--; }

int bt_gatt_notify(struct bt_conn *conn, const struct bt_gatt_attr *attr, const void *data,
                   uint16_t len) {
    ARG_UNUSED(attr);
    ARG_UNUSED(data);
    ARG_UNUSED(len);
    if (conn == NULL) {
        notified_all++;
    } else {
        conn->notified++;
    }
    return 0;
}

bool bt_gatt_is_subscribed(struct bt_conn *conn, const struct bt_gatt_attr *attr,
                           uint16_t ccc_type) {
    ARG_UNUSED(attr);
    return conn->subscribed && ccc_type == BT_GATT_CCC_NOTIFY;
}

void zls_uart_send(const uint8_t *frame, size_t len) {
    ARG_UNUSED(frame);
    ARG_UNUSED(len);
    uart_frames++;
}

/* Two bonded hosts, both connected: the Mac and the laptop on another profile. */
static struct bt_conn mac, laptop;

static void typing_to(enum zmk_transport transport, struct bt_conn *host) {
    memset(&mac, 0, sizeof mac);
    memset(&laptop, 0, sizeof laptop);
    selected = (struct zmk_endpoint_instance){.transport = transport};
    active = host;
    notified_all = 0;
    uart_frames = 0;
}

/* A HUD on `host` subscribes: what Zephyr does is tell the CCC's callback someone has. */
static void subscribe(struct bt_conn *host) {
    host->subscribed = true;
    for (size_t i = 0; i < 4; i++) {
        if (layer_signal_svc.attrs[i].ccc_changed != NULL) {
            layer_signal_svc.attrs[i].ccc_changed(&layer_signal_svc.attrs[i], BT_GATT_CCC_NOTIFY);
        }
    }
}

static const uint8_t frame[] = {0xA5, 0x5A, 0x01, 0x01, 0x04, 0x00, 0x00, 0x00, 0x00, 0x00};

static void test_ble_goes_to_the_selected_profile_alone(void) {
    typing_to(ZMK_TRANSPORT_BLE, &mac);
    subscribe(&mac);
    subscribe(&laptop);
    zls_transport_send(frame, sizeof frame);
    check(mac.notified == 1, "typing over BLE to the Mac: the Mac gets the frame");
    check(laptop.notified == 0 && notified_all == 0,
          "typing over BLE to the Mac: a laptop on another profile gets nothing");
    check(uart_frames == 0, "typing over BLE: the machine on the cable gets nothing");
    check(mac.refs == 0, "the connection ZMK lent is given back");
}

static void test_a_host_without_a_hud_is_not_sent_to(void) {
    /* The laptop runs the HUD, the Mac being typed to does not: nobody gets anything. */
    typing_to(ZMK_TRANSPORT_BLE, &mac);
    subscribe(&laptop);
    zls_transport_send(frame, sizeof frame);
    check(mac.notified == 0 && laptop.notified == 0 && notified_all == 0,
          "the host typed to has not subscribed: the frame goes nowhere");
    check(mac.refs == 0, "the connection ZMK lent is given back when nothing is sent");
}

static void test_usb_goes_to_the_cable_alone(void) {
    /* Plugged into the Mac and paired with it too: the HUD reads the port, and the copy over BLE
     * was the position told twice (9b0cbbb). */
    typing_to(ZMK_TRANSPORT_USB, NULL);
    subscribe(&mac);
    subscribe(&laptop);
    zls_transport_send(frame, sizeof frame);
    check(uart_frames == 1, "typing over USB: the machine on the cable gets the frame");
    check(mac.notified == 0 && laptop.notified == 0 && notified_all == 0,
          "typing over USB: no BLE host gets it");
}

static void test_typing_nowhere_sends_nothing(void) {
    typing_to(ZMK_TRANSPORT_NONE, NULL);
    subscribe(&mac);
    zls_transport_send(frame, sizeof frame);
    check(uart_frames == 0 && mac.notified == 0 && notified_all == 0,
          "no endpoint selected: nothing goes anywhere");
}

static void test_the_selected_host_gone_sends_nothing(void) {
    /* BLE still selected for an instant after its host went: there is no one to send to, and
     * every other subscriber is still not the one being typed to. */
    typing_to(ZMK_TRANSPORT_BLE, NULL);
    subscribe(&laptop);
    zls_transport_send(frame, sizeof frame);
    check(laptop.notified == 0 && notified_all == 0,
          "the selected profile's host is gone: nothing goes anywhere");
}

int main(void) {
    test_ble_goes_to_the_selected_profile_alone();
    test_a_host_without_a_hud_is_not_sent_to();
    test_usb_goes_to_the_cable_alone();
    test_typing_nowhere_sends_nothing();
    test_the_selected_host_gone_sends_nothing();
    printf("%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
