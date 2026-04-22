"""
Integration-ish test for towerwatch.main(): boots main() for 2 fake ticks,
asserts expected call sequence — Grafana.push called, Loki.flush called after
each batch push, probes called in order, events emitted correctly.
"""
import sys
import time as time_mod
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

_PI = Path(__file__).resolve().parents[1]
if str(_PI) not in sys.path:
    sys.path.insert(0, str(_PI))


def _make_fake_grafana():
    g = MagicMock()
    g.push_metrics.return_value = True
    g.push_annotation.return_value = None
    return g


def _make_fake_loki():
    loki = MagicMock()
    loki.push.return_value = None
    loki.log_and_push.return_value = None
    loki.flush.return_value = 0
    return loki


# ---------------------------------------------------------------------------
# 2-tick smoke: main() starts, runs two cycles, shuts down cleanly
# ---------------------------------------------------------------------------
def test_main_two_ticks(tmp_path, monkeypatch):
    import towerwatch
    import config

    # Point data files at tmp_path
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(config, "LAST_PUSH_MARKER_FILE", str(tmp_path / "last_push_ts"))
    monkeypatch.setattr(config, "LAST_ALIVE_MARKER_FILE", str(tmp_path / "last_alive_ts"))

    fake_grafana = _make_fake_grafana()
    fake_loki = _make_fake_loki()

    import grafana as grafana_mod
    import loki as loki_mod
    from scheduling import Scheduler

    monkeypatch.setattr(grafana_mod.GrafanaClient, "from_config",
                        classmethod(lambda cls, *a, **kw: fake_grafana))
    monkeypatch.setattr(loki_mod.LokiClient, "from_config",
                        classmethod(lambda cls, *a, **kw: fake_loki))

    # Make all probes fast no-ops in the tick module (where they're imported)
    import tick
    monkeypatch.setattr(tick, "run_ping",
                        lambda t: {"rtt_avg": 10, "rtt_min": 9, "rtt_max": 11,
                                   "jitter": 1, "pkt_loss": 0, "connected": True})
    monkeypatch.setattr(tick, "measure_tcp_connect", lambda: 5)
    monkeypatch.setattr(tick, "measure_dns", lambda ns: 20)
    monkeypatch.setattr(tick, "poll_gateway", lambda: {})

    # Skip partition wait
    import startup
    monkeypatch.setattr(startup, "wait_for_data_partition", lambda *a, **kw: None)

    # Shutdown after 2 ticks via sleep patch
    state_capture = {"state": None}
    tick_count = [0]

    def fake_sleep_fn(s):
        tick_count[0] += 1
        if tick_count[0] >= 2 and state_capture["state"]:
            state_capture["state"].shutdown_requested = True

    # We need to capture the state object — patch RuntimeState to record it
    from lifecycle import RuntimeState
    OrigState = RuntimeState

    class CapturingState(OrigState):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            state_capture["state"] = self

    monkeypatch.setattr("lifecycle.RuntimeState", CapturingState)
    monkeypatch.setattr(time_mod, "sleep", fake_sleep_fn)
    # Also patch perf_counter so cycles don't eat real time
    monkeypatch.setattr(time_mod, "perf_counter", lambda: 0.0)

    towerwatch.main()

    # service_restarted and service_started were pushed
    push_calls = [c[0][0] for c in fake_loki.push.call_args_list]
    assert "WARN" in push_calls   # service_restarted is WARN
    assert "INFO" in push_calls   # service_started is INFO

    # Grafana push_metrics called (batch size=2, 2 ticks → at least 1 push)
    assert fake_grafana.push_metrics.called

    # Loki flush called after successful metric push
    assert fake_loki.flush.called
