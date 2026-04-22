"""
Startup helpers: data-partition guard, marker IO, outage classification.
"""

import logging
import os
import subprocess
import sys
import time as _time
from enum import Enum
from pathlib import Path

import config
import events as events_mod

log = logging.getLogger("towerwatch")

IS_WINDOWS = sys.platform == "win32"


# ---------------------------------------------------------------------------
# Marker IO
# ---------------------------------------------------------------------------
def read_marker(path: Path) -> float | None:
    """Read a persisted Unix timestamp. Returns None if missing or unreadable."""
    try:
        raw = path.read_text(encoding="utf-8").strip()
        return float(raw) if raw else None
    except (OSError, ValueError):
        return None


def write_marker(path: Path, ts: float, *, atomic: bool = False) -> None:
    """Persist a Unix timestamp to a marker file. Best-effort — OSError is swallowed."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if atomic:
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(f"{ts:.0f}\n", encoding="utf-8")
            os.replace(tmp, path)
        else:
            path.write_text(f"{ts:.0f}\n", encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Outage classifier
# ---------------------------------------------------------------------------
class OutageKind(str, Enum):
    PROCESS_RESTART = "process_restart"
    NETWORK_UNREACHABLE = "network_unreachable"


def classify_outage(
    *,
    now: float,
    last_push_ts: float | None,
    last_alive_ts: float | None,
    gap_threshold_s: int,
) -> tuple["OutageKind", float] | None:
    """Return (kind, gap_s) if an outage should be annotated, else None."""
    if last_push_ts is None:
        return None
    gap = now - last_push_ts
    if gap < gap_threshold_s:
        return None
    if last_alive_ts and (now - last_alive_ts) < gap_threshold_s:
        return OutageKind.NETWORK_UNREACHABLE, gap
    return OutageKind.PROCESS_RESTART, gap


# ---------------------------------------------------------------------------
# Data partition guard
# ---------------------------------------------------------------------------
def wait_for_data_partition(path: Path = None, timeout_s: int = 30) -> None:
    """Block until the data partition is mounted or timeout. Skips on Windows."""
    if path is None:
        path = Path(config.DATA_DIR)
    if IS_WINDOWS:
        path.mkdir(parents=True, exist_ok=True)
        log.info("Windows: using local data dir %s", path)
        return
    deadline = _time.time() + timeout_s
    while _time.time() < deadline:
        if path.is_dir():
            try:
                result = subprocess.run(
                    ["mountpoint", "-q", str(path)],
                    capture_output=True, timeout=5,
                )
                if result.returncode == 0:
                    log.info("Data partition mounted at %s", path)
                    return
            except Exception:
                pass
        _time.sleep(1)
    log.warning("Data partition not detected at %s — buffering to local dir", path)
    try:
        from loki import _get_singleton
        events_mod.partition_missing(_get_singleton(), path=str(path))
    except Exception:
        pass
    path.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Startup outage reconciliation
# ---------------------------------------------------------------------------
def reconcile_previous_outage(grafana, loki, cfg) -> float | None:
    """Check markers from the previous run and post an annotation if an outage is detected.
    Returns the last_push timestamp if found, else None."""
    last_push = read_marker(Path(cfg.LAST_PUSH_MARKER_FILE))
    last_alive = read_marker(Path(cfg.LAST_ALIVE_MARKER_FILE))
    if last_push is None:
        return None
    startup_now = _time.time()
    outage = classify_outage(
        now=startup_now, last_push_ts=last_push, last_alive_ts=last_alive,
        gap_threshold_s=cfg.OUTAGE_GAP_THRESHOLD_S,
    )
    if outage:
        kind, gap_s = outage
        text = f"Outage: {int(gap_s) // 60} min — {kind.value} (v {cfg.BUILD_VERSION})"
        grafana.push_annotation(
            int(last_push * 1000), int(startup_now * 1000),
            text, reason=kind.value, version=cfg.BUILD_VERSION,
        )
        events_mod.outage_recorded(loki, gap_seconds=int(gap_s),
                                   reason=kind.value, version=cfg.BUILD_VERSION)
    return last_push
