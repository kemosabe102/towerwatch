"""
Per-tick probe collection and metric push logic.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import config as _config
import events as events_mod
import startup as startup_mod
from probes.ping import run_ping
from probes.tcp import measure_tcp_connect
from probes.dns import measure_dns
from probes.http import measure_http_latency, measure_http_throughput
from probes.gateway import poll_gateway

if TYPE_CHECKING:
    from grafana import GrafanaClient
    from loki import LokiClient
    from scheduling import Scheduler

log = logging.getLogger("towerwatch")


@dataclass
class TickContext:
    grafana: GrafanaClient | None
    loki: LokiClient | None
    scheduler: Scheduler | None


def format_influx_line(fields: dict, timestamp: int) -> str:
    parts = [f"{k}={v}" for k, v in fields.items() if v is not None]
    return (
        f"{_config.INFLUX_MEASUREMENT},host={_config.INFLUX_HOST_TAG} "
        + ",".join(parts)
        + f" {timestamp}"
    )


def update_connection_state(ctx: TickContext, state, connected: bool, timestamp: int) -> None:
    if connected and not state.connected:
        if state.outage_start:
            duration = timestamp - state.outage_start
            state.total_outage_s += duration
            events_mod.connection_restored(ctx.loki, down_duration_s=duration)
        state.outage_start = 0
    elif not connected and state.connected:
        state.outage_start = timestamp
        state.outage_count += 1
        log.warning("Connection DOWN")
        events_mod.connection_down(ctx.loki)
    state.connected = connected


def collect_probes(ctx: TickContext) -> tuple[dict, bool]:
    fields = {}
    now = time.time()

    any_connected = False
    for target_ip, target_label in _config.PROBE_TARGETS:
        ping = run_ping(target_ip)
        any_connected = any_connected or ping["connected"]
        for metric, value in ping.items():
            if metric == "connected":
                fields[f"connected_{target_label}"] = 1 if value else 0
            else:
                fields[f"{metric}_{target_label}"] = value
    fields["connected"] = 1 if any_connected else 0
    fields["metric_interval_s"] = _config.METRIC_INTERVAL_S

    fields["tcp_connect_ms"] = measure_tcp_connect()
    for ns in _config.DNS_TARGETS:
        fields[f"dns_resolve_ms_{ns.replace('.', '_')}"] = measure_dns(ns)

    fields.update(poll_gateway())

    if ctx.scheduler and ctx.scheduler.should_run_http_latency(now):
        fields["http_latency_ms"] = measure_http_latency()
    if ctx.scheduler and ctx.scheduler.should_run_throughput(now):
        fields.update(measure_http_throughput())

    return fields, any_connected


def push_batch(ctx: TickContext, state, line: str, any_connected: bool) -> None:
    state.metric_batch.append(line)
    if len(state.metric_batch) < _config.PUSH_BATCH_SIZE:
        return
    batch = state.metric_batch[:]
    state.metric_batch.clear()
    if not any_connected:
        return
    if not ctx.grafana or not ctx.grafana.push_metrics(batch):
        log.warning("Metric push failed — batch dropped (%d lines)", len(batch))
        return
    log.info("Pushed %d lines", len(batch))
    now = time.time()
    gap = now - state.last_successful_push_ts
    if gap >= _config.OUTAGE_GAP_THRESHOLD_S:
        gap_s = int(gap)
        text = f"Outage: {gap_s // 60} min — network_unreachable (v {_config.BUILD_VERSION})"
        if ctx.grafana:
            ctx.grafana.push_annotation(
                int(state.last_successful_push_ts * 1000), int(now * 1000),
                text, reason="network_unreachable", version=_config.BUILD_VERSION,
            )
        events_mod.outage_recorded(ctx.loki, gap_seconds=gap_s,
                                   reason="network_unreachable", version=_config.BUILD_VERSION)
    state.last_successful_push_ts = now
    startup_mod.write_marker(Path(_config.LAST_PUSH_MARKER_FILE), now, atomic=True)
    if ctx.loki:
        ctx.loki.flush()
