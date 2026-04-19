"""Sentinel management and run-state persistence for the bench harness.

The sentinel file blocks concurrent runs and survives crashes.
State JSON tracks the active test and reboot-resume phase.
"""

import json
import os
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    _DATA_ROOT = Path("./data")
else:
    _DATA_ROOT = Path("/opt/towerwatch/data")

BENCH_DIR       = _DATA_ROOT / "bench"
SENTINEL_FILE   = _DATA_ROOT / ".bench-in-progress"
STATE_FILE      = BENCH_DIR / "state.json"
SNAPSHOTS_DIR   = BENCH_DIR / "snapshots"
REPORTS_DIR     = BENCH_DIR / "reports"


def _ensure_dirs() -> None:
    for d in (BENCH_DIR, SNAPSHOTS_DIR, REPORTS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def arm_sentinel(run_id: str) -> None:
    _ensure_dirs()
    SENTINEL_FILE.write_text(json.dumps({"run_id": run_id, "started": time.time()}))


def clear_sentinel() -> None:
    try:
        SENTINEL_FILE.unlink()
    except FileNotFoundError:
        pass


def sentinel_present() -> bool:
    return SENTINEL_FILE.exists()


def read_sentinel() -> dict:
    try:
        return json.loads(SENTINEL_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_state(data: dict) -> None:
    _ensure_dirs()
    STATE_FILE.write_text(json.dumps(data, indent=2))


def read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def clear_state() -> None:
    try:
        STATE_FILE.unlink()
    except FileNotFoundError:
        pass
