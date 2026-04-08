#ifndef METRICS_PUSH_H
#define METRICS_PUSH_H

#include <Arduino.h>

// Initialize the metrics push subsystem (nothing heavy; just state reset).
void metricsPushInit();

// Attempt to push buffered metrics from the SD card to Grafana Cloud.
// Reads rows from SD, formats as Influx line protocol, POSTs via HTTP.
// Returns the number of rows successfully pushed (0 if offline or error).
uint8_t pushBufferedMetrics();

#endif // METRICS_PUSH_H
