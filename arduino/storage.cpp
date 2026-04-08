#include "storage.h"
#include "config.h"
#include <SD.h>
#include <SPI.h>

// Track read position for incremental reading during push
static uint32_t readPos = 0;

bool storageInit() {
  // Disable Ethernet CS to avoid SPI bus contention
  pinMode(10, OUTPUT);
  digitalWrite(10, HIGH);

  if (!SD.begin(SD_CS_PIN)) {
#if DEBUG_SERIAL
    Serial.println(F("SD init failed"));
#endif
    return false;
  }

#if DEBUG_SERIAL
  Serial.println(F("SD init OK"));
#endif

  readPos = 0;
  return true;
}

void appendMetric(uint32_t timestamp, const PingResult &result) {
  File f = SD.open(METRICS_FILENAME, FILE_WRITE);
  if (!f) {
#if DEBUG_SERIAL
    Serial.println(F("SD write err"));
#endif
    return;
  }

  // Format: timestamp,rtt_avg,rtt_min,rtt_max,jitter,pkt_loss,connected
  // Using print() calls instead of sprintf to avoid large format-string RAM usage
  f.print(timestamp);
  f.print(',');
  f.print(result.rttAvg);
  f.print(',');
  f.print(result.rttMin);
  f.print(',');
  f.print(result.rttMax);
  f.print(',');
  f.print(result.jitter);
  f.print(',');
  f.print(result.pktLoss);
  f.print(',');
  f.println(result.connected ? 1 : 0);

  f.close();

#if DEBUG_SERIAL
  Serial.print(F("SD: wrote ts="));
  Serial.println(timestamp);
#endif
}

bool readNextMetric(char *buf, uint8_t bufSize) {
  File f = SD.open(METRICS_FILENAME, FILE_READ);
  if (!f) {
    return false;
  }

  // Seek to our tracked read position
  if (readPos > 0) {
    if (!f.seek(readPos)) {
      f.close();
      return false;
    }
  }

  // Check if we've reached the end
  if (!f.available()) {
    f.close();
    return false;
  }

  // Read one line
  uint8_t i = 0;
  while (f.available() && i < (bufSize - 1)) {
    char c = f.read();
    if (c == '\n') {
      break;
    }
    if (c == '\r') {
      continue; // skip CR
    }
    buf[i++] = c;
  }
  buf[i] = '\0';

  // Update read position
  readPos = f.position();
  f.close();

  // Return true only if we actually read something
  return (i > 0);
}

void resetReadPosition() {
  readPos = 0;
}

void flushBuffer() {
  SD.remove(METRICS_FILENAME);
  readPos = 0;

#if DEBUG_SERIAL
  Serial.println(F("SD: buffer flushed"));
#endif
}

uint32_t bufferSize() {
  File f = SD.open(METRICS_FILENAME, FILE_READ);
  if (!f) {
    return 0;
  }
  uint32_t sz = f.size();
  f.close();
  return sz;
}
