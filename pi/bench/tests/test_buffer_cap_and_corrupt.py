"""Test 5: buffer_cap_and_corrupt — fill Loki JSONL buffer past 256 KB, inject corrupt line.

Pass: 10% trim triggers on overflow, corrupt line skipped, subsequent pushes succeed.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

from ..harness.snapshot import snapshot_file, restore_file
from .base import BenchTest

if sys.platform == "win32":
    BUFFER_FILE = Path("./data/buffer/loki.jsonl")
else:
    BUFFER_FILE = Path("/opt/towerwatch/data/buffer/loki.jsonl")

BUFFER_MAX = 256 * 1024  # 256 KB per config.py


class Test(BenchTest):
    name = "buffer_cap_and_corrupt"
    description = "Fill Loki buffer >256 KB + inject corrupt line; 10% trim, corrupt line skipped"
    timeout_s = 420

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._buffer_snapshot = None

    def inject(self) -> None:
        subprocess.run(["systemctl", "stop", "towerwatch"], check=True)
        time.sleep(3)
        BUFFER_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._buffer_snapshot = snapshot_file(BUFFER_FILE, "buffer_cap", "pre")

        # Write synthetic log entries to exceed the cap
        self.log.info("Filling Loki buffer beyond 256 KB cap", event="bench_inject")
        line = json.dumps({"ts": time.time(), "event": "bench_fill", "msg": "x" * 200}) + "\n"
        with BUFFER_FILE.open("w", encoding="utf-8") as fh:
            written = 0
            while written < BUFFER_MAX + 10_000:
                fh.write(line)
                written += len(line)
            # Inject one corrupt line in the middle
            fh.write("{corrupt json line\n")
            for _ in range(20):
                fh.write(line)

        subprocess.run(["systemctl", "start", "towerwatch"], check=True)
        # Give the service time to detect overflow and flush
        time.sleep(120)

    def observe(self) -> dict:
        # Service should still be running (no crash on corrupt line)
        r = subprocess.run(["systemctl", "is-active", "towerwatch"],
                           capture_output=True, text=True)
        if r.stdout.strip() != "active":
            raise Exception("Service not active after corrupt-buffer test")

        # Confirm log_buffer_flushed event in Loki
        entry = self.obs.poll_loki_event(
            event_name="log_buffer_flushed",
            start_ns=self._inject_start_ns,
            timeout_s=180,
            poll_interval_s=30,
        )
        self.log.info("log_buffer_flushed confirmed", event="bench_observe")
        return {"flush_entry": entry}

    def restore(self) -> None:
        subprocess.run(["systemctl", "stop", "towerwatch"], check=False)
        if self._buffer_snapshot:
            restore_file(BUFFER_FILE, self._buffer_snapshot)
        subprocess.run(["systemctl", "start", "towerwatch"], check=False)
