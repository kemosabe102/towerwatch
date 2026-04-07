# Towerwatch — 5G Cell Tower Network Quality Monitor

Continuously monitors latency, jitter, and packet loss on your 5G connection to build an evidence dataset for your cellular provider.

## Hardware

| Component | Notes |
|---|---|
| Arduino Uno R3 | ATmega328P, 2KB RAM, 32KB flash |
| Ethernet Shield | W5100 or W5500, stacks directly on top of Uno — no wiring needed |
| SD Card Module | SPI breakout (~$2-5), with any microSD card >= 1GB, wiped/formatted FAT32 |
| Ethernet Cable | Cat5e or Cat6, any length. Connects Ethernet shield to router |
| USB Wall Charger | Any 5V/1A+ phone charger. Powers the Arduino at the deployment location |
| USB-A to USB-B cable | The square "printer-style" connector — plugs into the Arduino Uno |

### Router Notes — Netgear Nighthawk M6

The Ethernet port on the M6 is **disabled by default**. Before deploying:

1. Connect to the M6's WiFi on your phone or laptop
2. Go to `192.168.1.1` in a browser
3. Navigate to Advanced Settings and enable the Ethernet port
4. Plug the M6 into wall power (USB-C) and enable Plugged-In Mode so it runs off the outlet instead of battery

---

## Wiring

### Ethernet Shield
Stacks directly onto the Uno — no wiring needed. Uses:
- **Pin 10**: Ethernet chip select (CS)
- **Pins 11, 12, 13**: SPI bus (shared with SD module)

### SD Card Module
Connect to the Arduino with a separate chip select pin:

| SD Module Pin | Arduino Pin |
|---|---|
| CS | **4** |
| MOSI | 11 (shared with Ethernet) |
| MISO | 12 (shared with Ethernet) |
| SCK | 13 (shared with Ethernet) |
| VCC | 5V |
| GND | GND |

---

## First-Time Setup (do this once at home)

