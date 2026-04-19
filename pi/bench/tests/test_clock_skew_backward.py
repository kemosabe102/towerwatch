"""Test 8: clock_skew_backward — step clock −30 min.

Verifies the gap-clamping guardrail: after a backward clock step, the
startup-gap check (startup_now - last_push >= OUTAGE_GAP_THRESHOLD_S)
goes negative and no bogus annotation is posted.
"""

import time

from ..harness.snapshot import snapshot_clock, step_clock, restore_clock
from ..harness.observe import ObserveError
from .base import BenchTest

SKEW_S = -1800   # −30 minutes
OBSERVE_DURATION_S = 120


class Test(BenchTest):
    name = "clock_skew_backward"
    description = "Step clock −30 min; verify no bogus (negative-duration) annotation is posted"
    timeout_s = 420

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._skewed = False

    def inject(self) -> None:
        snapshot_clock()
        self.log.info(f"Stepping clock backward {abs(SKEW_S)}s", event="bench_inject")
        step_clock(SKEW_S)
        self._skewed = True
        time.sleep(OBSERVE_DURATION_S)

    def observe(self) -> dict:
        restore_clock()
        self._skewed = False
        time.sleep(90)

        # Pass: no annotation with negative/zero duration was posted during the skew window.
        inject_end_ms = int(time.time() * 1000)
        anns = self.obs.get_annotations(self._inject_start_ms, inject_end_ms)
        bogus = [
            a for a in anns
            if a.get("timeEnd", 0) < a.get("time", 0)
               or (a.get("timeEnd", 0) - a.get("time", 0)) < 0
        ]
        if bogus:
            raise ObserveError(f"Bogus annotation posted despite gap-clamp guard: {bogus[0]}")
        self.log.info("No bogus annotation — gap-clamp guardrail holds", event="bench_observe")
        return {"bogus_annotations": 0, "checked_annotation_count": len(anns)}

    def restore(self) -> None:
        if self._skewed:
            restore_clock()
