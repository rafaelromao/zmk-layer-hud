/* Fake for the host tests (firmware/tests/test_signal_route.c): just enough of Zephyr. */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define ARG_UNUSED(x) (void)(x)

/* The test defines the CONFIG_ symbols it builds with, to 1. */
#define IS_ENABLED(config) (config)
