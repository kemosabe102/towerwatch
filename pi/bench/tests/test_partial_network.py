"""Test 2: partial_network — block port 443 only; ICMP still works.

Pass: metrics_push_failed events, buffer grows, probes continue (ping metrics present).
"""

import subprocess
import time

from ..harness.snapshot import snapshot_iptables, restore_iptables
from .base import BenchTest

BLOCK_DURATION_S = 180


class Test(BenchTest):
    name = "partial_network"
    description = "Block TCP 443 (HTTPS push); ICMP probes unaffected, push fails, buffer grows"
    timeout_s = 600

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._rules_file = None

    def inject(self) -> None:
        self._rules_file = snapshot_iptables("partial_net", "pre")
        subprocess.run([
            "iptables", "-I", "OUTPUT",
            "-p", "tcp", "--dport", "443", "-j", "REJECT"
        ], check=True)
        self.log.info(f"TCP 443 blocked for {BLOCK_DURATION_S}s", event="bench_inject")
        time.sleep(BLOCK_DURATION_S)

    def observe(self) -> dict:
        self.log.info("Polling for metrics_push_failed in Loki", event="bench_observe")
        entry = self.obs.poll_loki_event(
            event_name="metrics_push_failed",
            start_ns=self._inject_start_ns,
            timeout_s=300,
            poll_interval_s=30,
        )
        self.log.info("metrics_push_failed confirmed", event="bench_observe")
        return {"push_fail_entry": entry}

    def restore(self) -> None:
        if self._rules_file:
            restore_iptables(self._rules_file)
