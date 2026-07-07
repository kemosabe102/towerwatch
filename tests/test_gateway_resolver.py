"""Tests for towerwatch.net.GatewayResolver — periodic gateway re-resolution.

The resolver retires the "frozen gateway IP" bug class: GATEWAY_IP was resolved
once at config-import time, so a boot race (config imported before DHCP installed
the default route) froze it on the fallback forever. The resolver re-resolves on a
cadence and reports when the IP changes, so a DHCP renewal / router reboot heals
without a process restart, and the change is observable.
"""

from __future__ import annotations

from towerwatch.net import GatewayResolver

PROC_ROUTE_HEADER = (
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
)


def _route(gw_hex: str) -> str:
    return PROC_ROUTE_HEADER + f"eth0\t00000000\t{gw_hex}\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"


def test_override_wins_and_never_resolves(tmp_path):
    """When an override is set, the resolver returns it and ignores discovery."""
    route = tmp_path / "route"
    route.write_text(_route("0101000A"), encoding="ascii")  # 10.0.1.1 in the file
    r = GatewayResolver(
        override="192.168.99.1",
        fallback="1.2.3.4",
        route_path=route,
        is_windows=False,
    )
    assert r.current() == "192.168.99.1"


def test_discovers_from_route_when_no_override(tmp_path):
    route = tmp_path / "route"
    route.write_text(_route("0101000A"), encoding="ascii")  # 10.0.1.1
    r = GatewayResolver(override=None, fallback="1.2.3.4", route_path=route, is_windows=False)
    assert r.current() == "10.0.1.1"


def test_falls_back_when_no_default_route(tmp_path):
    route = tmp_path / "route"
    route.write_text(PROC_ROUTE_HEADER, encoding="ascii")  # header only, no default route
    r = GatewayResolver(override=None, fallback="9.9.9.9", route_path=route, is_windows=False)
    assert r.current() == "9.9.9.9"


class _FakeClock:
    """A monotonic clock we can advance by hand."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def test_reresolve_heals_frozen_fallback(tmp_path):
    """The exact bug: first resolve froze on the fallback (no route yet); once
    the route appears, re-resolution picks up the real IP and reports the change."""
    route = tmp_path / "route"
    route.write_text(PROC_ROUTE_HEADER, encoding="ascii")  # no default route yet
    clock = _FakeClock()
    r = GatewayResolver(
        override=None,
        fallback="192.168.1.1",
        route_path=route,
        is_windows=False,
        reresolve_interval_s=300,
        clock=clock,
    )
    assert r.current() == "192.168.1.1"  # frozen on fallback at boot

    # DHCP installs the default route (10.0.1.1) after boot.
    route.write_text(_route("0101000A"), encoding="ascii")

    # Before the interval elapses, nothing changes.
    clock.advance(299)
    assert r.maybe_reresolve() is None
    assert r.current() == "192.168.1.1"

    # After the interval, re-resolution heals and reports the new IP.
    clock.advance(2)
    assert r.maybe_reresolve() == "10.0.1.1"
    assert r.current() == "10.0.1.1"


def test_reresolve_returns_none_when_unchanged(tmp_path):
    route = tmp_path / "route"
    route.write_text(_route("0101000A"), encoding="ascii")
    clock = _FakeClock()
    r = GatewayResolver(
        override=None,
        fallback="1.2.3.4",
        route_path=route,
        is_windows=False,
        reresolve_interval_s=300,
        clock=clock,
    )
    assert r.current() == "10.0.1.1"
    clock.advance(301)
    assert r.maybe_reresolve() is None  # same IP → no change reported
    assert r.current() == "10.0.1.1"


def test_startup_retry_waits_for_route_then_succeeds(tmp_path):
    """Boot race: the default route isn't in place on the first read. resolve_with_retry
    retries (sleeping via an injected sleeper) until the route appears, avoiding the
    freeze-on-fallback that caused the whole bug."""
    route = tmp_path / "route"
    route.write_text(PROC_ROUTE_HEADER, encoding="ascii")  # no default route yet
    slept = []

    def fake_sleep(s):
        slept.append(s)
        # The route appears after the first retry sleep (DHCP finished).
        if len(slept) == 1:
            route.write_text(_route("0101000A"), encoding="ascii")

    r = GatewayResolver(override=None, fallback="192.168.1.1", route_path=route, is_windows=False)
    ip = r.resolve_with_retry(attempts=5, delay_s=2, sleeper=fake_sleep)
    assert ip == "10.0.1.1"  # got the real IP, not the fallback
    assert r.current() == "10.0.1.1"
    assert len(slept) == 1  # retried once, then succeeded


def test_startup_retry_gives_up_and_uses_fallback(tmp_path):
    """If the route never appears, retry exhausts and returns the fallback (no hang)."""
    route = tmp_path / "route"
    route.write_text(PROC_ROUTE_HEADER, encoding="ascii")
    slept = []
    r = GatewayResolver(override=None, fallback="9.9.9.9", route_path=route, is_windows=False)
    ip = r.resolve_with_retry(attempts=3, delay_s=1, sleeper=slept.append)
    assert ip == "9.9.9.9"
    assert len(slept) == 2  # attempts-1 sleeps between 3 tries


def test_startup_retry_skipped_when_override_set(tmp_path):
    """With an override there's no discovery to retry — returns immediately, no sleeps."""
    route = tmp_path / "route"
    route.write_text(PROC_ROUTE_HEADER, encoding="ascii")
    slept = []
    r = GatewayResolver(override="10.0.0.1", fallback="9.9.9.9", route_path=route, is_windows=False)
    ip = r.resolve_with_retry(attempts=5, delay_s=1, sleeper=slept.append)
    assert ip == "10.0.0.1"
    assert slept == []


def test_override_never_reresolves(tmp_path):
    """With an override set, maybe_reresolve is always a no-op even past the interval."""
    route = tmp_path / "route"
    route.write_text(_route("0101000A"), encoding="ascii")
    clock = _FakeClock()
    r = GatewayResolver(
        override="10.9.9.9",
        fallback="1.2.3.4",
        route_path=route,
        is_windows=False,
        reresolve_interval_s=300,
        clock=clock,
    )
    assert r.current() == "10.9.9.9"
    clock.advance(10_000)
    assert r.maybe_reresolve() is None
    assert r.current() == "10.9.9.9"
