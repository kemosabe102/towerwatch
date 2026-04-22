#!/usr/bin/env python3
"""
Towerwatch — 5G Cell Tower Network Quality Monitor

60-second monitoring loop: collects probes, updates state, pushes metrics to Grafana Cloud.
Cross-platform: Raspberry Pi (production) and Windows (dev).
"""

import logging
import sys
import time
from pathlib import Path

import config
import events as events_mod
import grafana as grafana_mod
import loki as loki_mod
import startup as startup_mod
from scheduling import Scheduler
from lifecycle import RuntimeState, configure_logging, install_signal_handlers
from tick import TickContext, collect_probes, update_connection_state, push_batch, format_influx_line

try:
    import credentials
except ImportError:
    print("ERROR: credentials.py not found. Copy credentials.py.example to credentials.py and fill in values.")
    raise SystemExit(1)

log = logging.getLogger("towerwatch")

IS_WINDOWS = sys.platform == "win32"


def main():
    state = RuntimeState()
    configure_logging()
    install_signal_handlers(state)

    loki = loki_mod.LokiClient.from_config(config, credentials)
    grafana = grafana_mod.GrafanaClient.from_config(config, credentials)
    scheduler = Scheduler.from_config(config)
    ctx = TickContext(grafana=grafana, loki=loki, scheduler=scheduler)

    log.info("=== Towerwatch %s ===", "(Windows)" if IS_WINDOWS else "(Raspberry Pi)")
    startup_mod.wait_for_data_partition(Path(config.DATA_DIR))

    events_mod.service_restarted(loki, version=config.BUILD_VERSION,
                                 build_date=config.BUILD_DATE, platform=sys.platform)
    events_mod.service_started(loki, log_level=config.LOKI_PUSH_LEVEL,
                               platform=sys.platform)

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

        log.info("Cycle t=%d connected=%s rtt_avg_google=%s duration=%dms",
                 timestamp, fields.get("connected"),
                 fields.get("rtt_avg_google"), fields["collection_duration_ms"])

        startup_mod.write_marker(Path(config.LAST_ALIVE_MARKER_FILE), time.time())
        push_batch(ctx, state, format_influx_line(fields, timestamp), any_connected)

        if scheduler and scheduler.should_heartbeat(time.time()):
            uptime_h = round((time.monotonic() - state.start_ts) / 3600, 1)
            events_mod.service_heartbeat(loki, uptime_h=uptime_h)

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
