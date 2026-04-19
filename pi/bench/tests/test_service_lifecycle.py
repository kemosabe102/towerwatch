"""Test 9: service_lifecycle — stop/start/restart, verify service_started + service_restarted events."""

import subprocess
import time

from ..harness.observe import ObserveError
from .base import BenchTest


class Test(BenchTest):
    name = "service_lifecycle"
    description = "stop → start → restart; verify service_started / service_restarted WARN events with BUILD_VERSION"
    timeout_s = 300

    def inject(self) -> None:
        self.log.info("Stopping towerwatch", event="bench_inject")
        subprocess.run(["systemctl", "stop", "towerwatch"], check=True)
        time.sleep(5)
        self.log.info("Starting towerwatch", event="bench_inject")
        subprocess.run(["systemctl", "start", "towerwatch"], check=True)
        # Give the service time to boot and emit its startup event
        time.sleep(15)
        self.log.info("Restarting towerwatch", event="bench_inject")
        subprocess.run(["systemctl", "restart", "towerwatch"], check=True)
        time.sleep(15)

    def observe(self) -> dict:
        self.log.info("Waiting for service_started event in Loki", event="bench_observe")
        # Allow up to 5 min for Loki ingestion after restart
        entry = self.obs.poll_loki_event(
            event_name="service_started",
            start_ns=self._inject_start_ns,
            timeout_s=300,
            poll_interval_s=30,
        )
        self.log.info("service_started confirmed", event="bench_observe")

        restarted = self.obs.poll_loki_event(
            event_name="service_restarted",
            start_ns=self._inject_start_ns,
            timeout_s=300,
            poll_interval_s=30,
        )
        self.log.info("service_restarted confirmed", event="bench_observe")
        return {"service_started": entry, "service_restarted": restarted}

    def restore(self) -> None:
        # Ensure service is running regardless of what happened
        subprocess.run(["systemctl", "start", "towerwatch"], check=False)
