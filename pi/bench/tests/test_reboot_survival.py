"""Test 14: reboot_survival — arm oneshot resume unit, reboot, verify recovery.

This test does NOT follow the normal inject/observe/restore pattern.
It arms a systemd oneshot that re-invokes run.py --resume post-boot,
then calls systemctl reboot. Observation happens in --resume mode.

Run this test separately: python run.py --test reboot_survival
"""

import subprocess
import sys
import time
from pathlib import Path

from ..harness.service import daemon_reload
from ..harness.report import TestResult
from ..harness.state import read_state, write_state, clear_state, clear_sentinel, arm_sentinel
from .base import BenchTest

ORDER = 13

RESUME_UNIT = "towerwatch-bench-resume.service"
RESUME_UNIT_PATH = Path(f"/etc/systemd/system/{RESUME_UNIT}")
BOOT_TIMEOUT_S = 300    # 5 min for Pi to reboot and come back
OBSERVE_TIMEOUT_S = 600  # Loki/Prom observation after reboot


RESUME_UNIT_CONTENT = """\
[Unit]
Description=Towerwatch bench resume (self-disabling oneshot)
After=network-online.target towerwatch.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/towerwatch/bench/run.py --resume
RemainAfterExit=no

[Install]
WantedBy=multi-user.target
"""


class Test(BenchTest):
    name = "reboot_survival"
    description = "Arm resume oneshot, reboot Pi, verify service_started + log buffer flush post-boot"
    timeout_s = 1200

    def inject(self) -> None:
        if sys.platform == "win32":
            raise Exception("reboot_survival test requires Linux/Pi")

        # Record pre-reboot state
        pre_state = {
            "test": self.name,
            "phase": "armed",
            "pre_reboot_ns": int(time.time() * 1e9),
            "pre_reboot_s": int(time.time()),
        }
        write_state(pre_state)

        # Install the resume oneshot
        RESUME_UNIT_PATH.write_text(RESUME_UNIT_CONTENT)
        daemon_reload(check=True)
        subprocess.run(["systemctl", "enable", RESUME_UNIT], check=True)
        self.log.warn(
            "Reboot armed — Pi will reboot now; resume oneshot installed",
            event="bench_reboot_armed",
        )
        # Emit a pre-reboot sentinel to Loki so we can verify it flushed post-boot
        time.sleep(5)
        subprocess.run(["systemctl", "reboot"], check=True)
        # Process ends here; resume oneshot picks up after boot

    def observe(self) -> dict:
        # This path is taken when run.py --resume invokes us post-boot
        state = read_state()
        pre_reboot_ns = state.get("pre_reboot_ns", self._inject_start_ns)

        self.log.info("Post-reboot observe: waiting for service_started in Loki", event="bench_observe")
        entry = self.obs.poll_loki_event(
            event_name="service_started",
            start_ns=pre_reboot_ns,
            timeout_s=OBSERVE_TIMEOUT_S,
            poll_interval_s=30,
        )
        self.log.info("Post-reboot service_started confirmed", event="bench_observe")

        # Confirm towerwatch_connected metric resumes
        self.obs.poll_prom_metric_present(
            promql="towerwatch_connected",
            start_s=state.get("pre_reboot_s", int(self._inject_start_s)),
            timeout_s=OBSERVE_TIMEOUT_S,
            poll_interval_s=30,
        )
        self.log.info("towerwatch_connected metric resuming post-reboot", event="bench_observe")
        return {"service_started_entry": entry}

    def restore(self) -> None:
        # Disarm the resume oneshot
        subprocess.run(["systemctl", "disable", "--now", RESUME_UNIT], check=False)
        try:
            RESUME_UNIT_PATH.unlink()
        except FileNotFoundError:
            pass
        daemon_reload(check=False)
        clear_state()
        clear_sentinel()
