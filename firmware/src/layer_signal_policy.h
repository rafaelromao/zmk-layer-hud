/*
 * Copyright (c) 2026 Rafael Romão
 *
 * SPDX-License-Identifier: MIT
 *
 * Pure encode/decode of the layer signal: no Zephyr, no ZMK, so it is unit
 * tested on the host (firmware/tests/test_layer_signal.c) and mirrored by the
 * Python decoder in host/hudfeed.py with the same test vectors.
 *
 * Wire format: inside one keyboard HID report, usage (base + L) for every
 * active layer id L >= 1, plus `commit`. A report is a layer-set announcement
 * if and only if it contains `commit`.
 */

#ifndef ZMK_LAYER_HUD_LAYER_SIGNAL_POLICY_H
#define ZMK_LAYER_HUD_LAYER_SIGNAL_POLICY_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Fill `out` with the usages to put in the report for `layer_state` (bit N =
 * layer id N, bit 0 ignored), commit last. Returns the count, or -1 when the
 * set plus the commit usage does not fit `cap`: the caller must then skip the
 * burst rather than send a truncated set. Layers whose usage would reach
 * `commit` are not representable and are dropped. */
static inline int zls_encode(uint32_t layer_state, uint8_t base, uint8_t commit, uint8_t *out,
                             size_t cap) {
    if (cap < 1 || commit <= base) {
        return -1;
    }
    size_t n = 0;
    for (unsigned l = 1; l < 32; l++) {
        if ((layer_state & (1u << l)) == 0) {
            continue;
        }
        unsigned usage = (unsigned)base + l;
        if (usage >= commit || usage > 0xFF) {
            continue;
        }
        if (n + 1 >= cap) {
            return -1; /* no room left for the commit usage */
        }
        out[n++] = (uint8_t)usage;
    }
    out[n++] = commit;
    return (int)n;
}

/* Decode the key bytes of one keyboard report. Returns true and sets *layers
 * (bit N = layer id N) only when `commit` is present; every other report is
 * not a layer announcement and must be ignored. Unrelated usages (real keys,
 * modifiers, zero padding) are skipped. */
static inline bool zls_decode(const uint8_t *keys, size_t n, uint8_t base, uint8_t commit,
                              uint32_t *layers) {
    bool has_commit = false;
    uint32_t acc = 0;
    for (size_t i = 0; i < n; i++) {
        uint8_t k = keys[i];
        if (k == commit) {
            has_commit = true;
        } else if (k > base && k < commit && (unsigned)(k - base) < 32) {
            acc |= 1u << (k - base);
        }
    }
    if (!has_commit) {
        return false;
    }
    *layers = acc;
    return true;
}

/* Key positions ride on the same wire, below the layer usages: one usage from the "hi" range
 * (0xA5..0xB5, 17 values) plus one from the "lo" range (0xB8..0xBF, 8 values) in the same
 * report encode position = hi * 8 + lo, so up to 136 keys. The pair is pressed and released
 * within one event, so a report never carries more than one position.
 *
 * 0xB6 and 0xB7 are deliberately unused: Linux maps them to Keypad ( and ), the only two
 * usages in 0xA5..0xDF that any OS turns into a printable key. */
#define ZLS_POS_HI 0xA5
#define ZLS_POS_HI_N 17
#define ZLS_POS_LO 0xB8
#define ZLS_POS_LO_N 8
#define ZLS_POS_MAX (ZLS_POS_HI_N * ZLS_POS_LO_N)

static inline bool zls_encode_position(uint32_t pos, uint8_t out[2]) {
    if (pos >= ZLS_POS_MAX) {
        return false;
    }
    out[0] = (uint8_t)(ZLS_POS_HI + pos / ZLS_POS_LO_N);
    out[1] = (uint8_t)(ZLS_POS_LO + pos % ZLS_POS_LO_N);
    return true;
}

/* Returns true and sets *pos when the key bytes hold exactly one hi and one lo usage. */
static inline bool zls_decode_position(const uint8_t *keys, size_t n, uint32_t *pos) {
    int hi = -1, lo = -1, n_hi = 0, n_lo = 0;
    for (size_t i = 0; i < n; i++) {
        uint8_t k = keys[i];
        if (k >= ZLS_POS_HI && k < ZLS_POS_HI + ZLS_POS_HI_N) {
            hi = k - ZLS_POS_HI;
            n_hi++;
        } else if (k >= ZLS_POS_LO && k < ZLS_POS_LO + ZLS_POS_LO_N) {
            lo = k - ZLS_POS_LO;
            n_lo++;
        }
    }
    if (n_hi != 1 || n_lo != 1) {
        return false;
    }
    *pos = (uint32_t)hi * ZLS_POS_LO_N + (uint32_t)lo;
    return true;
}

#endif /* ZMK_LAYER_HUD_LAYER_SIGNAL_POLICY_H */
