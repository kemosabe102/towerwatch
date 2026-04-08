#include "network_test.h"
#include "config.h"
#include <Ethernet.h>

// TCP-based RTT measurement using Google DNS (8.8.8.8:53).
// Each probe opens a TCP connection and times the handshake, then closes it.
// This requires no external libraries and works with all Ethernet library versions.

void networkTestInit() {
  // Nothing to initialize — EthernetClient is constructed per-probe.
}

void runPingTest(PingResult &result) {
  IPAddress target(PING_TARGET_PRIMARY);

  uint8_t  successes = 0;
  uint32_t rttSum    = 0;
  uint16_t minRtt    = 0xFFFF;
  uint16_t maxRtt    = 0;

  for (uint8_t i = 0; i < PING_LOSS_COUNT; i++) {
    EthernetClient client;
    uint32_t startMs = millis();
    bool connected = client.connect(target, PING_TCP_PORT);
    uint32_t elapsed = millis() - startMs;
    client.stop();

    if (connected) {
      uint16_t rtt = (uint16_t)elapsed;
      rttSum += rtt;
      if (rtt < minRtt) minRtt = rtt;
      if (rtt > maxRtt) maxRtt = rtt;
      successes++;
    }

    // Small gap between probes to avoid socket exhaustion on W5100
    delay(100);
  }

  if (successes > 0) {
    result.rttAvg    = (uint16_t)(rttSum / successes);
    result.rttMin    = minRtt;
    result.rttMax    = maxRtt;
    result.jitter    = (successes >= 2) ? (maxRtt - minRtt) : 0;
    result.connected = true;
  } else {
    result.rttAvg    = 0;
    result.rttMin    = 0;
    result.rttMax    = 0;
    result.jitter    = 0;
    result.connected = false;
  }

  result.pktLoss = (uint8_t)(((uint16_t)(PING_LOSS_COUNT - successes) * 100) / PING_LOSS_COUNT);
}
