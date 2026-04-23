"""Tests for tick.push_batch — no patch, fakes injected."""

from tests.fakes import FakeClock, FakeEvents, FakeGrafana, FakeLoki


def _make_ctx(grafana=None, loki=None, events=None, clock=None):
    from towerwatch.tick import TickContext

    return TickContext(
        grafana=grafana if grafana is not None else FakeGrafana(),
        loki=loki if loki is not None else FakeLoki(),
        scheduler=None,
        events=events or FakeEvents(),
        clock=clock or FakeClock(wall=[1_700_000_000.0]),
    )


def _make_state(last_push_ts=1_700_000_000.0):
    from towerwatch.lifecycle import RuntimeState

    s = RuntimeState()
    s.last_successful_push_ts = last_push_ts
    return s


def _call_push(ctx, state, lines, *, last_line=None, **kwargs):
    """Drive push_batch through a list of lines, returning the state."""
    from towerwatch.tick import push_batch

    for line in lines[:-1]:
        state.metric_batch.append(line)
    push_batch(
        ctx,
        state,
        lines[-1],
        any_connected=kwargs.get("any_connected", True),
        batch_size=kwargs.get("batch_size"),
        gap_threshold_s=kwargs.get("gap_threshold_s"),
        marker_file=kwargs.get("marker_file"),
        build_version=kwargs.get("build_version"),
    )


# ---------------------------------------------------------------------------
# push_metrics False → drop batch, skip marker
# ---------------------------------------------------------------------------
def test_push_failure_drops_batch_and_skips_marker(tmp_path):
    marker = tmp_path / "last_push_ts"
    grafana = FakeGrafana(push_ok=False)
    loki = FakeLoki()
    ctx = _make_ctx(grafana=grafana, loki=loki, clock=FakeClock(wall=[1_700_000_100.0]))
    state = _make_state(last_push_ts=1_700_000_000.0)
    _call_push(
        ctx, state, ["line0", "line1"], batch_size=2, gap_threshold_s=600, marker_file=marker
    )

    assert state.metric_batch == []
    assert len(grafana.push_calls) == 1
    assert loki.flush_calls == 0
    assert not marker.exists()


# ---------------------------------------------------------------------------
# any_connected=False → clear batch without push
# ---------------------------------------------------------------------------
def test_not_connected_clears_batch_without_push(tmp_path):
    grafana = FakeGrafana(push_ok=True)
    loki = FakeLoki()
    ctx = _make_ctx(grafana=grafana, loki=loki)
    state = _make_state()
    _call_push(
        ctx,
        state,
        ["line0", "line1"],
        batch_size=2,
        gap_threshold_s=600,
        marker_file=tmp_path / "last_push_ts",
        any_connected=False,
    )

    assert grafana.push_calls == []
    assert loki.flush_calls == 0
    assert state.metric_batch == []


# ---------------------------------------------------------------------------
# loki is None → no crash
# ---------------------------------------------------------------------------
def test_loki_none_does_not_crash(tmp_path):
    grafana = FakeGrafana(push_ok=True)
    ctx = _make_ctx(grafana=grafana, loki=None, clock=FakeClock(wall=[1_700_000_100.0]))
    state = _make_state(last_push_ts=1_700_000_000.0)
    _call_push(
        ctx,
        state,
        ["line0", "line1"],
        batch_size=2,
        gap_threshold_s=600,
        marker_file=tmp_path / "last_push_ts",
    )

    assert len(grafana.push_calls) == 1


# ---------------------------------------------------------------------------
# Outage gap ≥ threshold → annotation + event
# ---------------------------------------------------------------------------
def test_outage_gap_triggers_annotation(tmp_path):
    grafana = FakeGrafana(push_ok=True)
    events = FakeEvents()
    loki = FakeLoki()
    # now - last_push = 700 > 600 threshold
    ctx = _make_ctx(
        grafana=grafana, loki=loki, events=events, clock=FakeClock(wall=[1_700_000_700.0])
    )
    state = _make_state(last_push_ts=1_700_000_000.0)

    _call_push(
        ctx,
        state,
        ["line0", "line1"],
        batch_size=2,
        gap_threshold_s=600,
        marker_file=tmp_path / "last_push_ts",
        build_version="testv",
    )

    assert len(grafana.annotation_calls) == 1
    assert grafana.annotation_calls[0]["reason"] == "network_unreachable"
    assert grafana.annotation_calls[0]["version"] == "testv"
    assert events.called("outage_recorded")


# ---------------------------------------------------------------------------
# Successful push → marker written, flush called
# ---------------------------------------------------------------------------
def test_successful_push_writes_marker_and_flushes(tmp_path):
    grafana = FakeGrafana(push_ok=True)
    loki = FakeLoki()
    marker = tmp_path / "last_push_ts"
    # Small gap so no annotation fires
    ctx = _make_ctx(grafana=grafana, loki=loki, clock=FakeClock(wall=[1_700_000_005.0]))
    state = _make_state(last_push_ts=1_700_000_000.0)
    _call_push(
        ctx, state, ["line0", "line1"], batch_size=2, gap_threshold_s=600, marker_file=marker
    )

    assert marker.exists()
    assert loki.flush_calls == 1
    assert grafana.annotation_calls == []


# ---------------------------------------------------------------------------
# Batch not full yet → no push
# ---------------------------------------------------------------------------
def test_batch_not_full_no_push(tmp_path):
    grafana = FakeGrafana(push_ok=True)
    ctx = _make_ctx(grafana=grafana)
    state = _make_state()
    _call_push(
        ctx,
        state,
        ["line0", "line1"],
        batch_size=3,
        gap_threshold_s=600,
        marker_file=tmp_path / "last_push_ts",
    )

    assert grafana.push_calls == []
    assert len(state.metric_batch) == 2
