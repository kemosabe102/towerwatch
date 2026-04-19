"""Snapshot and restore primitives.

All mutations are preceded by a snapshot so restore is always possible.
Restore is idempotent — safe to call multiple times.
"""

import shutil
import subprocess
import sys
import time
from pathlib import Path

from .state import SNAPSHOTS_DIR


def _run(*args, check=True, capture=True):
    return subprocess.run(
        list(args),
        check=check,
        capture_output=capture,
        text=True,
    )


# ---------------------------------------------------------------------------
# iptables
# ---------------------------------------------------------------------------

def snapshot_iptables(run_id: str, label: str) -> Path:
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    rules_file = SNAPSHOTS_DIR / f"{run_id}_{label}_iptables.rules"
    result = _run("iptables-save")
    rules_file.write_text(result.stdout)
    return rules_file


def restore_iptables(rules_file: Path) -> None:
    if rules_file.exists():
        with rules_file.open() as fh:
            subprocess.run(["iptables-restore"], stdin=fh, check=True)

# ---------------------------------------------------------------------------
# tc (traffic control)
# ---------------------------------------------------------------------------

def snapshot_tc(run_id: str, label: str, iface: str = "eth0") -> dict:
    result = _run("tc", "qdisc", "show", "dev", iface, check=False)
    return {"iface": iface, "qdisc_show": result.stdout, "run_id": run_id, "label": label}


def restore_tc(snapshot: dict) -> None:
    iface = snapshot.get("iface", "eth0")
    # Remove any netem/tbf qdiscs we added (root); errors expected if already clean
    _run("tc", "qdisc", "del", "dev", iface, "root", check=False)


# ---------------------------------------------------------------------------
# Clock (NTP / timedatectl)
# ---------------------------------------------------------------------------

def snapshot_clock() -> dict:
    result = _run("timedatectl", "show", "--no-pager", check=False)
    return {"timedatectl_show": result.stdout}


def step_clock(offset_seconds: int) -> None:
    """Step the system clock by offset_seconds (positive = forward)."""
    _run("timedatectl", "set-ntp", "false")
    # `date -s` with a relative offset: +N seconds from now
    sign = "+" if offset_seconds >= 0 else ""
    _run("date", "-s", f"{sign}{offset_seconds} seconds")


def restore_clock() -> None:
    _run("timedatectl", "set-ntp", "true")
    # NTP resync is async; give it a moment before the test exits
    time.sleep(3)

# ---------------------------------------------------------------------------
# systemd drop-ins
# ---------------------------------------------------------------------------

DROPIN_DIR = Path("/etc/systemd/system/towerwatch.service.d")


def write_dropin(name: str, content: str) -> Path:
    DROPIN_DIR.mkdir(parents=True, exist_ok=True)
    path = DROPIN_DIR / f"bench-{name}.conf"
    path.write_text(content)
    _run("systemctl", "daemon-reload")
    return path


def remove_dropin(name: str) -> None:
    path = DROPIN_DIR / f"bench-{name}.conf"
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    _run("systemctl", "daemon-reload", check=False)


# ---------------------------------------------------------------------------
# Generic file snapshot/restore
# ---------------------------------------------------------------------------

def snapshot_file(src: Path, run_id: str, label: str) -> Path:
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    dest = SNAPSHOTS_DIR / f"{run_id}_{label}_{src.name}"
    if src.exists():
        shutil.copy2(src, dest)
    return dest


def restore_file(src: Path, snapshot: Path) -> None:
    if snapshot.exists():
        shutil.copy2(snapshot, src)
    elif src.exists():
        src.unlink()  # Original didn't exist; remove what the test created
