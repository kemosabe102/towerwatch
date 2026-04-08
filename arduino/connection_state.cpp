#include "connection_state.h"
#include "config.h"

static bool     currentlyConnected = false;
static uint32_t currentOutageStart = 0;
static uint16_t totalOutages       = 0;
static uint32_t totalOutageSecs    = 0;
static bool     initialized        = false;

void connectionStateInit() {
  currentlyConnected = false;
  currentOutageStart = 0;
  totalOutages       = 0;
  totalOutageSecs    = 0;
  initialized        = false;
}

void connectionStateUpdate(bool connected, uint32_t timestamp) {
  if (!initialized) {
    // First reading — set initial state without logging a transition
    currentlyConnected = connected;
    if (!connected) {
      currentOutageStart = timestamp;
    }
    initialized = true;

#if DEBUG_SERIAL
    Serial.print(F("State: init "));
    Serial.println(connected ? F("UP") : F("DOWN"));
#endif
    return;
  }

  if (connected && !currentlyConnected) {
    // Transition: DOWN -> UP (recovery)
    uint32_t outageDuration = 0;
    if (currentOutageStart > 0) {
      outageDuration = timestamp - currentOutageStart;
      totalOutageSecs += outageDuration;
    }

#if DEBUG_SERIAL
    Serial.print(F("State: UP after "));
    Serial.print(outageDuration);
    Serial.println(F("s outage"));
#endif

    currentlyConnected = true;
    currentOutageStart = 0;

  } else if (!connected && currentlyConnected) {
    // Transition: UP -> DOWN (outage start)
    currentlyConnected = false;
    currentOutageStart = timestamp;
    totalOutages++;

#if DEBUG_SERIAL
    Serial.print(F("State: DOWN at "));
    Serial.println(timestamp);
#endif
  }
  // No transition — state unchanged, nothing to log
}

bool isConnected() {
  return currentlyConnected;
}

uint32_t outageStartTime() {
  return currentOutageStart;
}

uint16_t outageCount() {
  return totalOutages;
}

uint32_t totalOutageSeconds() {
  return totalOutageSecs;
}
