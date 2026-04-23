"""Tests for startup.reconcile_previous_outage — no patch, fakes injected."""
import sys
from pathlib import Path

_PI = Path(__file__).resolve().parents[1]
if str(_PI) not in sys.path:
    sys.path.insert(0, str(_PI))

from tests.fakes import FakeClock, FakeEvents, FakeGrafana, FakeLoki


class _Cfg:
    def __init__(self, tmp_path, gap_threshold=600, build_version="test123"):
        self.LAST_PUSH_MARKER_FILE = str(tmp_path / "last_push_ts")
        self.LAST_ALIVE_MARKER_FILE = str(tmp_path / "last_alive_ts")
        self.OUTAGE_GAP_THRESHOLD_S = gap_threshold
        self.BUILD_VERSION = build_version


# ---------------------------------------------------------------------------
# No marker → None, no side effects
# ---------------------------------------------------------------------------
def test_reconcile_no_marker_returns_none(tmp_path):
    from startup import reconcile_previous_outage
    grafana = FakeGrafana()
    loki = FakeLoki()
    events = FakeEvents()
    cfg = _Cfg(tmp_path)

    result = reconcile_previous_outage(
        grafana, loki, cfg,
        clock=FakeClock(wall=[1_700_000_000.0]),
        events=events,
    )
    assert result is None
    assert grafana.annotation_calls == []
    assert not events.called("outage_recorded")


# ---------------------------------------------------------------------------
# Small gap → no annotation
# ---------------------------------------------------------------------------
def test_reconcile_small_gap_no_annotation(tmp_path):
    from startup import reconcile_previous_outage, write_marker
    grafana = FakeGrafana()
    loki = FakeLoki()
    events = FakeEvents()
    cfg = _Cfg(tmp_path, gap_threshold=600)

    # now - last_push = 30s < 600s
    last_push = 1_700_000_000.0
    write_marker(Path(cfg.LAST_PUSH_MARKER_FILE), last_push)

    result = reconcile_previous_outage(
        grafana, loki, cfg,
        clock=FakeClock(wall=[last_push + 30]),
        events=events,
    )
    assert result == last_push
    assert grafana.annotation_calls == []
    assert not events.called("outage_recorded")


# ---------------------------------------------------------------------------
# Large gap → annotation + event
# ---------------------------------------------------------------------------
def test_reconcile_large_gap_fires_annotation(tmp_path):
    from startup import reconcile_previous_outage, write_marker
    grafana = FakeGrafana()
    loki = FakeLoki()
    events = FakeEvents()
    cfg = _Cfg(tmp_path, gap_threshold=600)

    last_push = 1_700_000_000.0
    write_marker(Path(cfg.LAST_PUSH_MARKER_FILE), last_push)

    result = reconcile_previous_outage(
        grafana, loki, cfg,
        clock=FakeClock(wall=[last_push + 700]),  # 700s gap > 600
        events=events,
    )
    assert len(grafana.annotation_calls) == 1
    assert events.called("outage_recorded")
    assert result == last_push


# ---------------------------------------------------------------------------
# Network-unreachable variant (last_alive recent)
# ---------------------------------------------------------------------------
def test_reconcile_network_unreachable_when_last_alive_recent(tmp_path):
    from startup import reconcile_previous_outage, write_marker
    grafana = FakeGrafana()
    loki = FakeLoki()
    events = FakeEvents()
    cfg = _Cfg(tmp_path, gap_threshold=600)

    last_push = 1_700_000_000.0
    now = last_push + 700
    last_alive = now - 100  # still alive recently

    write_marker(Path(cfg.LAST_PUSH_MARKER_FILE), last_push)
    write_marker(Path(cfg.LAST_ALIVE_MARKER_FILE), last_alive)

    reconcile_previous_outage(
        grafana, loki, cfg,
        clock=FakeClock(wall=[now]),
        events=events,
    )
    assert grafana.annotation_calls[0]["reason"] == "network_unreachable"
