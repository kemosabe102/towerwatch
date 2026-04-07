#include "network_test.h"
#include "config.h"
#include <Ethernet.h>
#include <ICMPPing.h>

// Shared ICMP socket — uses one of the W5x00 hardware sockets
static SOCKET pingSocket = 0;

static ICMPPing ping(pingSocket, 1); // socket, id

void networkTestInit() {
  // ICMPPing doesn't require explicit init beyond construction.
}

void runPingTest(PingResult &result) {
  IPAddress target(PING_TARGET_PRIMARY);

  uint8_t  successes = 0;
  uint32_t rttSum = 0;
  uint16_t minRtt = 0xFFFF;
  uint16_t maxRtt = 0;

  // Send PING_LOSS_COUNT pings. Time each one manually for reliable RTT.
  for (uint8_t i = 0; i < PING_LOSS_COUNT; i++) {
    uint32_t startMs = millis();
    ICMPEchoReply echoReply = ping(target, PING_TIMEOUT_MS);
    uint32_t elapsed = millis() - startMs;

    if (echoReply.status == SUCCESS) {
      uint16_t rtt = (uint16_t)elapsed;
      rttSum += rtt;

      if (rtt < minRtt) minRtt = rtt;
      if (rtt > maxRtt) maxRtt = rtt;

      successes++;
    }
  }

  // Compute results
  if (successes > 0) {
    result.rttAvg = (uint16_t)(rttSum / successes);
    result.rttMin = minRtt;
    result.rttMax = maxRtt;

    // Jitter: difference between max and min RTT across the burst
    if (successes >= 2) {
      result.jitter = maxRtt - minRtt;
    } else {
      result.jitter = 0;
    }

    result.connected = true;
  } else {
    result.rttAvg = 0;
    result.rttMin = 0;
    result.rttMax = 0;
    result.jitter = 0;
    result.connected = false;
  }

  // Packet loss as percentage
  result.pktLoss = (uint8_t)(((uint16_t)(PING_LOSS_COUNT - successes) * 100) / PING_LOSS_COUNT);
}
