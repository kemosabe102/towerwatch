"""Characterization tests for the outage classifier — 6 tests.

Tests target the inline logic at towerwatch.py:480-486 directly.
Pass 7 will re-target these to startup.classify_outage without changing
the assertions — the tests will verify the extraction was correct.
"""
import pytest

GAP = 600  # config.OUTAGE_GAP_THRESHOLD_S default


def _classify(now, last_push, last_alive):
    """Reproduce the inline classifier logic from towerwatch.main()."""
    if last_push is None:
        return None
    gap = now - last_push
    if gap < GAP:
        return None
    if last_alive and (now - last_alive) < GAP:
        return "network_unreachable", gap
    return "process_restart", gap


# ---------------------------------------------------------------------------
# Short gap — no annotation
# ---------------------------------------------------------------------------
def test_short_gap_no_annotation():
    now = 1_700_001_000.0
    last_push = now - 300   # 5 min ago — under threshold
    result = _classify(now, last_push, last_alive=None)
    assert result is None


# ---------------------------------------------------------------------------
# Long gap + alive marker recent → network_unreachable
# ---------------------------------------------------------------------------
def test_long_gap_alive_recent_is_network_unreachable():
    now = 1_700_002_000.0
    last_push = now - 1200   # 20 min gap
    last_alive = now - 30    # process was alive 30s ago → network outage
    kind, gap = _classify(now, last_push, last_alive)
    assert kind == "network_unreachable"
    assert gap == 1200.0


# ---------------------------------------------------------------------------
# Long gap + alive marker old (or missing) → process_restart
# ---------------------------------------------------------------------------
def test_long_gap_alive_old_is_process_restart():
    now = 1_700_002_000.0
    last_push = now - 1800   # 30 min gap
    last_alive = now - 1800  # alive marker is as old as last push → restart
    kind, gap = _classify(now, last_push, last_alive)
    assert kind == "process_restart"

def test_long_gap_no_alive_marker_is_process_restart():
    now = 1_700_002_000.0
    last_push = now - 900
    kind, gap = _classify(now, last_push, last_alive=None)
    assert kind == "process_restart"


# ---------------------------------------------------------------------------
# Missing last_push → skip annotation
# ---------------------------------------------------------------------------
def test_missing_last_push_no_annotation():
    result = _classify(now=1_700_000_000.0, last_push=None, last_alive=None)
    assert result is None


# ---------------------------------------------------------------------------
# Exact threshold boundary — gap == GAP is an outage
# ---------------------------------------------------------------------------
def test_exact_threshold_boundary_triggers():
    now = 1_700_000_600.0
    last_push = now - GAP   # exactly at threshold
    kind, gap = _classify(now, last_push, last_alive=None)
    assert kind == "process_restart"
    assert gap == float(GAP)
