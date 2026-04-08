# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Towerwatch is an Arduino Uno R3 firmware that monitors 5G connection quality (latency, jitter, packet loss) via TCP probes and pushes metrics to Grafana Cloud. It buffers data to SD card during outages and flushes when connectivity returns.

**Target hardware:** Arduino Uno R3 (ATmega328P, 2KB RAM, 32KB flash) + W5100/W5500 Ethernet Shield + SPI SD card module.

## Build and Upload

This is an Arduino sketch — build and upload via **Arduino IDE**:

1. Open `towerwatch.ino` (File → Open) — all `.h`/`.cpp` tabs load automatically
2. Board: **Tools → Board → Arduino AVR Boards → Arduino Uno**
3. Port: **Tools → Port → COM?** (whichever port the USB-connected Arduino appears on)
4. Verify (compile): click **✓** button
5. Upload: click **→** button

No external library dependencies — only built-in Arduino libraries (`Ethernet`, `SD`, `SPI`).

There is no test framework or linter configured for this project.

## Architecture

The main loop in `towerwatch.ino` runs a 30-second cycle:

1. **`network_test`** — Opens TCP connections to 8.8.8.8:53 (configurable in `config.h`), runs probe bursts, computes RTT stats and packet loss into a `PingResult` struct
2. **`connection_state`** — State machine tracking up/down transitions and outage durations
3. **`storage`** — Appends CSV rows to `metrics.csv` on SD card; provides read/flush for the push buffer
4. **`metrics_push`** — Reads buffered CSV rows from SD, formats as Influx line protocol, POSTs to Grafana Cloud over HTTPS

Each module follows the pattern: `*Init()` called once in `setup()`, then per-cycle functions called in `loop()`.

## Key Constraints

- **2KB RAM limit** — All string literals must use `F()` macro. No `String` class — only `char[]` buffers. Constant data uses `PROGMEM`. Current usage: ~1280 bytes (62%).
- **SPI bus sharing** — SD card (CS pin 4) and Ethernet shield (CS pin 10) share the SPI bus; only one can be active at a time.
- **Watchdog timer** — 8-second WDT is enabled after DHCP completes. All blocking operations must call `wdt_reset()` periodically.
- **No RTC** — Timestamps are estimated from `BOOT_TIMESTAMP` + `millis()`. Must be updated before each deployment flash.

## Configuration

- `config.h` — All tuneable constants (network settings, test parameters, Grafana endpoint, intervals, pin assignments)
- `secrets.h` — **Gitignored**. Contains base64-encoded Grafana Cloud credentials in `PROGMEM`. Must be created manually per machine (see README for format).

## Serial Debugging

Set `DEBUG_SERIAL 1` in `config.h` (default). Monitor at 115200 baud. Set to `0` to save RAM in production.
