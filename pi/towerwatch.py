#!/usr/bin/env python3
"""
Towerwatch — 5G Cell Tower Network Quality Monitor

Continuously monitors latency, jitter, packet loss, DNS resolution,
TCP connection time, throughput, and M6 signal quality. Pushes metrics
to Grafana Cloud over HTTPS. Pushes structured logs to Loki.
Buffers logs locally during outages for delivery on reconnect.

Cross-platform: runs on Raspberry Pi (production) and Windows (testing).
"""

import logging
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import config
import events as events_mod
import grafana as grafana_mod
import loki as loki_mod
import startup as startup_mod
from scheduling import Scheduler
from probes.ping import run_ping
from probes.tcp import measure_tcp_connect
from probes.dns import measure_dns
from probes.http import measure_http_latency, measure_http_throughput
from probes.m6 import poll_m6_signal

try:
    import credentials
except ImportError:
    print("ERROR: credentials.py not found. Copy credentials.py.example to credentials.py and fill in values.")
    raise SystemExit(1)

log = logging.getLogger("towerwatch")

IS_WINDOWS = sys.platform == "win32"

_grafana_client: "grafana_mod.GrafanaClient | None" = None
_loki_client: "loki_mod.LokiClient | None" = None
_scheduler: "Scheduler | None" = None


# ---------------------------------------------------------------------------
# RuntimeState
# ---------------------------------------------------------------------------
@dataclass
class RuntimeState:
    connected: bool = True
    outage_start: int = 0
    outage_count: int = 0
    total_outage_s: int = 0
    start_ts: float = field(default_factory=time.monotonic)
    last_heartbeat_ts: float = 0.0
    last_successful_push_ts: float = field(default_factory=time.time)
    shutdown_requested: bool = False
    metric_batch: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def format_influx_line(fields: dict, timestamp: int) -> str:
    parts = [f"{k}={v}" for k, v in fields.items() if v is not None]
    return (
        f"{config.INFLUX_MEASUREMENT},host={config.INFLUX_HOST_TAG} "
        + ",".join(parts)
        + f" {timestamp}"
    )


def _configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _install_signal_handlers(state: "RuntimeState") -> None:
    def _on_sigterm(signum, frame):
        log.info("SIGTERM received — shutting down gracefully")
        state.shutdown_requested = True
    if not IS_WINDOWS:
        signal.signal(signal.SIGTERM, _on_sigterm)


def _update_connection_state(state: "RuntimeState", connected: bool, timestamp: int) -> None:
    if connected and not state.connected:
        if state.outage_start:
            duration = timestamp - state.outage_start
            state.total_outage_s += duration
            events_mod.connection_restored(_loki_client, down_duration_s=duration)
        state.outage_start = 0
    elif not connected and state.connected:
        state.outage_start = timestamp
        state.outage_count += 1
        log.warning("Connection DOWN")
        events_mod.connection_down(_loki_client)
    state.connected = connected


def _collect_probes() -> tuple[dict, bool]:
    fields = {}
    now = time.time()

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

    fields["tcp_connect_ms"] = measure_tcp_connect()
    for ns in config.DNS_TARGETS:
        fields[f"dns_resolve_ms_{ns.replace('.', '_')}"] = measure_dns(ns)

    fields.update(poll_m6_signal())

    if _scheduler and _scheduler.should_run_http_latency(now):
        fields["http_latency_ms"] = measure_http_latency()
    if _scheduler and _scheduler.should_run_throughput(now):
        fields.update(measure_http_throughput())

    return fields, any_connected


def _push_batch(state: "RuntimeState", line: str, any_connected: bool) -> None:
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
        gap_s = int(gap)
        text = f"Outage: {gap_s // 60} min — network_unreachable (v {config.BUILD_VERSION})"
        if _grafana_client:
            _grafana_client.push_annotation(
                int(state.last_successful_push_ts * 1000), int(now * 1000),
                text, reason="network_unreachable", version=config.BUILD_VERSION,
            )
        events_mod.outage_recorded(_loki_client, gap_seconds=gap_s,
                                   reason="network_unreachable", version=config.BUILD_VERSION)
    state.last_successful_push_ts = now
    startup_mod.write_marker(Path(config.LAST_PUSH_MARKER_FILE), now, atomic=True)
    if _loki_client:
        _loki_client.flush()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    global _grafana_client, _loki_client, _scheduler

    state = RuntimeState()
    _configure_logging()
    _install_signal_handlers(state)

    _loki_client = loki_mod.LokiClient.from_config(config, credentials)
    _grafana_client = grafana_mod.GrafanaClient.from_config(config, credentials)
    _scheduler = Scheduler.from_config(config)

    log.info("=== Towerwatch %s ===", "(Windows)" if IS_WINDOWS else "(Raspberry Pi)")
    startup_mod.wait_for_data_partition(Path(config.DATA_DIR))

    events_mod.service_restarted(_loki_client, version=config.BUILD_VERSION,
                                 build_date=config.BUILD_DATE, platform=sys.platform)
    events_mod.service_started(_loki_client, log_level=config.LOKI_PUSH_LEVEL,
                               platform=sys.platform)

    state.metric_batch.append(format_influx_line({"service_restart": 1}, int(time.time())))

    last_push = startup_mod.read_marker(Path(config.LAST_PUSH_MARKER_FILE))
    last_alive = startup_mod.read_marker(Path(config.LAST_ALIVE_MARKER_FILE))
    if last_push is not None:
        state.last_successful_push_ts = last_push
        startup_now = time.time()
        outage = startup_mod.classify_outage(
            now=startup_now, last_push_ts=last_push, last_alive_ts=last_alive,
            gap_threshold_s=config.OUTAGE_GAP_THRESHOLD_S,
        )
        if outage:
            kind, gap_s = outage
            text = f"Outage: {int(gap_s) // 60} min — {kind.value} (v {config.BUILD_VERSION})"
            _grafana_client.push_annotation(
                int(last_push * 1000), int(startup_now * 1000),
                text, reason=kind.value, version=config.BUILD_VERSION,
            )
            events_mod.outage_recorded(_loki_client, gap_seconds=int(gap_s),
                                       reason=kind.value, version=config.BUILD_VERSION)

    _loki_client.flush()

    while not state.shutdown_requested:
        cycle_start = time.perf_counter()
        timestamp = int(time.time())

        fields, any_connected = _collect_probes()
        _update_connection_state(state, any_connected, timestamp)
        fields["collection_duration_ms"] = round((time.perf_counter() - cycle_start) * 1000)

        log.info("Cycle t=%d connected=%s rtt_avg_google=%s duration=%dms",
                 timestamp, fields.get("connected"),
                 fields.get("rtt_avg_google"), fields["collection_duration_ms"])

        startup_mod.write_marker(Path(config.LAST_ALIVE_MARKER_FILE), time.time())
        _push_batch(state, format_influx_line(fields, timestamp), any_connected)

        if _scheduler and _scheduler.should_heartbeat(time.time()):
            uptime_h = round((time.monotonic() - state.start_ts) / 3600, 1)
            events_mod.service_heartbeat(_loki_client, uptime_h=uptime_h)

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
        raise
