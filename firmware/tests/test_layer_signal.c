/*
 * Copyright (c) 2026 Rafael Romão
 * SPDX-License-Identifier: MIT
 *
 * Host-side unit tests for the pure encode/decode policy of the ZMK module.
 * Build and run:  make test-firmware
 *
 * The vectors below are duplicated in host/hudfeed_test.py; keep both in sync.
 */

#include "../src/layer_signal_policy.h"

#include <stdio.h>
#include <string.h>

static int failures;
static int checks;

#define BASE 0xC0
#define COMMIT 0xDF

static void check(bool ok, const char *what) {
    checks++;
    if (!ok) {
        failures++;
        printf("FAIL %s\n", what);
    }
}

static void eq_bytes(const uint8_t *got, int n, const uint8_t *want, int want_n, const char *what) {
    checks++;
    if (n != want_n || (n > 0 && memcmp(got, want, (size_t)n) != 0)) {
        failures++;
        printf("FAIL %s: got [", what);
        for (int i = 0; i < n; i++) {
            printf("%s0x%02x", i ? " " : "", got[i]);
        }
        printf("] want [");
        for (int i = 0; i < want_n; i++) {
            printf("%s0x%02x", i ? " " : "", want[i]);
        }
        printf("]\n");
    }
}

static void test_encode(void) {
    uint8_t out[12];
    int n;

    n = zls_encode(0, BASE, COMMIT, out, sizeof out);
    eq_bytes(out, n, (const uint8_t[]){COMMIT}, 1, "empty set -> commit only");

    n = zls_encode((1u << 2) | (1u << 14), BASE, COMMIT, out, sizeof out);
    eq_bytes(out, n, (const uint8_t[]){0xC2, 0xCE, COMMIT}, 3, "layers {2,14}");

    n = zls_encode((1u << 0) | (1u << 5), BASE, COMMIT, out, sizeof out);
    eq_bytes(out, n, (const uint8_t[]){0xC5, COMMIT}, 2, "layer 0 is never sent");

    /* Layer 30 -> 0xDE is the last representable id; 31 would be the commit usage. */
    n = zls_encode((1u << 30) | (1u << 31) | (1u << 1), BASE, COMMIT, out, sizeof out);
    eq_bytes(out, n, (const uint8_t[]){0xC1, 0xDE, COMMIT}, 3, "id 30 kept, id 31 (= commit) dropped");

    n = zls_encode((1u << 1) | (1u << 2) | (1u << 3), BASE, COMMIT, out, 3);
    check(n == -1, "three layers do not fit cap 3 (room for commit needed)");

    n = zls_encode((1u << 1) | (1u << 2), BASE, COMMIT, out, 3);
    eq_bytes(out, n, (const uint8_t[]){0xC1, 0xC2, COMMIT}, 3, "two layers fit cap 3 exactly");

    n = zls_encode(0, BASE, COMMIT, out, 0);
    check(n == -1, "cap 0 fails");

    n = zls_encode(0, COMMIT, BASE, out, sizeof out);
    check(n == -1, "commit below base fails");

    /* Full 12-slot report: 11 layers + commit. */
    uint32_t eleven = 0;
    for (unsigned l = 1; l <= 11; l++) {
        eleven |= 1u << l;
    }
    n = zls_encode(eleven, BASE, COMMIT, out, 12);
    check(n == 12 && out[11] == COMMIT && out[0] == 0xC1 && out[10] == 0xCB,
          "eleven layers fill a 12-slot report");
    n = zls_encode(eleven | (1u << 12), BASE, COMMIT, out, 12);
    check(n == -1, "twelve layers overflow a 12-slot report");
}

static void test_decode(void) {
    uint32_t layers = 0xFFFFFFFFu;

    check(!zls_decode((const uint8_t[]){0xC2, 0xCE, 0x00}, 3, BASE, COMMIT, &layers),
          "no commit -> not an announcement");
    check(layers == 0xFFFFFFFFu, "no commit leaves *layers untouched");

    check(zls_decode((const uint8_t[]){COMMIT, 0, 0, 0, 0, 0}, 6, BASE, COMMIT, &layers) &&
              layers == 0,
          "commit alone -> empty set");

    check(zls_decode((const uint8_t[]){0xC2, 0xCE, COMMIT, 0, 0, 0}, 6, BASE, COMMIT, &layers) &&
              layers == ((1u << 2) | (1u << 14)),
          "{2,14} decodes");

    /* Real keys and modifiers in the same report are ignored. */
    check(zls_decode((const uint8_t[]){0x04, 0xE1, 0xC5, COMMIT, 0x2C, 0}, 6, BASE, COMMIT,
                     &layers) &&
              layers == (1u << 5),
          "unrelated usages ignored");

    /* Commit in the first slot, layers after it: order does not matter. */
    check(zls_decode((const uint8_t[]){COMMIT, 0xC1}, 2, BASE, COMMIT, &layers) &&
              layers == (1u << 1),
          "order independent");

    /* base itself would be layer 0 and is not a layer usage. */
    check(zls_decode((const uint8_t[]){BASE, COMMIT}, 2, BASE, COMMIT, &layers) && layers == 0,
          "base usage alone means no layer");

    /* Round trip. */
    uint8_t out[12];
    uint32_t state = (1u << 2) | (1u << 10) | (1u << 22);
    int n = zls_encode(state, BASE, COMMIT, out, sizeof out);
    check(n == 4 && zls_decode(out, (size_t)n, BASE, COMMIT, &layers) && layers == state,
          "encode/decode round trip");
}

int main(void) {
    test_encode();
    test_decode();
    printf("%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
