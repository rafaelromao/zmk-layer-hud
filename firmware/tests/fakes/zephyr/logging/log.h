/* Fake for the host tests: logging compiles to nothing. */
#pragma once

#define LOG_MODULE_DECLARE(...) extern int zls_fake_log_module
#define LOG_DBG(...) ((void)0)
