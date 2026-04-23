"""Clock abstraction for deterministic testing.

Production code takes a `Clock` via constructor/parameter defaulting to
`SystemClock()`. Tests pass a `FakeClock` with pre-loaded values.

The protocol is deliberately minimal — only the calls towerwatch actually
makes (`perf_counter` for elapsed-time measurement, `time` for wall-clock
timestamps, `sleep` for the main-loop pacing).
"""

from __future__ import annotations

import time as _time
from typing import Protocol


class Clock(Protocol):
    def perf_counter(self) -> float: ...
    def time(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    """Production Clock — delegates to the `time` module."""

    def perf_counter(self) -> float:
        return _time.perf_counter()

    def time(self) -> float:
        return _time.time()

    def sleep(self, seconds: float) -> None:
        _time.sleep(seconds)
