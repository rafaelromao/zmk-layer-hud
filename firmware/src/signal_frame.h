/*
 * Copyright (c) 2026 Rafael Romão
 *
 * SPDX-License-Identifier: MIT
 *
 * The wire format between the keyboard and the HUD host.
 *
 * Deliberately free of Zephyr headers, like layer_signal_policy.h beside it, so
 * the encoder can be compiled and tested on the host and so this file alone is
 * the contract. host/hudfeed.py mirrors it; the vectors in
 * firmware/tests/test_layer_signal.c and host/hudfeed_test.py are the same
 * bytes, and changing one without the others will fail both.
 *
 *   A5 5A  ver  kind  len  payload...  crc8
 *
 * Two magic bytes because CDC-ACM is a byte stream with no message boundaries:
 * a reader that joins mid-frame, or drops bytes, resynchronises on them and the
 * CRC catches the case where payload bytes happen to look like a header. Over
 * GATT each notification already is one frame, but it carries the same framing
 * so the host has one decoder rather than two.
 *
 * Layers travel as a bitmap rather than a list: it is fixed width, it cannot
 * overflow a frame the way the old usage-per-layer encoding could overflow a
 * report, and "no layers" is a value rather than an absence.
 */

#ifndef ZMK_LAYER_HUD_SIGNAL_FRAME_H
#define ZMK_LAYER_HUD_SIGNAL_FRAME_H

#include <stddef.h>
#include <stdint.h>

#define ZLS_FRAME_MAGIC0 0xA5
#define ZLS_FRAME_MAGIC1 0x5A
#define ZLS_FRAME_VERSION 1

/* Frame kinds. A host that does not know a kind skips it by its length rather
 * than losing sync, so new kinds do not need a version bump. */
#define ZLS_KIND_LAYERS 0x01   /* payload: uint32 little-endian layer bitmap */
#define ZLS_KIND_POSITION 0x02 /* payload: uint8 position, uint8 non-zero if pressed */

#define ZLS_FRAME_HEADER_LEN 5 /* magic0 magic1 version kind len */
#define ZLS_FRAME_MAX_PAYLOAD 4
#define ZLS_FRAME_MAX_LEN (ZLS_FRAME_HEADER_LEN + ZLS_FRAME_MAX_PAYLOAD + 1)

/* CRC-8, polynomial 0x07, init 0x00. Written out rather than taken from
 * Zephyr's crc8() so the host can reproduce it from this file alone. */
static inline uint8_t zls_crc8(const uint8_t *data, size_t len) {
    uint8_t crc = 0;
    for (size_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; bit++) {
            crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x07) : (uint8_t)(crc << 1);
        }
    }
    return crc;
}

/* -> bytes written, or 0 when the payload does not fit. The CRC covers
 * everything from the version byte to the end of the payload: the magic is
 * excluded so a resynchronising reader can verify without knowing where the
 * frame it is holding actually began. */
static inline size_t zls_frame_encode(uint8_t kind, const uint8_t *payload, uint8_t payload_len,
                                      uint8_t *out, size_t out_len) {
    if (payload_len > ZLS_FRAME_MAX_PAYLOAD ||
        out_len < (size_t)(ZLS_FRAME_HEADER_LEN + payload_len + 1)) {
        return 0;
    }

    out[0] = ZLS_FRAME_MAGIC0;
    out[1] = ZLS_FRAME_MAGIC1;
    out[2] = ZLS_FRAME_VERSION;
    out[3] = kind;
    out[4] = payload_len;
    for (uint8_t i = 0; i < payload_len; i++) {
        out[ZLS_FRAME_HEADER_LEN + i] = payload[i];
    }
    out[ZLS_FRAME_HEADER_LEN + payload_len] =
        zls_crc8(out + 2, (size_t)(ZLS_FRAME_HEADER_LEN - 2 + payload_len));

    return (size_t)(ZLS_FRAME_HEADER_LEN + payload_len + 1);
}

static inline size_t zls_frame_layers(uint32_t layer_state, uint8_t *out, size_t out_len) {
    const uint8_t payload[4] = {
        (uint8_t)(layer_state & 0xff),
        (uint8_t)((layer_state >> 8) & 0xff),
        (uint8_t)((layer_state >> 16) & 0xff),
        (uint8_t)((layer_state >> 24) & 0xff),
    };
    return zls_frame_encode(ZLS_KIND_LAYERS, payload, sizeof(payload), out, out_len);
}

static inline size_t zls_frame_position(uint8_t position, int pressed, uint8_t *out,
                                        size_t out_len) {
    const uint8_t payload[2] = {position, (uint8_t)(pressed ? 1 : 0)};
    return zls_frame_encode(ZLS_KIND_POSITION, payload, sizeof(payload), out, out_len);
}

#endif /* ZMK_LAYER_HUD_SIGNAL_FRAME_H */
