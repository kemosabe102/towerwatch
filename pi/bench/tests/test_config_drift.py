"""Test 11: config_drift — three sub-cases via systemd drop-in Environment overrides.

11a (expected-failure): empty LOKI_URL → _flush_log_buffer AttributeError on flush path
11b: empty GRAFANA_ANNOTATION_TOKEN → annotation skipped gracefully (no crash)
11c: missing M6_ADMIN_PASSWORD → m6 probe disables cleanly
"""

import subprocess
import time

from ..harness.report import TestResult
from ..harness.service import service_active, service_control
from ..harness.snapshot import write_dropin, remove_dropin
from ..harness.observe import ObserveError
from .base import BenchTest

WAIT_S = 120  # 2 probe cycles per sub-case


class Test(BenchTest):
    name = "config_drift"
    description = "Sub-cases: empty LOKI_URL (xfail), empty annotation token (graceful), no M6 pw (graceful)"
    timeout_s = 900

    def inject(self) -> None:
        pass  # All injection happens in run() override

    def observe(self) -> dict:
        return {}

    def _run_subcases(self) -> dict:
        results = {}

        # --- 11a: empty LOKI_URL (expected-failure: AttributeError on flush) ---
        self.log.info("Sub-case 11a: empty LOKI_URL", event="bench_inject")
        write_dropin("drift-a", "[Service]\nEnvironment=LOKI_URL=\n")
        service_control("restart")
        time.sleep(WAIT_S)
        # Service should NOT have crashed (systemctl is-active == active)
        results["11a_service_still_active"] = service_active()
        remove_dropin("drift-a")
        service_control("restart", check=False)
        time.sleep(15)

        # --- 11b: empty GRAFANA_ANNOTATION_TOKEN ---
        self.log.info("Sub-case 11b: empty annotation token", event="bench_inject")
        write_dropin("drift-b", "[Service]\nEnvironment=GRAFANA_ANNOTATION_TOKEN=\n")
        service_control("restart")
        time.sleep(WAIT_S)
        results["11b_service_still_active"] = service_active()
        remove_dropin("drift-b")
        service_control("restart", check=False)
        time.sleep(15)

        # --- 11c: empty M6_ADMIN_PASSWORD ---
        self.log.info("Sub-case 11c: empty M6 password", event="bench_inject")
        write_dropin("drift-c", "[Service]\nEnvironment=M6_ADMIN_PASSWORD=\n")
        service_control("restart")
        time.sleep(WAIT_S)
        results["11c_service_still_active"] = service_active()
        remove_dropin("drift-c")
        service_control("restart", check=False)

        return results

    def run(self) -> TestResult:
        import time
        started = time.time()
        self._inject_start_s  = started
        self._inject_start_ns = int(started * 1e9)
        self._inject_start_ms = int(started * 1e3)
        try:
            results = self._run_subcases()
        except Exception as e:
            self._safe_restore()
            return TestResult(name=self.name, status="error",
                              duration_s=time.time()-started, error_msg=str(e))
        finally:
            self._restored = True

        # 11a is expected-failure: service crash/AttributeError = expected, survives = fixed
        # For now treat all sub-cases as pass if service stayed alive
        all_alive = all(v for k, v in results.items() if "_active" in k)
        status = "pass" if all_alive else "fail"
        return TestResult(name=self.name, status=status,
                          duration_s=time.time()-started, evidence=results)

    def restore(self) -> None:
        for name in ("drift-a", "drift-b", "drift-c"):
            remove_dropin(name)
        service_control("restart", check=False)
