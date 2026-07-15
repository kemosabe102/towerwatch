"""Main tick loop. Takes a composed `TickContext` and drives the 60s cycle.

The body here is the former `pi/towerwatch.py:main()` post-composition.
Tests drive `run_loop` directly with a fake context and a state whose
`shutdown_requested` flips after N ticks.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

from towerwatch import config
from towerwatch import events as events_mod
from towerwatch import startup as startup_mod
from towerwatch.lifecycle import RuntimeState
from towerwatch.tick import (
    TickContext,
    collect_probes,
    format_band_sig_line,
    format_build_info_line,
    format_influx_line,
    handle_egress_check,
    handle_gateway_reresolution,
    push_batch,
    update_connection_state,
)

log = logging.getLogger("towerwatch")

IS_WINDOWS = sys.platform == "win32"


def run_loop(ctx: TickContext, state: RuntimeState) -> None:
    """Run the monitoring loop until `state.shutdown_requested` is set."""
    loki = ctx.loki
    grafana = ctx.grafana
    scheduler = ctx.scheduler

    log.info("=== Towerwatch %s ===", "(Windows)" if IS_WINDOWS else "(Raspberry Pi)")
    startup_mod.wait_for_data_partition(Path(config.DATA_DIR))

    events_mod.service_restarted(
        loki,
        version=config.BUILD_VERSION,
        build_date=config.BUILD_DATE,
        platform=sys.platform,
    )
    # Resolve the gateway at startup with retry so a boot race (config imported
    # before DHCP installed the default route) can't freeze us on the fallback.
    # On Windows / with an override this is effectively immediate.
    startup_gateway_ip = ctx.gateway_resolver.resolve_with_retry()
    events_mod.service_started(
        loki,
        log_level=config.LOKI_PUSH_LEVEL,
        platform=sys.platform,
        gateway_ip=startup_gateway_ip,
    )

    state.metric_batch.append(format_influx_line({"service_restart": 1}, int(time.time())))

    last_push = startup_mod.reconcile_previous_outage(grafana, loki, config)
    if last_push is not None:
        state.last_successful_push_ts = last_push

    loki.flush()

    if not IS_WINDOWS and config.STARTUP_GRACE_S > 0:
        log.info("Startup grace period: waiting %ds for network to settle", config.STARTUP_GRACE_S)
        time.sleep(config.STARTUP_GRACE_S)

    while not state.shutdown_requested:
        cycle_start = time.perf_counter()
        timestamp = int(time.time())

        fields, any_connected = collect_probes(ctx)
        update_connection_state(ctx, state, any_connected, timestamp)
        fields["collection_duration_ms"] = round((time.perf_counter() - cycle_start) * 1000)

        log.info(
            "Cycle t=%d connected=%s rtt_avg_google=%s duration=%dms",
            timestamp,
            fields.get("connected"),
            fields.get("rtt_avg_google"),
            fields["collection_duration_ms"],
        )

        startup_mod.write_marker(Path(config.LAST_ALIVE_MARKER_FILE), time.time())
        # Re-resolve the gateway (heals a frozen-IP boot race live) and surface
        # the current IP on build_info so the dashboard shows which IP is probed.
        gateway_ip = handle_gateway_reresolution(ctx)
        # Egress-IP / failover check (scheduled, low cadence); surfaces the public
        # IP on build_info + fires a change-event on a flip (LTE-failover signal).
        # The IP is a build_info tag; egress_cgnat is a metric field on this tick's
        # line — both held between checks so each series stays continuous.
        egress = handle_egress_check(ctx, state)
        fields.update(egress.fields)
        state.metric_batch.append(
            format_build_info_line(timestamp, gateway_ip=gateway_ip, egress_ip=egress.ip)
        )
        band_sig_line = format_band_sig_line(fields, timestamp)
        if band_sig_line is not None:
            state.metric_batch.append(band_sig_line)
        push_batch(ctx, state, format_influx_line(fields, timestamp), any_connected)

        if scheduler and scheduler.should_heartbeat(time.time()):
            uptime_h = round((time.monotonic() - state.start_ts) / 3600, 1)
            events_mod.service_heartbeat(
                loki,
                uptime_h=uptime_h,
                version=config.BUILD_VERSION,
                build_date=config.BUILD_DATE,
            )

        elapsed = time.perf_counter() - cycle_start
        time.sleep(max(0, config.METRIC_INTERVAL_S - elapsed))

    log.info("Shutdown complete")
