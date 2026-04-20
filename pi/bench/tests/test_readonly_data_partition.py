"""Test 6: readonly_data_partition — remount /opt/towerwatch/data read-only for 5 min.

Pass: service survives, partition_not_detected / write-fail events emit, clean recovery on remount rw.
"""

import subprocess
import sys
import time
from pathlib import Path

from ..harness.observe import BenchSkip
from .base import BenchTest

DATA_MOUNT = "/opt/towerwatch/data"
INJECT_DURATION_S = 120  # 2 probe cycles


def _bind_mounts_sharing_device(data_mount: str) -> list[str]:
    """Return other mount points backed by the same device as data_mount.

    A read-only remount fails if any other active mount (typically a bind mount
    like /var/lib/tailscale → /opt/towerwatch/data/tailscale-state/) holds open
    write handles on the same underlying device. Detect this up-front so we can
    skip cleanly instead of failing with "mount point is busy".
    """
    try:
        entries = Path("/proc/self/mountinfo").read_text().splitlines()
    except OSError:
        return []
    data_dev = None
    for line in entries:
        # mountinfo format: <id> <parent> <major:minor> <root> <mount-point> ...
        parts = line.split()
        if len(parts) >= 5 and parts[4] == data_mount:
            data_dev = parts[2]
            break
    if not data_dev:
        return []
    shared = []
    for line in entries:
        parts = line.split()
        if len(parts) >= 5 and parts[2] == data_dev and parts[4] != data_mount:
            shared.append(parts[4])
    return shared


class Test(BenchTest):
    name = "readonly_data_partition"
    description = "Remount data partition read-only; service survives, write-fail events emitted"
    timeout_s = 450

    def inject(self) -> None:
        if sys.platform == "win32":
            raise Exception("readonly_data_partition test requires Linux/Pi")
        shared = _bind_mounts_sharing_device(DATA_MOUNT)
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
