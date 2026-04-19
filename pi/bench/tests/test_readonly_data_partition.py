"""Test 6: readonly_data_partition — remount /opt/towerwatch/data read-only for 5 min.

Pass: service survives, partition_not_detected / write-fail events emit, clean recovery on remount rw.
"""

import subprocess
import sys
import time

from .base import BenchTest

DATA_MOUNT = "/opt/towerwatch/data"
INJECT_DURATION_S = 300  # 5 min


class Test(BenchTest):
    name = "readonly_data_partition"
    description = "Remount data partition read-only; service survives, write-fail events emitted"
    timeout_s = 600

    def inject(self) -> None:
        if sys.platform == "win32":
            raise Exception("readonly_data_partition test requires Linux/Pi")
        self.log.info(f"Remounting {DATA_MOUNT} read-only", event="bench_inject")
        subprocess.run(["mount", "-o", "remount,ro", DATA_MOUNT], check=True)
        time.sleep(INJECT_DURATION_S)

    def observe(self) -> dict:
        # Service should still be alive
        r = subprocess.run(["systemctl", "is-active", "towerwatch"],
                           capture_output=True, text=True)
        if r.stdout.strip() != "active":
            raise Exception("towerwatch crashed during read-only partition test")

        # Expect partition_not_detected or a write-related WARN
        entry = self.obs.poll_loki_event(
            event_name="partition_not_detected",
            start_ns=self._inject_start_ns,
            timeout_s=300,
            poll_interval_s=30,
        )
        self.log.info("partition_not_detected confirmed", event="bench_observe")
        return {"partition_warn_entry": entry}

    def restore(self) -> None:
        self.log.info(f"Remounting {DATA_MOUNT} read-write", event="bench_restore")
        subprocess.run(["mount", "-o", "remount,rw", DATA_MOUNT], check=False)
        # Give the service time to recover and resume normal writes
        time.sleep(30)
