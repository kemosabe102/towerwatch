#ifndef CONNECTION_STATE_H
#define CONNECTION_STATE_H

#include <Arduino.h>

// Connection state machine: tracks up/down transitions and outage durations.

// Initialize connection state tracking.
void connectionStateInit();

// Update the connection state with the latest test result.
// connected: true if the network test indicated connectivity.
// timestamp: current Unix epoch seconds.
// Logs transitions and outage durations to Serial.
void connectionStateUpdate(bool connected, uint32_t timestamp);

// Returns true if the connection is currently considered up.
bool isConnected();

// Returns the timestamp when the current outage started, or 0 if connected.
uint32_t outageStartTime();

// Returns how many outages have been recorded since boot.
uint16_t outageCount();

// Returns total seconds spent in outage since boot.
uint32_t totalOutageSeconds();

#endif // CONNECTION_STATE_H
