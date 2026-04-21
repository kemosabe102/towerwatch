#!/usr/bin/env python3
"""
Towerwatch — 5G Cell Tower Network Quality Monitor

Continuously monitors latency, jitter, packet loss, DNS resolution,
TCP connection time, throughput, and M6 signal quality. Pushes metrics
to Grafana Cloud over HTTPS. Pushes structured logs to Loki.
Buffers logs locally during outages for delivery on reconnect.

Cross-platform: runs on Raspberry Pi (production) and Windows (testing).
"""

import json
import logging
import os
import random
import re
import signal
import socket
import subprocess
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

import config
import grafana as grafana_mod
from probes.ping import run_ping, PingProbe
from probes.tcp import measure_tcp_connect, TCPProbe
from probes.dns import measure_dns, DNSProbe
from probes.http import measure_http_latency, measure_http_throughput, HTTPLatencyProbe, HTTPThroughputProbe
from probes.ookla import run_speedtest, OoklaProbe
from probes.m6 import poll_m6_signal, M6Probe
import loki as loki_mod
from loki import push_log, log_and_push

try:
    import credentials
except ImportError:
    print("ERROR: credentials.py not found. Copy credentials.py.example to credentials.py and fill in values.")
    raise SystemExit(1)

log = logging.getLogger("towerwatch")

IS_WINDOWS = sys.platform == "win32"


# ---------------------------------------------------------------------------
# RuntimeState: consolidated module globals
# ---------------------------------------------------------------------------
@dataclass
class RuntimeState:
    """Encapsulates all mutable state shared across main loop functions."""
    connected: bool = True
    outage_start: int = 0
    outage_count: int = 0
    total_outage_s: int = 0
    start_ts: float = field(default_factory=time.monotonic)
    last_heartbeat_ts: float = 0.0
    last_successful_push_ts: float = field(default_factory=time.time)
    shutdown_requested: bool = False
    metric_batch: list = field(default_factory=list)


def update_connection_state(state: "RuntimeState", connected: bool, timestamp: int):
    """Update connection state tracking. Logs transitions."""
    if connected and not state.connected:
        if state.outage_start:
            duration = timestamp - state.outage_start
            state.total_outage_s += duration
            log_and_push("INFO", f"Connection restored after {duration}s",
                       event=config.LOG_EVENT_CONN_RESTORED, down_duration_s=duration)
        state.outage_start = 0
    elif not connected and state.connected:
        state.outage_start = timestamp
        state.outage_count += 1
        log.warning("Connection DOWN")
        push_log("ERROR", "All targets unreachable",
                 {"event": config.LOG_EVENT_CONN_DOWN})
    state.connected = connected


def format_influx_line(fields: dict, timestamp: int) -> str:
    """Format a single Influx line protocol string."""
    parts = [f"{k}={v}" for k, v in fields.items() if v is not None]
    return (
        f"{config.INFLUX_MEASUREMENT},host={config.INFLUX_HOST_TAG} "
        + ",".join(parts)
        + f" {timestamp}"
    )







# ---------------------------------------------------------------------------
# Startup guard: wait for data partition
# ---------------------------------------------------------------------------
def wait_for_data_partition(timeout: int = 30):
    """Block until the data partition is mounted or timeout. Skips on Windows."""
    data = Path(config.DATA_DIR)
    if IS_WINDOWS:
        data.mkdir(parents=True, exist_ok=True)
        log.info("Windows: using local data dir %s", data)
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        if data.is_dir():
            try:
                result = subprocess.run(
                    ["mountpoint", "-q", str(data)],
                    capture_output=True, timeout=5,
                )
                if result.returncode == 0:
                    log.info("Data partition mounted at %s", data)
                    return
            except Exception:
                pass
        time.sleep(1)
    log.warning("Data partition not detected at %s — buffering to local dir", data)
    try:
        _buffer_log_entry(_build_loki_payload(
            "WARN", f"Data partition not detected at {data}",
            {"event": config.LOG_EVENT_PARTITION_MISSING, "path": str(data)}))
    except Exception:
        pass
    data.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Random throughput schedule
# ---------------------------------------------------------------------------
def _build_daily_throughput_schedule() -> list[float]:
    """Generate random timestamps for today's throughput tests.
    Divides 24 hours into equal slots, picks a random second in each.
    Skips slots whose time has already passed."""
    n = config.HTTP_THROUGHPUT_TESTS_PER_DAY
    now = time.time()
    # Start of today (local time)
    local = time.localtime(now)
    midnight = time.mktime(time.struct_time((
        local.tm_year, local.tm_mon, local.tm_mday,
        0, 0, 0, 0, 0, local.tm_isdst,
    )))
    slot_size = 86400 / n
    schedule = []
    for i in range(n):
        slot_start = midnight + i * slot_size
        slot_end = slot_start + slot_size
        t = random.uniform(slot_start, slot_end)
        if t > now:
            schedule.append(t)
    schedule.sort()
    log.info("Throughput schedule: %s",
             [time.strftime("%H:%M", time.localtime(t)) for t in schedule])
    return schedule


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------
def _configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _install_signal_handlers(state: "RuntimeState") -> None:
    """Install signal handlers. SIGTERM sets shutdown_requested flag in state."""
    def _on_sigterm(signum, frame):
        log.info("SIGTERM received — shutting down gracefully")
        state.shutdown_requested = True
    if not IS_WINDOWS:
        signal.signal(signal.SIGTERM, _on_sigterm)


