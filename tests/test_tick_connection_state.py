"""Tests for tick.update_connection_state — no patch, fakes injected."""

from tests.fakes import FakeClock, FakeEvents, FakeLoki


def _make_ctx(events=None, loki=None, clock=None):
    from towerwatch.tick import TickContext

    return TickContext(
        grafana=None,
        loki=loki or FakeLoki(),
        scheduler=None,
        events=events or FakeEvents(),
        clock=clock or FakeClock(),
    )


def _make_state():
    from towerwatch.lifecycle import RuntimeState

    return RuntimeState()


# ---------------------------------------------------------------------------
# up → down
# ---------------------------------------------------------------------------
def test_up_to_down_emits_connection_down():
    from towerwatch.tick import update_connection_state

    events = FakeEvents()
    ctx = _make_ctx(events=events)
    state = _make_state()
    state.connected = True

    update_connection_state(ctx, state, connected=False, timestamp=1000)

    assert events.called("connection_down")
    assert state.connected is False
    assert state.outage_start == 1000
    assert state.outage_count == 1


# ---------------------------------------------------------------------------
# down → up
# ---------------------------------------------------------------------------
def test_down_to_up_emits_connection_restored_with_duration():
    from towerwatch.tick import update_connection_state

    events = FakeEvents()
    ctx = _make_ctx(events=events)
    state = _make_state()
    state.connected = False
    state.outage_start = 900

    update_connection_state(ctx, state, connected=True, timestamp=1000)

    assert events.called("connection_restored")
    # Find the call and check kwargs
    call = next(c for c in events.calls if c[0] == "connection_restored")
    assert call[2]["down_duration_s"] == 100
    assert state.connected is True
    assert state.outage_start == 0
    assert state.total_outage_s == 100


# ---------------------------------------------------------------------------
# Zero outage_start guard
# ---------------------------------------------------------------------------
def test_down_to_up_with_zero_outage_start_does_not_record_duration():
    from towerwatch.tick import update_connection_state

    events = FakeEvents()
    ctx = _make_ctx(events=events)
    state = _make_state()
    state.connected = False
    state.outage_start = 0

    update_connection_state(ctx, state, connected=True, timestamp=1000)

    assert not events.called("connection_restored")
    assert state.total_outage_s == 0


# ---------------------------------------------------------------------------
# Stable states (no transition)
# ---------------------------------------------------------------------------
def test_already_connected_stays_connected():
    from towerwatch.tick import update_connection_state

    events = FakeEvents()
    ctx = _make_ctx(events=events)
    state = _make_state()
    state.connected = True

    update_connection_state(ctx, state, connected=True, timestamp=1000)

    assert not events.called("connection_down")
    assert not events.called("connection_restored")


def test_already_disconnected_stays_disconnected():
    from towerwatch.tick import update_connection_state

    events = FakeEvents()
    ctx = _make_ctx(events=events)
    state = _make_state()
    state.connected = False
    state.outage_start = 900

    update_connection_state(ctx, state, connected=False, timestamp=1000)

    assert not events.called("connection_down")
    assert not events.called("connection_restored")
    assert state.outage_start == 900
