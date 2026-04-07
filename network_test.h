#ifndef NETWORK_TEST_H
#define NETWORK_TEST_H

#include <Arduino.h>

// Results from a single ping-burst test cycle
struct PingResult {
  uint16_t rttAvg;    // Average round-trip time in ms
  uint16_t rttMin;    // Minimum RTT in ms
  uint16_t rttMax;    // Maximum RTT in ms
  uint16_t jitter;    // Jitter (max - min) in ms
  uint8_t  pktLoss;   // Packet loss percentage (0-100)
  bool     connected; // True if at least one ping succeeded
};

// Initialize the ping subsystem (call once in setup)
void networkTestInit();

// Run a full test cycle: burst of pings, compute stats.
// Writes results into the provided PingResult struct.
void runPingTest(PingResult &result);

#endif // NETWORK_TEST_H
