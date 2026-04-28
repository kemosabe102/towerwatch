"""
Per-tick probe collection and metric push logic.

All seams are injected: `TickContext` carries the grafana/loki/scheduler
collaborators plus a duck-typed `events` namespace and a `Clock`. Functions
that branch on config constants (`PUSH_BATCH_SIZE`, `OUTAGE_GAP_THRESHOLD_S`,
`LAST_PUSH_MARKER_FILE`, `BUILD_VERSION`) accept them as keyword arguments
with production defaults drawn from `config.py`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from towerwatch import config as _config
from towerwatch import events as events_mod
from towerwatch import startup as startup_mod
from towerwatch.clock import Clock, SystemClock
from towerwatch.probes.cloudflare import measure_http_throughput, measure_http_upload
from towerwatch.probes.dns import measure_dns
from towerwatch.probes.gateway import poll_gateway
from towerwatch.probes.http import measure_http_latency
from towerwatch.probes.ping import run_ping
from towerwatch.probes.tcp import measure_tcp_connect

log = logging.getLogger("towerwatch")


def _default_clock() -> Clock:
    return SystemClock()


@dataclass
class TickContext:
    """Per-tick orchestration context. All collaborators are duck-typed (Any) so
    tests can pass hand-written fakes without inheriting from production classes —
    the whole DI pattern here assumes structural typing over nominal."""

    grafana: Any = None
    loki: Any = None
    scheduler: Any = None
    events: Any = events_mod
    clock: Clock = field(default_factory=_default_clock)


def _common_tags() -> str:
    """Influx tag set baked into every line — `host`, `carrier`, `connection_type`.

    Tag values must not contain spaces or commas in line protocol; the slug
    helper in config.py guarantees that for carrier/connection_type. host is
    set by the operator and assumed clean.
    """
    return (
        f"host={_config.INFLUX_HOST_TAG},"
        f"carrier={_config.INFLUX_CARRIER_TAG},"
        f"connection_type={_config.INFLUX_CONNECTION_TYPE_TAG}"
    )


def format_influx_line(fields: dict, timestamp: int) -> str:
    parts = [f"{k}={v}" for k, v in fields.items() if v is not None]
    return f"{_config.INFLUX_MEASUREMENT},{_common_tags()} " + ",".join(parts) + f" {timestamp}"


def format_build_info_line(
    ts: int,
    *,
    version: str | None = None,
    build_date: str | None = None,
    link_max_download_mbps: int | None = None,
    link_max_upload_mbps: int | None = None,
) -> str:
    """Influx line for the `towerwatch_build_info` Prom gauge.

    `version`, `build_date`, and `link_max_*` are emitted as Influx **tags**
    (not fields) so Grafana Cloud Prom ingest turns them into metric labels.
    Tag values are unquoted strings by spec; field string values are not (see
    the pinned characterization test in test_influx_line_format.py).

    `link_max_download_mbps` / `link_max_upload_mbps` carry per-site link
    capacity so the dashboard can `label_values()` them into templating
    variables for gauge max + Saturation Golden Signal.
    """
    v = version if version is not None else _config.BUILD_VERSION
    d = build_date if build_date is not None else _config.BUILD_DATE
    ld = (
        link_max_download_mbps
        if link_max_download_mbps is not None
        else _config.LINK_MAX_DOWNLOAD_MBPS
    )
    lu = link_max_upload_mbps if link_max_upload_mbps is not None else _config.LINK_MAX_UPLOAD_MBPS
    return (
        f"{_config.INFLUX_MEASUREMENT},"
        f"{_common_tags()},"
        f"version={v},"
        f"build_date={d},"
        f"link_max_download_mbps={ld},"
        f"link_max_upload_mbps={lu} "
        f"build_info=1 {ts}"
    )


def format_speedtest_line(
    ts: int,
    *,
    download_mbps: float,
    upload_mbps: float,
    triggered_by: str,
    download_bytes: int = 0,
    upload_bytes: int = 0,
) -> str:
    """Influx line for a manual Cloudflare speedtest run.

    `triggered_by` is emitted as a tag (not a field), matching the build_info
    pattern — Grafana Cloud Prom ingest turns tags into metric labels so
    dashboards can group/filter by operator.

    `*_bytes` fields feed the dashboard's "Speedtest Data (7d)" stat — they
    let the same query that sums scheduled-probe bytes also account for
    operator-triggered runs.
    """
    return (
        f"{_config.INFLUX_MEASUREMENT},"
        f"{_common_tags()},"
        f"triggered_by={triggered_by} "
        f"speedtest_download_mbps={download_mbps},"
        f"speedtest_upload_mbps={upload_mbps},"
        f"speedtest_download_bytes={download_bytes}i,"
        f"speedtest_upload_bytes={upload_bytes}i {ts}"
    )


def update_connection_state(ctx: TickContext, state, connected: bool, timestamp: int) -> None:
    if connected and not state.connected:
        if state.outage_start:
            duration = timestamp - state.outage_start
            state.total_outage_s += duration
            ctx.events.connection_restored(ctx.loki, down_duration_s=duration)
        state.outage_start = 0
    elif not connected and state.connected:
        state.outage_start = timestamp
        state.outage_count += 1
        log.warning("Connection DOWN")
        ctx.events.connection_down(ctx.loki)
    state.connected = connected


def collect_probes(ctx: TickContext) -> tuple[dict, bool]:
    fields = {}
    now = ctx.clock.time()

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
        # Download + upload run back-to-back on the same scheduler tick so both
        # directions get matched cadence and a single time-series row.
        fields.update(measure_http_throughput())
        fields.update(measure_http_upload())

    return fields, any_connected


def push_batch(
    ctx: TickContext,
    state,
    line: str,
    any_connected: bool,
    *,
    batch_size: int | None = None,
    gap_threshold_s: int | None = None,
    marker_file: str | Path | None = None,
    build_version: str | None = None,
) -> None:
    if batch_size is None:
        batch_size = _config.PUSH_BATCH_SIZE
    if gap_threshold_s is None:
        gap_threshold_s = _config.OUTAGE_GAP_THRESHOLD_S
    if marker_file is None:
        marker_file = _config.LAST_PUSH_MARKER_FILE
    if build_version is None:
        build_version = _config.BUILD_VERSION

    state.metric_batch.append(line)
    if len(state.metric_batch) < batch_size:
        return
    batch = state.metric_batch[:]
    state.metric_batch.clear()
    if not any_connected:
        return
    if not ctx.grafana or not ctx.grafana.push_metrics(batch):
        log.warning("Metric push failed — batch dropped (%d lines)", len(batch))
        return
    log.info("Pushed %d lines", len(batch))
    now = ctx.clock.time()
    gap = now - state.last_successful_push_ts
    if gap >= gap_threshold_s:
        gap_s = int(gap)
        text = f"Outage: {gap_s // 60} min — network_unreachable (v {build_version})"
        if ctx.grafana:
            ctx.grafana.push_annotation(
                int(state.last_successful_push_ts * 1000),
                int(now * 1000),
                text,
                reason="network_unreachable",
                version=build_version,
            )
        ctx.events.outage_recorded(
            ctx.loki,
            gap_seconds=gap_s,
            reason="network_unreachable",
            version=build_version,
        )
    state.last_successful_push_ts = now
    startup_mod.write_marker(Path(marker_file), now, atomic=True)
    if ctx.loki:
        ctx.loki.flush()
