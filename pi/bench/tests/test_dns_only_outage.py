"""Test 12: dns_only_outage — block UDP/TCP 53 to both DNS targets; dns_failed events, others unaffected."""

import subprocess
import time

from ..harness.snapshot import snapshot_iptables, restore_iptables
from .base import BenchTest

DNS_TARGETS = ["8.8.8.8", "1.1.1.1"]
BLOCK_DURATION_S = 120   # 2 probe cycles


class Test(BenchTest):
    name = "dns_only_outage"
    description = "Block port 53 UDP+TCP to DNS targets; dns_failed events, TCP/ping unaffected"
    timeout_s = 420

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._rules_file = None

    def inject(self) -> None:
        self._rules_file = snapshot_iptables("dns_outage", "pre")
        for ip in DNS_TARGETS:
            for proto in ("udp", "tcp"):
                subprocess.run([
                    "iptables", "-I", "OUTPUT",
                    "-d", ip, "-p", proto, "--dport", "53", "-j", "DROP"
                ], check=True)
        self.log.info(f"DNS blocked to {DNS_TARGETS} for {BLOCK_DURATION_S}s", event="bench_inject")
        time.sleep(BLOCK_DURATION_S)

    def observe(self) -> dict:
        self.log.info("Polling for dns_failed event in Loki", event="bench_observe")
        entry = self.obs.poll_loki_event(
            event_name="dns_failed",
            start_ns=self._inject_start_ns,
            timeout_s=180,
            poll_interval_s=30,
        )
        self.log.info("dns_failed confirmed", event="bench_observe")

        # TCP probe should still be present (not blocked)
        tcp_result = self.obs.poll_prom_metric_present(
            promql='towerwatch_tcp_connect_ms',
            start_s=int(self._inject_start_s),
            timeout_s=120,
            poll_interval_s=30,
        )
        self.log.info("TCP metric still present — confirmed DNS-only impact", event="bench_observe")
        return {"dns_failed_entry": entry, "tcp_data_points": len(tcp_result)}

    def restore(self) -> None:
        if self._rules_file:
            restore_iptables(self._rules_file)
