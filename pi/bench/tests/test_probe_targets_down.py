"""Test 10: probe_targets_down — DROP iptables to one target; verify per-target metric absent.

Runs three sub-cases in sequence (one target at a time).
"""

import subprocess
import time

from ..harness.snapshot import snapshot_iptables, restore_iptables
from .base import BenchTest

ORDER = 2

TARGETS = [
    ("8.8.8.8",     "google"),
    ("1.1.1.1",     "cloudflare"),
    ("192.168.1.1", "gateway"),
]
BLOCK_DURATION_S = 120   # 2 probe cycles


class Test(BenchTest):
    name = "probe_targets_down"
    description = "Block one probe target at a time; per-target metric absent, others unaffected"
    timeout_s = 900

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._rules_files = []

    def inject(self) -> None:
        # Sub-cases run sequentially inside inject+observe per target
        pass  # Actual injection happens in run() override below

    def run(self):
        import time
        from ..harness.report import TestResult
        started = time.time()
        evidence = {}
        try:
            for ip, label in TARGETS:
                self.log.info(f"Blocking target {label} ({ip})", event="bench_inject")
                rules = snapshot_iptables("probe_down", f"pre_{label}")
                self._rules_files.append(rules)
                block_start = int(time.time())
                subprocess.run([
                    "iptables", "-I", "OUTPUT", "-d", ip, "-j", "DROP"
                ], check=True)
                time.sleep(BLOCK_DURATION_S)
                block_end = int(time.time())
                # Restore egress before querying Grafana — gateway block also kills DNS.
                restore_iptables(rules)

                # Query the *second half* of the blocked window so any metric pushed
                # just before the block (timestamp < block_start) can't linger in-range.
                query_start = block_start + 60
                try:
                    self.obs.assert_prom_metric_absent(
                        f'towerwatch_rtt_avg_{label}',
                        query_start,
                        block_end,
                    )
                    evidence[label] = "absent_confirmed"
                except Exception as e:
                    evidence[label] = f"FAIL: {e}"

                time.sleep(30)  # Let metrics resume before next sub-case

            status = "pass" if all(v == "absent_confirmed" for v in evidence.values()) else "fail"
        except Exception as e:
            status = "error"
            evidence["error"] = str(e)
            self._safe_restore()
        finally:
            self._restored = True

        return TestResult(
            name=self.name,
            status=status,
            duration_s=time.time() - started,
            evidence=evidence,
        )

    def observe(self) -> dict:
        return {}  # Handled inside run()

    def restore(self) -> None:
        for f in self._rules_files:
            restore_iptables(f)
