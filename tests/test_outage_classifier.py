"""Tests for startup.classify_outage — 6 tests.

Originally targeted inline towerwatch.main() logic; Pass 7 re-targets
to startup.classify_outage without changing the assertions.
"""

from towerwatch.startup import OutageKind, classify_outage

GAP = 600  # config.OUTAGE_GAP_THRESHOLD_S default


# ---------------------------------------------------------------------------
# Short gap — no annotation
# ---------------------------------------------------------------------------
def test_short_gap_no_annotation():
    now = 1_700_001_000.0
    last_push = now - 300  # 5 min ago — under threshold
    result = classify_outage(
        now=now, last_push_ts=last_push, last_alive_ts=None, gap_threshold_s=GAP
    )
    assert result is None


# ---------------------------------------------------------------------------
# Long gap + alive marker recent → network_unreachable
# ---------------------------------------------------------------------------
def test_long_gap_alive_recent_is_network_unreachable():
    now = 1_700_002_000.0
    last_push = now - 1200  # 20 min gap
    last_alive = now - 30  # process was alive 30s ago → network outage
    result = classify_outage(
        now=now, last_push_ts=last_push, last_alive_ts=last_alive, gap_threshold_s=GAP
    )
    assert result is not None
    kind, gap = result
    assert kind == OutageKind.NETWORK_UNREACHABLE
    assert gap == 1200.0


# ---------------------------------------------------------------------------
# Long gap + alive marker old (or missing) → process_restart
# ---------------------------------------------------------------------------
def test_long_gap_alive_old_is_process_restart():
    now = 1_700_002_000.0
    last_push = now - 1800  # 30 min gap
    last_alive = now - 1800  # alive marker is as old as last push → restart
    result = classify_outage(
        now=now, last_push_ts=last_push, last_alive_ts=last_alive, gap_threshold_s=GAP
    )
    assert result is not None
    kind, _gap = result
    assert kind == OutageKind.PROCESS_RESTART


def test_long_gap_no_alive_marker_is_process_restart():
    now = 1_700_002_000.0
    last_push = now - 900
    result = classify_outage(
        now=now, last_push_ts=last_push, last_alive_ts=None, gap_threshold_s=GAP
    )
    assert result is not None
    kind, _gap = result
    assert kind == OutageKind.PROCESS_RESTART


# ---------------------------------------------------------------------------
# Missing last_push → skip annotation
# ---------------------------------------------------------------------------
def test_missing_last_push_no_annotation():
    result = classify_outage(
        now=1_700_000_000.0, last_push_ts=None, last_alive_ts=None, gap_threshold_s=GAP
    )
    assert result is None


# ---------------------------------------------------------------------------
# Exact threshold boundary — gap == GAP is an outage
# ---------------------------------------------------------------------------
def test_exact_threshold_boundary_triggers():
    now = 1_700_000_600.0
    last_push = now - GAP  # exactly at threshold
    result = classify_outage(
        now=now, last_push_ts=last_push, last_alive_ts=None, gap_threshold_s=GAP
    )
    assert result is not None
    kind, gap = result
    assert kind == OutageKind.PROCESS_RESTART
    assert gap == float(GAP)