# ---------------------------------------------------------------------------
# Main loop helpers
# ---------------------------------------------------------------------------
def _collect_probes(state: "RuntimeState", last_http_latency, throughput_schedule, last_schedule_day):
    """Run all probe sections. Returns (fields, any_connected, last_http_latency, throughput_schedule, last_schedule_day)."""
    fields = {}
    now = time.time()

    # ICMP Ping (multi-target)
    any_connected = False
    for target_ip, target_label in config.PROBE_TARGETS:
        ping = run_ping(target_ip)
        any_connected = any_connected or ping["connected"]
        for metric, value in ping.items():
            if metric == "connected":
                fields[f"connected_{target_label}"] = 1 if value else 0
            else:
                fields[f"{metric}_{target_label}"] = value
    fields["connected"] = 1 if any_connected else 0
    fields["metric_interval_s"] = config.METRIC_INTERVAL_S

    # TCP + DNS
    fields["tcp_connect_ms"] = measure_tcp_connect()
    for ns in config.DNS_TARGETS:
        ns_label = ns.replace(".", "_")
        fields[f"dns_resolve_ms_{ns_label}"] = measure_dns(ns)

    # M6 Signal
    fields.update(poll_m6_signal())

    # HTTP Latency (10KB, every 5 min)
    if now - last_http_latency >= config.HTTP_LATENCY_INTERVAL_S:
        fields["http_latency_ms"] = measure_http_latency()
        last_http_latency = now

    # HTTP Throughput (1MB, random schedule)
    today_yday = time.localtime().tm_yday
    if today_yday != last_schedule_day:
        throughput_schedule = _build_daily_throughput_schedule()
        last_schedule_day = today_yday
    if throughput_schedule and now >= throughput_schedule[0]:
        throughput_schedule.pop(0)
        fields.update(measure_http_throughput())

    return fields, any_connected, last_http_latency, throughput_schedule, last_schedule_day


def _log_cycle(state: "RuntimeState", fields, timestamp):
    """Log cycle summary to console and Loki."""
    duration = fields["collection_duration_ms"]
    log.info("Cycle t=%d connected=%s rtt_avg_google=%s duration=%dms",
             timestamp, fields.get("connected"),
             fields.get("rtt_avg_google"), duration)
    push_log("DEBUG", f"Cycle complete in {duration}ms",
             {"event": "cycle_complete", "duration_ms": duration,
              "connected": fields.get("connected")})


