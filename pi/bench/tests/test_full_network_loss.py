"""Test 1: full_network_loss — DROP all egress for 12 min via iptables OUTPUT REJECT.

Pass criteria (all three required):
  1. connection_down Loki event during outage
  2. Buffered lines flushed (log_buffer_flushed) after restore
  3. Outage annotation posted to Grafana with duration >= 10 min

Annotation polling uses a 20-min timeout to allow for:
  - 12 min outage
  - 2 min push batch window
  - 1–2 min Grafana ingestion + annotation POST
  - polling slack
"""

import subprocess
import time

from ..harness.snapshot import snapshot_iptables, restore_iptables
from .base import BenchTest

OUTAGE_DURATION_S = 620   # 10m20s — just above the 10-min annotation threshold
ANNOTATION_TIMEOUT_S = 600  # 10 min polling timeout after restore


class Test(BenchTest):
    name = "full_network_loss"
    description = "DROP all egress 12 min; connection_down, buffer flush, outage annotation >= 10 min"
    timeout_s = 1350  # ~22 min hard cap (620s outage + 600s observation + slack)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._rules_file = None

    def inject(self) -> None:
        self._rules_file = snapshot_iptables("full_loss", "pre")
        self.log.warn(
            f"Dropping all egress for {OUTAGE_DURATION_S}s",
            event="bench_inject",
            outage_duration_s=OUTAGE_DURATION_S,
        )
        subprocess.run([
            "iptables", "-I", "OUTPUT", "-j", "REJECT"
        ], check=True)
        time.sleep(OUTAGE_DURATION_S)
        # Restore network before observe so Grafana reads can succeed
        restore_iptables(self._rules_file)
        self._rules_file = None  # Mark as already restored
        self.log.warn("Network restored — beginning observation window", event="bench_inject")

    def observe(self) -> dict:
        inject_end_ms = int(time.time() * 1000)

        # 1. connection_down event (should have fired during outage, flushed on reconnect)
        self.log.info("Polling for connection_down in Loki", event="bench_observe")
        conn_down = self.obs.poll_loki_event(
            event_name="connection_down",
            start_ns=self._inject_start_ns,
            timeout_s=600,
            poll_interval_s=30,
        )
        self.log.info("connection_down confirmed", event="bench_observe")

        # 2. log_buffer_flushed (fired on reconnect when buffered entries are pushed)
        self.log.info("Polling for log_buffer_flushed in Loki", event="bench_observe")
        flush_entry = self.obs.poll_loki_event(
            event_name="log_buffer_flushed",
            start_ns=self._inject_start_ns,
            timeout_s=600,
            poll_interval_s=30,
        )
        self.log.info("log_buffer_flushed confirmed", event="bench_observe")

        # 3. Outage annotation with duration >= 10 min (600 s)
        self.log.info(
            f"Polling for outage annotation (timeout={ANNOTATION_TIMEOUT_S}s)",
            event="bench_observe",
        )
        annotation = self.obs.poll_annotation(
            inject_start_ms=self._inject_start_ms,
            inject_end_ms=inject_end_ms,
            timeout_s=ANNOTATION_TIMEOUT_S,
            poll_interval_s=60,
            min_duration_s=600,
        )
        duration_s = (annotation.get("timeEnd", 0) - annotation.get("time", 0)) / 1000
        self.log.warn(
            f"Outage annotation confirmed: duration={duration_s:.0f}s",
            event="bench_observe",
            annotation_duration_s=round(duration_s),
        )

        return {
            "connection_down_entry": conn_down,
            "flush_entry": flush_entry,
            "annotation_id": annotation.get("id"),
            "annotation_duration_s": round(duration_s),
        }

    def restore(self) -> None:
        if self._rules_file is not None:
            restore_iptables(self._rules_file)
