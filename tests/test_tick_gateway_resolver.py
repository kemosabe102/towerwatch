"""Integration: the tick loop uses the live GatewayResolver, not the frozen
config constant, so a re-resolution feeds the probes + build_info + a change event.
"""

from __future__ import annotations

import towerwatch.tick as tick_mod
from tests.fakes import FakeClock
from towerwatch.tick import TickContext, collect_probes


class _StubResolver:
    """Minimal GatewayResolver stand-in: returns a fixed IP, records reresolve."""

    def __init__(self, ip: str, reresolve_result: str | None = None):
        self._ip = ip
        self._reresolve_result = reresolve_result
        self.current_calls = 0
        self.reresolve_calls = 0

    def current(self) -> str:
        self.current_calls += 1
        return self._ip

    def maybe_reresolve(self) -> str | None:
        self.reresolve_calls += 1
        return self._reresolve_result


def test_collect_probes_pings_resolver_ip_for_gateway(monkeypatch):
    """The 'gateway' ping target uses resolver.current(), not config.GATEWAY_IP."""
    pinged: list[str] = []

    def fake_run_ping(ip):
        pinged.append(ip)
        return {
            "rtt_avg": 1,
            "rtt_min": 1,
            "rtt_max": 1,
            "jitter": 0,
            "pkt_loss": 0,
            "connected": True,
        }

    monkeypatch.setattr(tick_mod, "run_ping", fake_run_ping)
    monkeypatch.setattr(tick_mod, "measure_tcp_connect", lambda: 1)
    monkeypatch.setattr(tick_mod, "measure_dns", lambda ns: 1)
    monkeypatch.setattr(tick_mod, "poll_gateway", lambda ip=None: {})
    # Freeze PROBE_TARGETS so the gateway entry has a stale IP the resolver overrides.
    monkeypatch.setattr(
        tick_mod._config,
        "PROBE_TARGETS",
        [("8.8.8.8", "google"), ("192.168.1.1", "gateway")],
    )
    monkeypatch.setattr(tick_mod._config, "DNS_TARGETS", [])

    resolver = _StubResolver(ip="10.0.1.1")
    ctx = TickContext(gateway_resolver=resolver, clock=FakeClock(wall=[0.0]))
    collect_probes(ctx)

    # The stale 192.168.1.1 must NOT be pinged for the gateway target; 10.0.1.1 must.
    assert "10.0.1.1" in pinged
    assert "192.168.1.1" not in pinged
    assert "8.8.8.8" in pinged  # other targets untouched


def test_collect_probes_polls_gateway_with_resolver_ip(monkeypatch):
    """poll_gateway is called with the resolver's live IP so M6/baseline hit the
    right host after a re-resolution."""
    polled_ip: list[str | None] = []

    monkeypatch.setattr(
        tick_mod,
        "run_ping",
        lambda ip: {
            "rtt_avg": 1,
            "rtt_min": 1,
            "rtt_max": 1,
            "jitter": 0,
            "pkt_loss": 0,
            "connected": True,
        },
    )
    monkeypatch.setattr(tick_mod, "measure_tcp_connect", lambda: 1)
    monkeypatch.setattr(tick_mod._config, "PROBE_TARGETS", [("10.0.1.1", "gateway")])
    monkeypatch.setattr(tick_mod._config, "DNS_TARGETS", [])

    def fake_poll_gateway(ip=None):
        polled_ip.append(ip)
        return {}

    monkeypatch.setattr(tick_mod, "poll_gateway", fake_poll_gateway)

    resolver = _StubResolver(ip="10.0.1.1")
    ctx = TickContext(gateway_resolver=resolver, clock=FakeClock(wall=[0.0]))
    collect_probes(ctx)
    assert polled_ip == ["10.0.1.1"]


# ---------------------------------------------------------------------------
# handle_gateway_reresolution — the per-tick re-resolve + notify step
# ---------------------------------------------------------------------------
class _RecordingEvents:
    def __init__(self):
        self.changes = []

    def gateway_ip_changed(self, loki, *, old_ip, new_ip):
        self.changes.append((old_ip, new_ip))


def test_handle_reresolution_no_change_returns_current_no_event():
    from towerwatch.tick import handle_gateway_reresolution

    resolver = _StubResolver(ip="10.0.1.1", reresolve_result=None)  # unchanged
    events = _RecordingEvents()
    ctx = TickContext(gateway_resolver=resolver, events=events, loki=object())

    ip = handle_gateway_reresolution(ctx)
    assert ip == "10.0.1.1"
    assert resolver.reresolve_calls == 1
    assert events.changes == []  # no change → no event


def test_handle_reresolution_change_emits_event_with_old_and_new():
    from towerwatch.tick import handle_gateway_reresolution

    # A resolver stub that reports the pre-change IP from current(), then flips
    # to the new IP on maybe_reresolve() (returning the new IP).
    class _ChangingResolver:
        def __init__(self):
            self._ip = "192.168.1.1"
            self.reresolve_calls = 0

        def current(self):
            return self._ip

        def maybe_reresolve(self):
            self.reresolve_calls += 1
            self._ip = "10.0.1.1"
            return "10.0.1.1"

    r = _ChangingResolver()
    events = _RecordingEvents()
    ctx = TickContext(gateway_resolver=r, events=events, loki=object())

    ip = handle_gateway_reresolution(ctx)
    assert ip == "10.0.1.1"  # returns the NEW ip for build_info
    assert events.changes == [("192.168.1.1", "10.0.1.1")]
