#include "metrics_push.h"
#include "config.h"
#include "storage.h"
#include <Ethernet.h>
#include <avr/pgmspace.h>

static EthernetClient client;

// Buffer for reading PROGMEM strings
static char pgmBuf[64];

static void readPgm(const char *pgmStr) {
  strncpy_P(pgmBuf, pgmStr, sizeof(pgmBuf) - 1);
  pgmBuf[sizeof(pgmBuf) - 1] = '\0';
}

// Print a PROGMEM string directly to the client, byte by byte.
// Handles strings of any length without needing a RAM buffer.
static void clientPrintPgm(const char *pgmStr) {
  char c;
  while ((c = pgm_read_byte(pgmStr++)) != '\0') {
    client.write(c);
  }
}

void metricsPushInit() {
  // No persistent state to initialize
}

// Parse CSV line into 7 field pointers. Returns true if all 7 found.
// Mutates the input buffer (strtok).
static bool parseCsvFields(char *line, char *fields[7]) {
  uint8_t fc = 0;
  char *tok = strtok(line, ",");
  while (tok != NULL && fc < 7) {
    fields[fc++] = tok;
    tok = strtok(NULL, ",");
  }
  return (fc == 7);
}

// Compute the byte length of the Influx line protocol string for a given row.
// Does NOT include the trailing \r\n that println adds.
static uint16_t computeInfluxLineLen(const char *fields[7]) {
  readPgm(INFLUX_MEASUREMENT);
  uint16_t len = strlen(pgmBuf);   // "towerwatch"
  len += 17;                        // ",host=towerwatch "
  len += 8 + strlen(fields[1]);     // "rtt_avg=V"
  len += 9 + strlen(fields[2]);     // ",rtt_min=V"
  len += 9 + strlen(fields[3]);     // ",rtt_max=V"
  len += 8 + strlen(fields[4]);     // ",jitter=V"
  len += 10 + strlen(fields[5]);    // ",pkt_loss=V"
  len += 11 + strlen(fields[6]);    // ",connected=V"
  len += 1 + strlen(fields[0]);     // " timestamp"
  len += 2;                         // \r\n from println
  return len;
}

// Send one Influx line protocol row to the connected client.
static void sendInfluxLine(const char *fields[7]) {
  readPgm(INFLUX_MEASUREMENT);
  client.print(pgmBuf);
  client.print(F(",host=towerwatch "));
  client.print(F("rtt_avg="));
  client.print(fields[1]);
  client.print(F(",rtt_min="));
  client.print(fields[2]);
  client.print(F(",rtt_max="));
  client.print(fields[3]);
  client.print(F(",jitter="));
  client.print(fields[4]);
  client.print(F(",pkt_loss="));
  client.print(fields[5]);
  client.print(F(",connected="));
  client.print(fields[6]);
  client.print(' ');
  client.println(fields[0]);
}

uint8_t pushBufferedMetrics() {
  readPgm(GRAFANA_HOST);
  char hostBuf[48];
  strncpy(hostBuf, pgmBuf, sizeof(hostBuf) - 1);
  hostBuf[sizeof(hostBuf) - 1] = '\0';

  if (!client.connect(hostBuf, GRAFANA_PORT)) {
#if DEBUG_SERIAL
    Serial.println(F("Push: connect failed"));
#endif
    return 0;
  }

  // Send HTTP headers with chunked transfer encoding
  // (avoids needing to know Content-Length upfront)
  readPgm(GRAFANA_PATH);
  client.print(F("POST "));
  client.print(pgmBuf);
  client.println(F(" HTTP/1.1"));

  client.print(F("Host: "));
  client.println(hostBuf);

  client.print(F("Authorization: Basic "));
  clientPrintPgm(GRAFANA_BASIC_AUTH);
  client.println();

  client.println(F("Content-Type: text/plain"));
  client.println(F("Transfer-Encoding: chunked"));
  client.println(F("Connection: close"));
  client.println();

  // Send buffered rows as chunked body
  uint8_t rowsSent = 0;
  char csvBuf[MAX_CSV_LINE];

  resetReadPosition();

  while (rowsSent < MAX_PUSH_ROWS && readNextMetric(csvBuf, sizeof(csvBuf))) {
    if (csvBuf[0] == '\0') continue;

    // Parse CSV into fields (mutates csvBuf)
    char *fields[7];
    if (!parseCsvFields(csvBuf, fields)) continue;

    // Send chunk: hex size, then data, then blank line
    uint16_t lineLen = computeInfluxLineLen((const char **)fields);
    char hexBuf[8];
    snprintf(hexBuf, sizeof(hexBuf), "%X", lineLen);
    client.println(hexBuf);

    sendInfluxLine((const char **)fields);

    client.println(); // blank line after chunk data
    rowsSent++;
  }

  // Terminal chunk
  client.println(F("0"));
  client.println();

  // Read HTTP response status line
  bool success = false;
  uint32_t deadline = millis() + 5000;
  while (client.connected() && millis() < deadline) {
    if (client.available()) {
      char respBuf[32];
      uint8_t ri = 0;
      while (client.available() && ri < sizeof(respBuf) - 1) {
        char c = client.read();
        if (c == '\n') break;
        respBuf[ri++] = c;
      }
      respBuf[ri] = '\0';

      // Check for "HTTP/1.1 2xx" — look for " 2" after "HTTP"
      char *sp = strchr(respBuf, ' ');
      if (sp && sp[1] == '2') {
        success = true;
      }

#if DEBUG_SERIAL
      Serial.print(F("Push resp: "));
      Serial.println(respBuf);
#endif
      break;
    }
  }

  // Drain remaining response
  while (client.available()) {
    client.read();
  }
  client.stop();

  if (success && rowsSent > 0) {
    flushBuffer();
#if DEBUG_SERIAL
    Serial.print(F("Push: "));
    Serial.print(rowsSent);
    Serial.println(F(" rows OK"));
#endif
  } else {
    resetReadPosition();
#if DEBUG_SERIAL
    Serial.println(F("Push: failed, will retry"));
#endif
    rowsSent = 0;
  }

  return rowsSent;
}
