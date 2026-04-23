"""
Process lifecycle helpers: runtime state, logging configuration, signal handling.

All collaborators (signal module, IS_WINDOWS flag) are injectable — tests
pass fakes, production uses defaults.
"""

import logging
import signal
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from towerwatch import config

log = logging.getLogger("towerwatch")

IS_WINDOWS = sys.platform == "win32"


@dataclass
class RuntimeState:
    connected: bool = True
    outage_start: int = 0
    outage_count: int = 0
    total_outage_s: int = 0
    start_ts: float = field(default_factory=time.monotonic)
    last_heartbeat_ts: float = 0.0
    last_successful_push_ts: float = field(default_factory=time.time)
    shutdown_requested: bool = False
    metric_batch: list = field(default_factory=list)


def configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def install_signal_handlers(
    state: RuntimeState,
    *,
    is_windows: bool | None = None,
    signal_module: Any = signal,
) -> None:
    """Install SIGTERM handler that flips state.shutdown_requested.

    On Windows (or when is_windows=True), this is a no-op.
    """
    if is_windows is None:
        is_windows = IS_WINDOWS

    def _on_sigterm(signum, frame):
        log.info("SIGTERM received — shutting down gracefully")
        state.shutdown_requested = True

    if not is_windows:
        signal_module.signal(signal_module.SIGTERM, _on_sigterm)
