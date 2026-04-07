#ifndef STORAGE_H
#define STORAGE_H

#include <Arduino.h>
#include "network_test.h"

// Initialize the SD card. Returns true on success.
bool storageInit();

// Append a metric row to the CSV file on the SD card.
// timestamp: Unix epoch seconds
// result: ping test results
void appendMetric(uint32_t timestamp, const PingResult &result);

// Read the next buffered metric line into the provided buffer.
// Returns true if a line was read, false if no more lines.
// The caller provides the buffer and its size.
bool readNextMetric(char *buf, uint8_t bufSize);

// Reset the read position to the start of the file.
void resetReadPosition();

// Flush the buffer file: delete and recreate after all rows have been pushed.
void flushBuffer();

// Returns the number of bytes in the metrics file (approximate row count guide).
uint32_t bufferSize();

#endif // STORAGE_H
