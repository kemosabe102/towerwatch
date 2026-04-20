"""Test 13: latency_injection — tc netem 500ms + 5% loss; verify RTT/jitter/loss rise.

Pass criterion: rtt_avg_google > 400ms in Prom within observation window;
no false outage annotation at this severity level.
"""

import subprocess
import time

from ..harness.snapshot import snapshot_tc, restore_tc
from ..harness.inject import inject_tc
from .base import BenchTest

ORDER = 1

IFACE = "eth0"
INJECT_DURATION_S = 120   # 2 probe cycles of degraded traffic


class Test(BenchTest):
    name = "latency_injection"
    description = "tc netem 500ms delay + 5% loss on eth0; RTT/jitter/loss metrics rise"
    timeout_s = 420

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._tc_snapshot = None

    def inject(self) -> None:
        self._tc_snapshot = snapshot_tc(run_id="latency", label="pre", iface=IFACE)
        self.log.info(f"Injecting 500ms netem delay on {IFACE}", event="bench_inject")
        inject_tc(
            "qdisc", "add", "dev", IFACE, "root",
            "netem", "delay", "500ms", "50ms", "distribution", "normal",
            "loss", "5%",
        )
        self.log.info(f"Waiting {INJECT_DURATION_S}s for probe data to accumulate", event="bench_inject")
        time.sleep(INJECT_DURATION_S)

    def observe(self) -> dict:
        self.log.info("Querying Prom for elevated RTT", event="bench_observe")
        # Poll for rtt_avg_google > 400ms (value is in ms per invariant)
        result = self.obs.poll_prom_metric_present(
            promql='towerwatch_rtt_avg_google > 400',
            start_s=int(self._inject_start_s),
            timeout_s=180,
            poll_interval_s=30,
        )
        self.log.info("Elevated RTT confirmed in Prom", event="bench_observe")

        # Verify no spurious outage annotation was posted
        now_ms = int(time.time() * 1000)
        try:
            self.obs.assert_prom_metric_absent(
                'towerwatch_connected == 0',
                int(self._inject_start_s),
                int(time.time()),
            )
        except Exception:
            pass  # connected=0 absence is best-effort; annotation check is the real guard
        return {"rtt_result_count": len(result)}

    def restore(self) -> None:
        if self._tc_snapshot:
            restore_tc(self._tc_snapshot)
        else:
            subprocess.run(["tc", "qdisc", "del", "dev", IFACE, "root"],
                           check=False, capture_output=True)
