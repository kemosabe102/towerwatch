"""BenchTest base class.

Lifecycle: snapshot → inject → observe → restore
Cleanup is guaranteed via try/finally + atexit + SIGTERM/SIGINT handlers.
Subclasses implement inject(), observe(), restore(), and set metadata.
"""

import atexit
import signal
import time
from abc import ABC, abstractmethod
from typing import Optional

from ..harness.logger import BenchLogger
from ..harness.observe import GrafanaObserver
from ..harness.report import TestResult


class BenchTest(ABC):
    # Subclasses set these
    name: str = ""
    description: str = ""
    expected_failure: bool = False     # True = test passes when the known bug is present
    # Wall-clock cap; harness SIGTERMs the test if exceeded
    timeout_s: int = 900               # 15 min default; annotation tests use 1200

    def __init__(self, logger: BenchLogger, observer: GrafanaObserver):
        self.log = logger
        self.obs = observer
        self._inject_start_s: Optional[float] = None
        self._inject_start_ns: Optional[int] = None
        self._inject_start_ms: Optional[int] = None
        self._restored = False
        # Register cleanup on unexpected exit
        atexit.register(self._safe_restore)
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT,  self._signal_handler)

    def _signal_handler(self, signum, frame):
        self.log.warn(f"Signal {signum} received — running restore", event="bench_signal")
        self._safe_restore()
        raise SystemExit(1)

    def _safe_restore(self):
        if not self._restored:
            try:
                self.restore()
            except Exception as e:
                self.log.error(f"restore() raised: {e}", event="bench_restore_error")
            finally:
                self._restored = True

    @abstractmethod
    def inject(self) -> None:
        """Perform the fault injection. Called inside try block."""

    @abstractmethod
    def observe(self) -> dict:
        """Assert expected observables via Grafana Cloud read APIs.

        Returns a dict of evidence (log entry IDs, metric values, annotation IDs).
        Raises ObserveError on assertion failure.
        """

    @abstractmethod
    def restore(self) -> None:
        """Undo all mutations.  Must be idempotent."""

    def run(self) -> TestResult:
        started = time.time()
        result = TestResult(
            name=self.name,
            status="error",
            expected_failure=self.expected_failure,
            started_at=started,
        )
        self.log.info(f"Starting test: {self.name}", event="bench_test_start", test=self.name)
        try:
            self._inject_start_s  = time.time()
            self._inject_start_ns = int(self._inject_start_s * 1e9)
            self._inject_start_ms = int(self._inject_start_s * 1e3)
            self.inject()
            evidence = self.observe()
            result.evidence = evidence or {}
            if self.expected_failure:
                # Bug is still present — that's the expected outcome
                result.status = "expected_failure"
            else:
                result.status = "pass"
        except Exception as e:
            result.error_msg = str(e)
            if self.expected_failure:
                # Bug was NOT present (exception means the fix landed) — flip to fail
                result.status = "fail"
                result.error_msg = f"Expected failure didn't occur: {e}"
            else:
                result.status = "fail"
            self.log.error(f"Test {self.name} failed: {e}", event="bench_test_fail", test=self.name)
        finally:
            self._safe_restore()
            result.duration_s = time.time() - started
        self.log.warn(
            f"Test complete: {self.name} → {result.status}",
            event="bench_test_complete",
            test=self.name,
            status=result.status,
            duration_s=round(result.duration_s, 1),
        )
        return result
