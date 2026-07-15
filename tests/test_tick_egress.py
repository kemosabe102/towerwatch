"""Integration: handle_egress_check — scheduled egress probe + change-event with
the first-observation guard, mirroring handle_gateway_reresolution.
"""

from __future__ import annotations

import towerwatch.tick as tick_mod
from tests.fakes import FakeClock
from towerwatch.lifecycle import RuntimeState
from towerwatch.tick import TickContext, handle_egress_check


class _Sched:
    def __init__(self, run: bool):
        self._run = run
        self.calls = 0

    def should_run_egress_ip(self, now):
        self.calls += 1
        return self._run


class _Events:
    def __init__(self):
        self.changes = []

    def egress_ip_changed(self, loki, *, old_ip, new_ip, cgnat, colo):
        self.changes.append((old_ip, new_ip, cgnat, colo))


def _ctx(sched, events):
    # wall clock value doesn't matter — the scheduler stub decides run/no-run.
    return TickContext(scheduler=sched, events=events, loki=object(), clock=FakeClock(wall=[0.0]))


def test_not_scheduled_returns_held_value_no_probe(monkeypatch):
    """Between scheduled checks: no probe call, returns state's held IP for a
    stable build_info label."""
    called = []
    monkeypatch.setattr(tick_mod, "measure_egress", lambda: called.append(1) or {})
    state = RuntimeState(last_egress_ip="203.0.113.45")
    sched, events = _Sched(run=False), _Events()

    result = handle_egress_check(_ctx(sched, events), state)
    assert result.ip == "203.0.113.45"
    assert called == []  # not scheduled → no probe
    assert events.changes == []


def test_first_observation_sets_state_but_fires_no_event(monkeypatch):
    """last_egress_ip == "" (fresh process): the first successful check sets the
    value but does NOT fire a change event ('' -> X is init, not a change)."""
    monkeypatch.setattr(
        tick_mod,
        "measure_egress",
        lambda: {"egress_ip": "203.0.113.45", "egress_colo": "SEA", "egress_cgnat": 0},
    )
    state = RuntimeState(last_egress_ip="")
    sched, events = _Sched(run=True), _Events()

    result = handle_egress_check(_ctx(sched, events), state)
    assert result.ip == "203.0.113.45"
    assert state.last_egress_ip == "203.0.113.45"
    assert events.changes == []  # first observation → NO event


def test_unchanged_ip_fires_no_event(monkeypatch):
    monkeypatch.setattr(
        tick_mod,
        "measure_egress",
        lambda: {"egress_ip": "203.0.113.45", "egress_colo": "SEA", "egress_cgnat": 0},
    )
    state = RuntimeState(last_egress_ip="203.0.113.45")
    sched, events = _Sched(run=True), _Events()

    result = handle_egress_check(_ctx(sched, events), state)
    assert result.ip == "203.0.113.45"
    assert events.changes == []


def test_real_change_fires_event_with_context(monkeypatch):
    monkeypatch.setattr(
        tick_mod,
        "measure_egress",
        lambda: {"egress_ip": "198.51.100.7", "egress_colo": "DEN", "egress_cgnat": 0},
    )
    state = RuntimeState(last_egress_ip="203.0.113.45")
    sched, events = _Sched(run=True), _Events()

    result = handle_egress_check(_ctx(sched, events), state)
    assert result.ip == "198.51.100.7"
    assert state.last_egress_ip == "198.51.100.7"
    assert events.changes == [("203.0.113.45", "198.51.100.7", 0, "DEN")]


def test_probe_failure_keeps_held_value_no_event(monkeypatch):
    """A scheduled check that returns {} (fetch failure) must NOT fabricate a
    change — hold the last value, fire nothing."""
    monkeypatch.setattr(tick_mod, "measure_egress", lambda: {})
    state = RuntimeState(last_egress_ip="203.0.113.45")
    sched, events = _Sched(run=True), _Events()

    result = handle_egress_check(_ctx(sched, events), state)
    assert result.ip == "203.0.113.45"
    assert events.changes == []


def test_disabled_by_config_skips(monkeypatch):
    """When EGRESS_IP_CHECK_ENABLED is False, no probe, returns held value."""
    monkeypatch.setattr(tick_mod._config, "EGRESS_IP_CHECK_ENABLED", False)
    called = []
    monkeypatch.setattr(tick_mod, "measure_egress", lambda: called.append(1) or {})
    state = RuntimeState(last_egress_ip="203.0.113.45")
    sched, events = _Sched(run=True), _Events()

    result = handle_egress_check(_ctx(sched, events), state)
    assert result.ip == "203.0.113.45"
    assert called == []


# ---------------------------------------------------------------------------
# egress_cgnat must reach the METRIC LINE, not just the probe + change-event.
#
# The absence of this assertion is precisely why the bug shipped in 29ac022:
# handle_egress_check returned a bare `str` (the IP), so the probe's
# egress_cgnat field had nowhere to go and never reached Prometheus — while
# every existing test still passed. CLAUDE.md and docs/ullrich-gateway-ubc1340.md
# both document egress_cgnat as a "plottable field".
# ---------------------------------------------------------------------------


def test_cgnat_field_is_returned_for_the_metric_line(monkeypatch):
    """A scheduled check surfaces egress_cgnat as a metric field (0/1)."""
    monkeypatch.setattr(
        tick_mod,
        "measure_egress",
        lambda: {"egress_ip": "100.64.0.1", "egress_colo": "SEA", "egress_cgnat": 1},
    )
    state = RuntimeState(last_egress_ip="")
    sched, events = _Sched(run=True), _Events()

    result = handle_egress_check(_ctx(sched, events), state)
    assert result.fields == {"egress_cgnat": 1}


def test_cgnat_held_between_scheduled_checks(monkeypatch):
    """Between checks (~15 min apart) the field is HELD, so the series is
    continuous and plottable rather than sparse 1-sample-per-15-min."""
    called = []
    monkeypatch.setattr(tick_mod, "measure_egress", lambda: called.append(1) or {})
    state = RuntimeState(last_egress_ip="203.0.113.45", last_egress_cgnat=0)
    sched, events = _Sched(run=False), _Events()

    result = handle_egress_check(_ctx(sched, events), state)
    assert called == []  # not scheduled → no probe
    assert result.fields == {"egress_cgnat": 0}  # ...but still emitted


def test_no_cgnat_field_before_first_reading(monkeypatch):
    """Before any successful check, emit NO field — a fabricated 0 would read as
    'confirmed not CGNAT' when the truth is 'unknown'. Mirrors the egress_ip tag
    being omitted rather than sent as `none`."""
    monkeypatch.setattr(tick_mod, "measure_egress", lambda: {})
    state = RuntimeState(last_egress_ip="", last_egress_cgnat=None)
    sched, events = _Sched(run=True), _Events()

    result = handle_egress_check(_ctx(sched, events), state)
    assert result.fields == {}


def test_probe_failure_holds_last_cgnat(monkeypatch):
    """A failed scheduled check must not drop the field to 0 — hold the last
    known reading, same as the IP."""
    monkeypatch.setattr(tick_mod, "measure_egress", lambda: {})
    state = RuntimeState(last_egress_ip="100.64.0.1", last_egress_cgnat=1)
    sched, events = _Sched(run=True), _Events()

    result = handle_egress_check(_ctx(sched, events), state)
    assert result.fields == {"egress_cgnat": 1}
    assert result.ip == "100.64.0.1"
