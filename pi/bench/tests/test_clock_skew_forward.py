"""Test 7: clock_skew_forward — step clock +2h; verify no bogus outage annotation.

Pass: no outage annotation is posted for a gap caused purely by clock skew.
NTP resyncs on restore.
"""

import time

from ..harness.snapshot import snapshot_clock, step_clock, restore_clock
from ..harness.observe import ObserveError
from .base import BenchTest

SKEW_S = 7200   # +2 hours
OBSERVE_DURATION_S = 180  # 3 probe cycles while clock is skewed


class Test(BenchTest):
    name = "clock_skew_forward"
    description = "Step clock +2h; no spurious outage annotation should be created"
    timeout_s = 600

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._clock_snapshot = None
        self._skewed = False

    def inject(self) -> None:
        self._clock_snapshot = snapshot_clock()
        self.log.info(f"Stepping clock forward {SKEW_S}s", event="bench_inject")
        step_clock(SKEW_S)
        self._skewed = True
        time.sleep(OBSERVE_DURATION_S)

    def observe(self) -> dict:
        # Restore clock before checking annotations — NTP sync needed for accurate window
        restore_clock()
        self._skewed = False

        # Wait for any annotation that might have been generated
        time.sleep(120)

        # No annotation should have been posted during the skew window
        inject_end_ms = int(time.time() * 1000)
        inject_start_ms = self._inject_start_ms
        try:
            anns = self.obs.get_annotations(inject_start_ms, inject_end_ms)
            spurious = [
                a for a in anns
                if a.get("time", 0) >= inject_start_ms
                and a.get("time", 0) <= inject_end_ms
            ]
            if spurious:
                raise ObserveError(
                    f"Spurious annotation created during clock-skew: {spurious[0]}"
                )
        except ObserveError:
            raise
        except Exception as e:
            raise ObserveError(f"Annotation check failed: {e}") from e

        self.log.info("No spurious annotation — clock-skew guardrail works", event="bench_observe")
        return {"spurious_annotations": 0}

    def restore(self) -> None:
        if self._skewed:
            restore_clock()
