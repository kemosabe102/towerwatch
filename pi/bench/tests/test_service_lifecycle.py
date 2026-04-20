"""Test 9: service_lifecycle — stop/start/restart, verify service_restarted WARN events."""

import subprocess
import time

from ..harness.service import service_control
from ..harness.observe import ObserveError
from .base import BenchTest

ORDER = 0

class Test(BenchTest):
    name = "service_lifecycle"
    description = "stop → start → restart; verify service_restarted WARN events with BUILD_VERSION arrive in Loki"
    timeout_s = 300

    def inject(self) -> None:
        self.log.info("Stopping towerwatch", event="bench_inject")
        service_control("stop")
        time.sleep(5)
        self.log.info("Starting towerwatch", event="bench_inject")
        service_control("start")
        time.sleep(15)
        self.log.info("Restarting towerwatch", event="bench_inject")
        service_control("restart")
        time.sleep(15)

    def observe(self) -> dict:
        self.log.info("Waiting for service_restarted events in Loki (WARN-level, survives LOKI_PUSH_LEVEL filter)", event="bench_observe")
        # Two restarts (start + restart) — poll for the most recent one
        entry = self.obs.poll_loki_event(
            event_name="service_restarted",
            start_ns=self._inject_start_ns,
            timeout_s=270,
            poll_interval_s=30,
        )
        version = entry.get("labels", {}).get("version") or ""
        self.log.info("service_restarted confirmed", event="bench_observe")
        return {"service_restarted": entry, "version_in_event": version}

    def restore(self) -> None:
        service_control("start", check=False)