### 1. Install Arduino IDE
Download from [arduino.cc](https://www.arduino.cc/en/software). Install with default options.

### 2. No extra libraries needed
The sketch uses only built-in Arduino libraries (`Ethernet`, `SD`, `SPI`). No Library Manager installs required.

### 3. Clone the repo
```bash
git clone git@github.com:kemosabe102/towerwatch.git
```

### 4. Open the sketch
File → Open → navigate to `towerwatch.ino`. Multiple tabs will appear — that's normal.

### 5. Set your board and port
- **Tools → Board → Arduino AVR Boards → Arduino Uno**
- **Tools → Port → COM?** (plug in the Arduino via USB first — it will appear automatically)

### 6. Pre-compile (warms up the cache for fast uploads later)
Click the **✓ (Verify)** button. This compiles everything once so future uploads only recompile the changed file. Takes 30-60 seconds the first time.

### 7. Set up Grafana credentials
Credentials are stored in `secrets.h` which is gitignored and never committed. You must create this file locally on any machine you use to flash the device.

Create `secrets.h` in the project folder with this content:
```cpp
#ifndef SECRETS_H
#define SECRETS_H

#include <avr/pgmspace.h>

// Generate with: echo -n 'YOUR_USER_ID:YOUR_API_KEY' | base64
const char GRAFANA_BASIC_AUTH[] PROGMEM = "YOUR_BASE64_HERE";

#endif
```

To generate the base64 value:
```bash
echo -n '3009582:YOUR_API_KEY' | base64
```

Replace `YOUR_API_KEY` with your Grafana Cloud API token (MetricsPublisher role).
Your instance ID is `3009582` and your metrics endpoint is:
`https://prometheus-prod-67-prod-us-west-0.grafana.net`

---

## Deploying to the Remote Location

Bring with you:
- The Arduino (already flashed from home is fine)
- Laptop with this repo cloned and Arduino IDE installed
- Ethernet cable
- USB wall charger + USB-A to USB-B cable
- The SD card (inserted into the SD module)

### Step 1 — Update the timestamp

The Arduino has no real-time clock. Before each power-on deployment, update `BOOT_TIMESTAMP` in `towerwatch.ino` line 28 to the current Unix time so Grafana timestamps are accurate.

Get the current Unix time:
```bash
date +%s
```

Add ~30 seconds to account for compile and upload time. Then update line 28:
```cpp
#define BOOT_TIMESTAMP 1744XXXXXXUL   // paste your value here
```

### Step 2 — Upload

1. Plug Arduino into laptop via USB
2. Confirm board is **Arduino Uno** and port is correct (Tools menu)
3. Click the **→ (Upload)** button
4. Wait ~20-30 seconds for upload to complete

### Step 3 — Verify via Serial Monitor (optional but recommended)

Tools → Serial Monitor → set baud rate to **115200**

You should see:
```
=== Towerwatch ===
Initializing...
Ethernet init...
IP: 192.168.x.x
SD init OK
Ready. Monitoring...
Interval: 30s
```

Then every 30 seconds:
```
--- Cycle t=1744XXXXXX ---
RTT avg=45 min=32 max=67 jitter=35 loss=0%
Pushed 1 rows
Outages: 0 Total downtime: 0s
Buffer: 0 bytes
```

If you see the IP address and `SD init OK`, everything is working. Close the Serial Monitor.

### Step 4 — Deploy

1. Unplug USB from laptop
2. Plug in USB wall charger for power
3. Plug in Ethernet cable to shield and to M6 router
4. The device starts monitoring automatically — no button press needed

---

## Timestamp Accuracy

The Uno has no battery-backed clock. Every time it powers on, it starts counting from `BOOT_TIMESTAMP`. This means:

- If you re-flash right before deploying (Step 1 above), timestamps are accurate to within ~30 seconds
- If you power-cycle the device without re-flashing, timestamps reset to `BOOT_TIMESTAMP` again — they will be behind by however long the device was off
- Timestamps drift ~1-2 seconds per day while running
- For a future upgrade, a DS3231 RTC module (~$2) eliminates all of this

**Rule of thumb:** Re-flash with a fresh timestamp any time you power-cycle the device at the deployment location.

---

## How It Works

```
Every 30 seconds:
  1. Open TCP connections to 8.8.8.8:53 (Google DNS) — 10 probes
  2. Compute avg/min/max RTT, jitter, packet loss from probe results
  3. Write CSV row to SD card
  4. If connected, POST buffered rows to Grafana Cloud
  5. On successful push, flush SD buffer
  6. Track connection state transitions (outage start/end)
```

### During Outages
When the connection is down, metrics buffer to the SD card as CSV. At ~60 bytes/row and one row per 30 seconds, a 1GB SD card holds years of data. When connectivity returns, buffered data is pushed to Grafana Cloud automatically.

### Metrics Collected
| Field | Description |
|---|---|
| `rtt_avg` | Average round-trip time in ms |
| `rtt_min` | Minimum RTT in ms |
| `rtt_max` | Maximum RTT in ms |
| `jitter` | RTT variance (max - min) in ms |
| `pkt_loss` | Packet loss percentage (0–100) |
| `connected` | 1 = up, 0 = down |

---

## Verification Checklist

| Check | What to look for |
|---|---|
| Serial Monitor | `IP: x.x.x.x` and `SD init OK` at boot |
| Cycle output | RTT/jitter/loss values every 30s |
| SD card | Remove card, open `metrics.csv` on a computer — rows with timestamp and metrics |
| Grafana Explore | Query measurement `towerwatch` — data points appearing |
| Outage test | Unplug Ethernet → see buffering messages → reconnect → see flush |

---

## Secrets and Credentials

`secrets.h` is gitignored and must be created manually on each machine used to flash the device. It contains the base64-encoded Grafana credentials. See **First-Time Setup → Step 7** above.

To rotate credentials:
1. Log in to [grafana.com](https://grafana.com) → your stack → Access Policies or API Keys
2. Delete the old key, create a new one with **MetricsPublisher** role
3. Re-encode: `echo -n '3009582:YOUR_NEW_API_KEY' | base64`
4. Update `secrets.h` with the new value

---

## File Structure

```
towerwatch/
├── towerwatch.ino          # Main sketch: setup(), loop()
├── config.h                # All configurable constants
├── secrets.h               # Gitignored — Grafana credentials (create locally)
├── network_test.h/.cpp     # TCP probe + metric computation
├── storage.h/.cpp          # SD card CSV read/write/flush
├── metrics_push.h/.cpp     # Grafana Cloud HTTP push
├── connection_state.h/.cpp # Up/down state machine
└── README.md               # This file
```

---

## Memory Budget

The sketch is designed to fit within the Uno's 2048 bytes of RAM:
- All string literals use `F()` macro (stored in flash, not RAM)
- No `String` class — only `char[]` buffers
- SD and Ethernet share SPI bus; only one active at a time
- Constant data uses `PROGMEM`

Current usage: **29758 bytes flash (92%)**, **1280 bytes RAM (62%)**

---

## Future Upgrades

- **DS3231 RTC module** (~$2): Accurate timestamps, survives power cycles
- **ESP32**: Same code, adds WiFi (no Ethernet cable), more RAM for throughput testing
- **Grafana dashboard template**: Pre-built dashboard JSON for all metrics
- **Alerting**: Grafana alerts when packet loss > 5% or latency > 200ms
- **Multi-target probing**: Probe router + DNS + remote server to isolate tower vs. internet issues