def _write_ts(path: Path, ts: float, atomic: bool = False) -> None:
    """Persist a Unix timestamp to a marker file. Best-effort — OSError is swallowed."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if atomic:
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(f"{ts:.0f}\n", encoding="utf-8")
            os.replace(tmp, path)
        else:
            path.write_text(f"{ts:.0f}\n", encoding="utf-8")
    except OSError:
        pass


def _read_ts(path: Path) -> float | None:
    """Read a persisted Unix timestamp. Returns None if missing or unreadable."""
    try:
        raw = path.read_text(encoding="utf-8").strip()
        return float(raw) if raw else None
    except (OSError, ValueError):
        return None


_grafana_client: "grafana_mod.GrafanaClient | None" = None
_loki_client: "loki_mod.LokiClient | None" = None


def _record_outage_annotation(start_ts: float, end_ts: float, reason: str):
    """POST a region annotation for an outage gap and log it."""
    gap_s = int(end_ts - start_ts)
    gap_min = int(gap_s / 60)
    text = (f"Outage: {gap_min} min — {reason} "
            f"(v {config.BUILD_VERSION})")
    if _grafana_client:
        _grafana_client.push_annotation(int(start_ts * 1000), int(end_ts * 1000),
                                        text, reason=reason, version=config.BUILD_VERSION)
    push_log("WARN", f"Outage recorded: {gap_s}s ({gap_min} min)",
             {"event": config.LOG_EVENT_OUTAGE_RECORDED,
              "gap_seconds": gap_s, "reason": reason,
              "version": config.BUILD_VERSION})


def _batch_and_push(state: "RuntimeState", line: str, any_connected: bool):
    """Accumulate metrics in memory; push when batch is full. Drop on failure."""
    state.metric_batch.append(line)
    if len(state.metric_batch) < config.PUSH_BATCH_SIZE:
        return
    batch = state.metric_batch[:]
    state.metric_batch.clear()
    if not any_connected:
        return
    if not _grafana_client or not _grafana_client.push_metrics(batch):
        log.warning("Metric push failed — batch dropped (%d lines)", len(batch))
        return
    log.info("Pushed %d lines", len(batch))
    now = time.time()
    gap = now - state.last_successful_push_ts
    if gap >= config.OUTAGE_GAP_THRESHOLD_S:
        _record_outage_annotation(state.last_successful_push_ts, now, "network_unreachable")
    state.last_successful_push_ts = now
    _write_ts(Path(config.LAST_PUSH_MARKER_FILE), now, atomic=True)
    if _loki_client:
        _loki_client.flush()


def _maybe_heartbeat(state: "RuntimeState"):
    """Emit a periodic heartbeat log to Loki so the Event Log panel stays populated."""
    now = time.time()
    if now - state.last_heartbeat_ts >= config.HEARTBEAT_INTERVAL_S:
        uptime_h = round((time.monotonic() - state.start_ts) / 3600, 1)
        push_log("WARN", "Service heartbeat",
                 {"event": config.LOG_EVENT_HEARTBEAT, "uptime_h": uptime_h})
        state.last_heartbeat_ts = now


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    global _grafana_client, _loki_client
    state = RuntimeState()
    _configure_logging()
    _install_signal_handlers(state)
    _loki_client = loki_mod.LokiClient.from_config(config, credentials)
    _grafana_client = grafana_mod.GrafanaClient.from_config(config, credentials)
    log.info("=== Towerwatch %s ===", "(Windows)" if IS_WINDOWS else "(Raspberry Pi)")
    wait_for_data_partition()

    # Emit service_restarted at WARN so it survives LOKI_PUSH_LEVEL=WARN and shows
    # up as a deploy marker in the event-log panel. Keep service_started too for
    # backwards compat with any saved Grafana queries.
    push_log("WARN", "Service restarted",
             {"event": config.LOG_EVENT_SERVICE_RESTARTED,
              "version": config.BUILD_VERSION,
              "build_date": config.BUILD_DATE,
              "platform": sys.platform})
    push_log("INFO", "Service started",
             {"event": config.LOG_EVENT_SERVICE_STARTED, "log_level": config.LOKI_PUSH_LEVEL,
              "platform": sys.platform})

    # One-shot restart metric — will be pushed with the first batch.
    state.metric_batch.append(format_influx_line({"service_restart": 1}, int(time.time())))

    # Load persisted markers and check for a cross-restart gap. Use last_alive_ts
    # to distinguish: process was running (network outage) vs process was dead (restart).
    loaded_last_push = _read_ts(Path(config.LAST_PUSH_MARKER_FILE))
    loaded_last_alive = _read_ts(Path(config.LAST_ALIVE_MARKER_FILE))
    if loaded_last_push is not None:
        state.last_successful_push_ts = loaded_last_push
        startup_now = time.time()
        startup_gap = startup_now - loaded_last_push
        if startup_gap >= config.OUTAGE_GAP_THRESHOLD_S:
            if loaded_last_alive and (startup_now - loaded_last_alive) < config.OUTAGE_GAP_THRESHOLD_S:
                reason = "network_unreachable"
            else:
                reason = "process_restart"
            _record_outage_annotation(loaded_last_push, startup_now, reason)

    if _loki_client:
        _loki_client.flush()

    last_http_latency = 0
    throughput_schedule = _build_daily_throughput_schedule()
    last_schedule_day = time.localtime().tm_yday

    while not state.shutdown_requested:
        cycle_start = time.perf_counter()
        timestamp = int(time.time())

        fields, any_connected, last_http_latency, throughput_schedule, last_schedule_day = \
            _collect_probes(state, last_http_latency, throughput_schedule, last_schedule_day)

        update_connection_state(state, any_connected, timestamp)
        fields["collection_duration_ms"] = round(
            (time.perf_counter() - cycle_start) * 1000
        )

        _log_cycle(state, fields, timestamp)
        _write_ts(Path(config.LAST_ALIVE_MARKER_FILE), time.time())
        _batch_and_push(state, format_influx_line(fields, timestamp), any_connected)

        _maybe_heartbeat(state)

        elapsed = time.perf_counter() - cycle_start
        time.sleep(max(0, config.METRIC_INTERVAL_S - elapsed))

    log.info("Shutdown complete")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Stopped by user")
    except Exception as e:
        log.critical("Fatal error: %s", e, exc_info=True)
        push_log("ERROR", f"Fatal: {e}", {"event": "fatal_error", "error": str(e)})
        raise
