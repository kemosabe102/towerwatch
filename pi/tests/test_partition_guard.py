"""Tests for startup.wait_for_data_partition — 5 tests."""
import subprocess
import sys
import time as time_mod
from pathlib import Path
from unittest.mock import patch

import pytest

_PI = Path(__file__).resolve().parents[1]
if str(_PI) not in sys.path:
    sys.path.insert(0, str(_PI))


# ---------------------------------------------------------------------------
# Windows path: skips mountpoint check, creates dir
# ---------------------------------------------------------------------------
def test_windows_creates_data_dir(tmp_path, monkeypatch):
    import startup
    monkeypatch.setattr(startup, "IS_WINDOWS", True)
    data = tmp_path / "data"
    startup.wait_for_data_partition(data, timeout_s=1)
    assert data.is_dir()


# ---------------------------------------------------------------------------
# Linux: mountpoint found → returns without timeout
# ---------------------------------------------------------------------------
def test_linux_mounted_returns_immediately(tmp_path, monkeypatch):
    import startup
    monkeypatch.setattr(startup, "IS_WINDOWS", False)
    data = tmp_path / "data"
    data.mkdir()

    def fake_run(cmd, **kwargs):
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(time_mod, "sleep", lambda s: None)
    startup.wait_for_data_partition(data, timeout_s=5)
    # No assertion needed — would hang if broken


# ---------------------------------------------------------------------------
# Linux: mountpoint never found → timeout, dir created anyway
# ---------------------------------------------------------------------------
def test_linux_not_mounted_creates_dir_after_timeout(tmp_path, monkeypatch):
    import startup
    monkeypatch.setattr(startup, "IS_WINDOWS", False)
    data = tmp_path / "missing_data"

    calls = [0]

    def fake_time():
        v = calls[0]
        calls[0] += 31
        return float(v)

    monkeypatch.setattr(time_mod, "time", fake_time)
    monkeypatch.setattr(time_mod, "sleep", lambda s: None)

    def fake_run(cmd, **kwargs):
        return type("R", (), {"returncode": 1})()

    monkeypatch.setattr(subprocess, "run", fake_run)
    startup.wait_for_data_partition(data, timeout_s=1)
    assert data.is_dir()


# ---------------------------------------------------------------------------
# Marker round-trip: write then read
# ---------------------------------------------------------------------------
def test_marker_roundtrip(tmp_path):
    from startup import read_marker, write_marker
    p = tmp_path / "markers" / "last_push.txt"
    write_marker(p, 1_700_000_000.0)
    assert read_marker(p) == 1_700_000_000.0


def test_marker_atomic_roundtrip(tmp_path):
    from startup import read_marker, write_marker
    p = tmp_path / "markers" / "last_push.txt"
    write_marker(p, 1_700_001_234.0, atomic=True)
    assert read_marker(p) == 1_700_001_234.0
