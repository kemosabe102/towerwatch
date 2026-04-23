"""Tests for startup.wait_for_data_partition — no patch, fakes injected."""
import sys
from pathlib import Path

_PI = Path(__file__).resolve().parents[1]
if str(_PI) not in sys.path:
    sys.path.insert(0, str(_PI))

from tests.fakes import FakeClock, FakeCompletedProcess, FakeEvents, FakeLoki, FakeSubprocess


# ---------------------------------------------------------------------------
# Windows path: skips mountpoint, creates dir
# ---------------------------------------------------------------------------
def test_windows_creates_data_dir(tmp_path):
    from startup import wait_for_data_partition
    data = tmp_path / "data"
    wait_for_data_partition(
        data, timeout_s=1,
        is_windows=True,
        clock=FakeClock(),
        subprocess_run=FakeSubprocess(),
        loki=FakeLoki(),
    )
    assert data.is_dir()


# ---------------------------------------------------------------------------
# Linux mountpoint present → returns immediately
# ---------------------------------------------------------------------------
def test_linux_mounted_returns_immediately(tmp_path):
    from startup import wait_for_data_partition
    data = tmp_path / "data"
    data.mkdir()

    clock = FakeClock(wall=[0.0, 0.5])  # start + one loop check
    runner = FakeSubprocess(
        FakeCompletedProcess(returncode=0)  # mountpoint -q returns 0
    )
    wait_for_data_partition(
        data, timeout_s=5,
        is_windows=False,
        clock=clock,
        subprocess_run=runner,
        loki=FakeLoki(),
    )
    assert len(runner.calls) == 1


# ---------------------------------------------------------------------------
# Linux not mounted → timeout, dir created anyway, partition_missing event
# ---------------------------------------------------------------------------
def test_linux_not_mounted_creates_dir_after_timeout_and_emits_event(tmp_path):
    from startup import wait_for_data_partition
    data = tmp_path / "missing_data"

    # Two clock reads: one for deadline, one for while check (exceeds deadline)
    clock = FakeClock(wall=[0.0, 31.0])
    events = FakeEvents()
    loki = FakeLoki()
    wait_for_data_partition(
        data, timeout_s=1,
        is_windows=False,
        clock=clock,
        subprocess_run=FakeSubprocess(),  # never called (dir doesn't exist yet)
        loki=loki,
        events=events,
    )
    assert data.is_dir()
    assert events.called("partition_missing")


# ---------------------------------------------------------------------------
# Marker round-trip
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
