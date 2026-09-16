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

#endif /* ZMK_LAYER_HUD_LAYER_SIGNAL_POLICY_H */
