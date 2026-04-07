# Towerwatch — 5G Cell Tower Network Quality Monitor

Continuously monitors latency, jitter, and packet loss on your 5G connection to build an evidence dataset for your cellular provider.

## Hardware

| Component | Notes |
|---|---|
| Arduino Uno R3 | ATmega328P, 2KB RAM, 32KB flash |
| Ethernet Shield | W5100 or W5500, stacks on top of Uno |
| SD Card Module | SPI breakout (~$2-5), with any microSD card >= 1GB |
| Ethernet Cable | Connects Ethernet shield to 5G router/hotspot |

## Wiring

### Ethernet Shield
The Ethernet shield stacks directly onto the Uno — no wiring needed. It uses:
- **Pin 10**: Ethernet chip select (CS)
- **Pin 11**: SPI MOSI
- **Pin 12**: SPI MISO
- **Pin 13**: SPI SCK

### SD Card Module
Connect the SD card module to the **remaining SPI pins** with a separate chip select:

| SD Module Pin | Arduino Pin |
|---|---|
| CS | **4** (configurable in `config.h`) |
| MOSI | 11 (shared with Ethernet) |
| MISO | 12 (shared with Ethernet) |
| SCK | 13 (shared with Ethernet) |
| VCC | 5V |
| GND | GND |

Both the Ethernet shield and SD module share the SPI bus. They are selected individually via their CS pins (10 for Ethernet, 4 for SD).

## Setup

### 1. Install Libraries

In the Arduino IDE, install via Library Manager (`Sketch > Include Library > Manage Libraries`):

- **ICMPPing** by Blake Foster — search for "ICMPPing"

The following are built-in and require no installation:
- `Ethernet`
- `SD`
- `SPI`

### 2. Configure `config.h`

Edit `config.h` to match your environment:

```cpp
// Network: DHCP (default) or static IP
#define USE_DHCP 1

// Ping targets
#define PING_TARGET_PRIMARY   8, 8, 8, 8
#define PING_TARGET_SECONDARY 1, 1, 1, 1

// Grafana Cloud credentials
const char GRAFANA_HOST[] PROGMEM = "influx-prod-us-central-0.grafana.net";
const char GRAFANA_API_KEY[] PROGMEM = "YOUR_API_KEY_HERE";
```

### 3. Set Boot Timestamp

The Uno has no real-time clock. Before flashing, update `BOOT_TIMESTAMP` in `towerwatch.ino` to the current Unix epoch time:

```bash
date +%s
```

Paste the output as:
```cpp
#define BOOT_TIMESTAMP 1709312400UL
```

Timestamps will drift slightly over time (~1-2 seconds/day). For precise timestamps, add a DS3231 RTC module (~$2).

### 4. Flash

1. Open `towerwatch.ino` in Arduino IDE
2. Select **Board: Arduino Uno** and the correct **Port**
3. Click **Upload**

### 5. Grafana Cloud Setup

1. Create a free account at [grafana.com](https://grafana.com)
2. Go to **Connections > Add new connection > Hosted metrics (Influx)**
3. Note your **Influx push endpoint URL** and **instance ID**
4. Create an API key with `MetricsPublisher` role
5. Update `config.h` with your endpoint host and API key

The metrics will appear under the measurement name `towerwatch` with these fields:
- `rtt_avg`, `rtt_min`, `rtt_max` — latency in milliseconds
- `jitter` — RTT variance in milliseconds
- `pkt_loss` — packet loss percentage (0-100)
- `connected` — 1 = up, 0 = down

## How It Works

```
Every 30 seconds:
  1. Send 10 ICMP pings to 8.8.8.8
  2. Compute avg/min/max RTT, jitter, packet loss
  3. Write CSV row to SD card
  4. If connected, POST buffered rows to Grafana Cloud
  5. On successful push, flush SD buffer
  6. Track connection state transitions (outage start/end)
```

### During Outages
When the connection is down, metrics are buffered to the SD card as CSV. At ~60 bytes/row and one row per 30 seconds, a 1GB SD card can hold years of data. When connectivity returns, buffered data is pushed to Grafana Cloud automatically.

### Serial Monitor
Connect at **115200 baud** to see real-time output:
```
=== Towerwatch ===
Initializing...
Ethernet init...
IP: 192.168.1.100
SD init OK
Ready. Monitoring...
Interval: 30s

--- Cycle t=1709312430 ---
RTT avg=45 min=32 max=67 jitter=35 loss=0%
SD: wrote ts=1709312430
Push: 1 rows OK
Outages: 0 Total downtime: 0s
Buffer: 0 bytes
```

## Verification

1. **Serial monitor**: Confirm ping results and CSV writes appear
2. **SD card**: Remove the card and check `metrics.csv` on a computer
3. **Grafana Cloud**: Go to **Explore**, select your Influx data source, query `towerwatch`
4. **Outage simulation**: Unplug Ethernet cable → verify buffering → reconnect → verify flush
5. **24-hour soak test**: Leave running, confirm continuous data in Grafana

## Memory Budget

The sketch is designed to fit within the Uno's 2048 bytes of RAM:

- All string literals use `F()` (stored in flash, not RAM)
- No `String` class — only `char[]` buffers
- SD and Ethernet share SPI; only one is active at a time
- Constant data uses `PROGMEM`

## File Structure

```
towerwatch/
├── towerwatch.ino          # Main sketch: setup(), loop()
├── config.h                # All configurable constants
├── network_test.h/.cpp     # ICMP ping + metric computation
├── storage.h/.cpp          # SD card CSV read/write/flush
├── metrics_push.h/.cpp     # Grafana Cloud HTTP push
├── connection_state.h/.cpp # Up/down state machine
└── README.md               # This file
```

## Future Upgrades

- **ESP32**: Same Arduino C++ code, minimal changes. Adds WiFi (no Ethernet cable), throughput testing (enough RAM for HTTP downloads), built-in flash filesystem (no SD module). ~$8.
- **RTC module**: DS3231 for accurate timestamps (~$2)
- **Grafana dashboard template**: Pre-built dashboard JSON for all metrics
- **Alerting**: Grafana alerts when packet loss > 5% or latency > 200ms
- **Multi-target pinging**: Ping router + DNS + remote server to isolate tower vs internet issues
