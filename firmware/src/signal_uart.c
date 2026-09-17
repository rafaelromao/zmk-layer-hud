/*
 * Copyright (c) 2026 Rafael Romão
 *
 * SPDX-License-Identifier: MIT
 *
 * USB carrier: the frames go out of a CDC-ACM serial interface the keyboard
 * exposes alongside its HID one. The host opens a /dev/ttyACM* (or the macOS
 * equivalent) and reads; nothing the keyboard sends here can reach the
 * compositor as a key, which is the entire reason this module stopped putting
 * its usages in the keyboard report.
 *
 * The UART comes from a devicetree chosen node, so the module does not care
 * which peripheral carries it. The snippet in snippets/layer-hud-usb-uart adds
 * a cdc-acm-uart under zephyr_udc0 and points the chosen at it -- the same
 * shape ZMK Studio uses for its own RPC transport.
 *
 * Writes go through a ring buffer drained by the TX interrupt. uart_poll_out
 * would have been shorter, but on CDC-ACM it blocks once the host stops
 * draining, and a blocking write in the key path is exactly the bug this
 * redesign exists to remove. A full buffer drops the frame instead.
 */

#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/init.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/ring_buffer.h>

#include "signal_transport.h"

LOG_MODULE_DECLARE(zmk, CONFIG_ZMK_LOG_LEVEL);

#define UART_NODE DT_CHOSEN(zmk_layer_hud_uart)

#if !DT_NODE_HAS_STATUS(UART_NODE, okay)
#error "zmk,layer-hud-uart chosen node missing or disabled: build with the " \
       "layer-hud-usb-uart snippet, or set the chosen yourself"
#endif

static const struct device *const uart_dev = DEVICE_DT_GET(UART_NODE);

RING_BUF_DECLARE(tx_ring, CONFIG_ZMK_LAYER_SIGNAL_UART_TX_BUF);

static void uart_cb(const struct device *dev, void *user_data) {
    ARG_UNUSED(user_data);

    if (!uart_irq_update(dev) || !uart_irq_is_pending(dev)) {
        return;
    }

    while (uart_irq_tx_ready(dev)) {
        uint8_t *data;
        uint32_t claimed = ring_buf_get_claim(&tx_ring, &data, tx_ring.size);
        if (claimed == 0) {
            uart_irq_tx_disable(dev);
            ring_buf_get_finish(&tx_ring, 0);
            break;
        }

        int sent = uart_fifo_fill(dev, data, (int)claimed);
        ring_buf_get_finish(&tx_ring, sent < 0 ? 0 : (uint32_t)sent);

        if (sent <= 0) {
            break;
        }
    }
}

void zls_uart_send(const uint8_t *frame, size_t len) {
    if (!device_is_ready(uart_dev)) {
        return;
    }

    /* All or nothing: half a frame in the stream would make the host resync on
     * the next magic and lose the one after it too. */
    if (ring_buf_space_get(&tx_ring) < len) {
        LOG_DBG("layer signal frame dropped: uart buffer full");
        return;
    }

    ring_buf_put(&tx_ring, frame, len);
    uart_irq_tx_enable(uart_dev);
}

static int zls_uart_init(void) {
    if (!device_is_ready(uart_dev)) {
        LOG_WRN("layer signal uart not ready");
        return -ENODEV;
    }

    uart_irq_rx_disable(uart_dev);
    uart_irq_tx_disable(uart_dev);
    uart_irq_callback_user_data_set(uart_dev, uart_cb, NULL);

    return 0;
}

SYS_INIT(zls_uart_init, APPLICATION, CONFIG_APPLICATION_INIT_PRIORITY);
