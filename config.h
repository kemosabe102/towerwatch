#ifndef CONFIG_H
#define CONFIG_H

#include <avr/pgmspace.h>

// ============================================================
// Towerwatch Configuration
// Edit these values for your environment.
// ============================================================

// --- Network ---
// Set USE_DHCP to 0 for static IP, 1 for DHCP
#define USE_DHCP 1

// Static IP settings (only used if USE_DHCP == 0)
#define STATIC_IP      192, 168, 1, 100
#define STATIC_GATEWAY 192, 168, 1, 1
#define STATIC_SUBNET  255, 255, 255, 0
#define STATIC_DNS     8, 8, 8, 8

// MAC address for the Ethernet shield
#define ETH_MAC { 0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0xED }

// --- Ping Targets ---
// Primary and secondary ping targets (IP addresses)
#define PING_TARGET_PRIMARY   8, 8, 8, 8     // Google DNS
#define PING_TARGET_SECONDARY 1, 1, 1, 1     // Cloudflare DNS

// --- Test Parameters ---
#define TEST_INTERVAL_MS      30000UL  // 30 seconds between test cycles
#define PING_COUNT            5        // Pings per burst (for jitter calc)
#define PING_LOSS_COUNT       10       // Pings for packet-loss measurement
#define PING_TIMEOUT_MS       3000     // Timeout per ping in ms
#define PING_TCP_PORT         53       // TCP port used for RTT probes (Google DNS)

// --- SD Card ---
#define SD_CS_PIN             4        // Chip select for SD card module
// Note: Ethernet shield typically uses pin 10 for its CS.
// The SD module uses a separate CS pin (pin 4 is common).

#define METRICS_FILENAME "metrics.csv"
#define MAX_CSV_LINE     80   // Max length of a single CSV row

// --- Grafana Cloud ---
// Grafana Cloud metrics endpoint (Influx line protocol — plain text, Arduino-friendly)
// This pushes to the same Prometheus/Mimir backend as remote_write, just via text HTTP.
const char GRAFANA_HOST[] PROGMEM = "prometheus-prod-67-prod-us-west-0.grafana.net";
#define GRAFANA_PORT 443

// Path for Influx write endpoint
const char GRAFANA_PATH[] PROGMEM = "/api/v1/push/influx/write";

// Grafana Cloud Basic Auth — loaded from secrets.h (gitignored).
// See secrets.h for instructions on generating the base64 value.
#include "secrets.h"

// Measurement name used in Influx line protocol
const char INFLUX_MEASUREMENT[] PROGMEM = "towerwatch";

// Max rows to push per cycle (limits time spent in HTTP)
#define MAX_PUSH_ROWS  10

// --- Serial Debug ---
#define SERIAL_BAUD 115200
// Set to 0 to disable serial debug output and save RAM
#define DEBUG_SERIAL 1

#endif // CONFIG_H
