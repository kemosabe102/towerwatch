// ============================================================
// Towerwatch — 5G Cell Tower Network Quality Monitor
//
// Continuously monitors latency, jitter, and packet loss to
// build an evidence dataset for your cellular provider.
//
// Hardware: Arduino Uno R3 + Ethernet Shield + SD Card Module
// ============================================================

#include <SPI.h>
#include <Ethernet.h>
#include <SD.h>
#include <avr/wdt.h>

#include "config.h"
#include "network_test.h"
#include "storage.h"
#include "metrics_push.h"
#include "connection_state.h"

// MAC address for Ethernet shield
static byte mac[] = ETH_MAC;

// Approximate Unix timestamp — incremented from a base value.
// The Uno has no RTC, so we estimate time from millis() offset.
// Set BOOT_TIMESTAMP to the Unix epoch time when you flash the sketch.
// You can get it from: date +%s
#define BOOT_TIMESTAMP 1775523939UL

static uint32_t bootTimestamp = BOOT_TIMESTAMP;
static uint32_t lastTestMillis = 0;
static bool sdReady = false;

// Get approximate Unix timestamp
static uint32_t getTimestamp() {
  return bootTimestamp + (millis() / 1000UL);
}

void setup() {
#if DEBUG_SERIAL
  Serial.begin(SERIAL_BAUD);
  while (!Serial) { ; } // wait for serial on boards that need it
  Serial.println(F("=== Towerwatch ==="));
  Serial.println(F("Initializing..."));
#endif

  // --- Initialize Ethernet (before watchdog — DHCP can take >8s) ---
#if DEBUG_SERIAL
  Serial.println(F("Ethernet init..."));
#endif

#if USE_DHCP
  if (Ethernet.begin(mac) == 0) {
#if DEBUG_SERIAL
    Serial.println(F("DHCP failed, trying static"));
#endif
    // Fall back to static IP
    IPAddress ip(STATIC_IP);
    IPAddress gw(STATIC_GATEWAY);
    IPAddress sn(STATIC_SUBNET);
    IPAddress dns(STATIC_DNS);
    Ethernet.begin(mac, ip, dns, gw, sn);
  }
#else
  IPAddress ip(STATIC_IP);
  IPAddress gw(STATIC_GATEWAY);
  IPAddress sn(STATIC_SUBNET);
  IPAddress dns(STATIC_DNS);
  Ethernet.begin(mac, ip, dns, gw, sn);
#endif

  // Enable watchdog timer now that slow init is done
  wdt_enable(WDTO_8S);
  wdt_reset();

#if DEBUG_SERIAL
  Serial.print(F("IP: "));
  Serial.println(Ethernet.localIP());
#endif

  // --- Initialize SD Card ---
  sdReady = storageInit();
  wdt_reset();

  // --- Initialize subsystems ---
  networkTestInit();
  metricsPushInit();
  connectionStateInit();

#if DEBUG_SERIAL
  Serial.println(F("Ready. Monitoring..."));
  Serial.print(F("Interval: "));
  Serial.print(TEST_INTERVAL_MS / 1000);
  Serial.println(F("s"));
#endif

  // Run first test immediately
  lastTestMillis = millis() - TEST_INTERVAL_MS;
}

void loop() {
  wdt_reset();

  // Maintain DHCP lease
#if USE_DHCP
  Ethernet.maintain();
#endif

  uint32_t now = millis();

  // Check if it's time for a test cycle
  if ((now - lastTestMillis) < TEST_INTERVAL_MS) {
    return;
  }
  lastTestMillis = now;

  uint32_t timestamp = getTimestamp();

#if DEBUG_SERIAL
  Serial.println();
  Serial.print(F("--- Cycle t="));
  Serial.print(timestamp);
  Serial.println(F(" ---"));
#endif

  // 1. Run ping test
  PingResult result;
  runPingTest(result);
  wdt_reset();

#if DEBUG_SERIAL
  Serial.print(F("RTT avg="));
  Serial.print(result.rttAvg);
  Serial.print(F(" min="));
  Serial.print(result.rttMin);
  Serial.print(F(" max="));
  Serial.print(result.rttMax);
  Serial.print(F(" jitter="));
  Serial.print(result.jitter);
  Serial.print(F(" loss="));
  Serial.print(result.pktLoss);
  Serial.println(F("%"));
#endif

  // 2. Update connection state
  connectionStateUpdate(result.connected, timestamp);
  wdt_reset();

  // 3. Buffer metric to SD card
  if (sdReady) {
    appendMetric(timestamp, result);
    wdt_reset();
  }

  // 4. Attempt to push buffered metrics to Grafana Cloud
  if (result.connected && sdReady) {
    uint8_t pushed = pushBufferedMetrics();
    wdt_reset();

#if DEBUG_SERIAL
    if (pushed > 0) {
      Serial.print(F("Pushed "));
      Serial.print(pushed);
      Serial.println(F(" rows"));
    }
#endif
  }

#if DEBUG_SERIAL
  // Status summary
  Serial.print(F("Outages: "));
  Serial.print(outageCount());
  Serial.print(F(" Total downtime: "));
  Serial.print(totalOutageSeconds());
  Serial.println(F("s"));

  if (sdReady) {
    Serial.print(F("Buffer: "));
    Serial.print(bufferSize());
    Serial.println(F(" bytes"));
  }
#endif
}
