"""Test 8: clock_skew_backward — step clock −30 min.

EXPECTED FAILURE: negative push-gap causes nonsensical annotation region math.
Passes while the bug is present; fails (flips to FAIL) once gap-clamping lands.
"""

import time

from ..harness.snapshot import snapshot_clock, step_clock, restore_clock
from ..harness.observe import ObserveError
from .base import BenchTest

SKEW_S = -1800   # −30 minutes
OBSERVE_DURATION_S = 120


class Test(BenchTest):
    name = "clock_skew_backward"
    description = "Step clock −30 min; expected-failure: negative gap → bogus annotation math"
    expected_failure = True
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

        # After a negative skew, check whether a nonsensical annotation was created
        # (timeEnd < time, or duration < 0). If one exists, the bug is confirmed → expected_failure.
        inject_end_ms = int(time.time() * 1000)
        anns = self.obs.get_annotations(self._inject_start_ms, inject_end_ms)
        bogus = [
            a for a in anns
            if a.get("timeEnd", 0) < a.get("time", 0)
               or (a.get("timeEnd", 0) - a.get("time", 0)) < 0
        ]
        if not bogus:
            # Bug is NOT present — annotation math is already clamped. Raise so base flips to fail.
            raise ObserveError("No bogus annotation found — gap-clamping may already be in place")

        return {"bogus_annotation_confirmed": True, "example": bogus[0]}

    def restore(self) -> None:
        if self._skewed:
            restore_clock()
