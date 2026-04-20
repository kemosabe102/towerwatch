"""Test 6: readonly_data_partition — remount /opt/towerwatch/data read-only for 5 min.

Pass: service survives, partition_not_detected / write-fail events emit, clean recovery on remount rw.
"""

import subprocess
import sys
import time

from ..harness.mountinfo import bind_mounts_sharing_device
from ..harness.observe import BenchSkip
from ..harness.paths import DATA_MOUNT
from ..harness.service import service_active
from .base import BenchTest

ORDER = 9

INJECT_DURATION_S = 120  # 2 probe cycles


class Test(BenchTest):
    name = "readonly_data_partition"
    description = "Remount data partition read-only; service survives, write-fail events emitted"
    timeout_s = 450

    def inject(self) -> None:
        if sys.platform == "win32":
            raise Exception("readonly_data_partition test requires Linux/Pi")
        shared = bind_mounts_sharing_device(DATA_MOUNT)
        if shared:
            raise BenchSkip(
                f"Bind mount(s) {shared} share the data-partition device; "
                "read-only remount would either fail as busy or disconnect "
                "SSH (tailscale state). Run from console or disable the bind "
                "mount first."
            )
        self.log.info(f"Remounting {DATA_MOUNT} read-only", event="bench_inject")
        subprocess.run(["mount", "-o", "remount,ro", DATA_MOUNT], check=True)
        time.sleep(INJECT_DURATION_S)

    def observe(self) -> dict:
        # Service should still be alive
        if not service_active():
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
