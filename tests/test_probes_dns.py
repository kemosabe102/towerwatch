"""Tests for DNSProbe — no patch, fakes injected directly."""
import socket
import sys
from pathlib import Path

_PI = Path(__file__).resolve().parents[1]
if str(_PI) not in sys.path:
    sys.path.insert(0, str(_PI))

from tests.fakes import FakeClock, FakeLoki, FakeResolver

import dns.resolver


def _make_probe(raises=None, result=None):
    from towerwatch.probes.dns import DNSProbe
    resolver = FakeResolver(result=result, raises=raises)
    return DNSProbe(
        "8.8.8.8",
        resolver_factory=lambda: resolver,
        clock=FakeClock(perf=[0.0, 0.025]),
        loki=FakeLoki(),
    ), resolver


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
def test_dns_success_returns_ms():
    probe, _ = _make_probe(result=[])
    assert probe.measure() == 25


# ---------------------------------------------------------------------------
# Error paths → 0
# ---------------------------------------------------------------------------
def test_dns_nxdomain_returns_zero():
    probe, _ = _make_probe(raises=dns.resolver.NXDOMAIN())
    assert probe.measure() == 0


def test_dns_no_answer_returns_zero():
    probe, _ = _make_probe(raises=dns.resolver.NoAnswer())
    assert probe.measure() == 0


def test_dns_timeout_returns_zero():
    probe, _ = _make_probe(raises=dns.resolver.Timeout())
    assert probe.measure() == 0


def test_dns_generic_exception_returns_zero():
    probe, _ = _make_probe(raises=Exception("generic"))
    assert probe.measure() == 0


def test_dns_no_nameservers_returns_zero():
    probe, _ = _make_probe(raises=dns.resolver.NoNameservers())
    assert probe.measure() == 0


def test_dns_socket_error_returns_zero():
    probe, _ = _make_probe(raises=socket.gaierror("unreachable"))
    assert probe.measure() == 0


def test_dns_oserror_returns_zero():
    probe, _ = _make_probe(raises=OSError("network down"))
    assert probe.measure() == 0


# ---------------------------------------------------------------------------
# Failure emits DNS failed event
# ---------------------------------------------------------------------------
def test_dns_failure_emits_event():
    from towerwatch.probes.dns import DNSProbe
    loki = FakeLoki()
    probe = DNSProbe(
        "1.1.1.1",
        resolver_factory=lambda: FakeResolver(raises=dns.resolver.Timeout()),
        clock=FakeClock(perf=[0.0]),
        loki=loki,
    )
    probe.measure()
    assert any(lp[2].get("event") == "dns_failed" for lp in loki.log_and_pushes)


# ---------------------------------------------------------------------------
# Nameserver/lifetime are applied to the resolver
# ---------------------------------------------------------------------------
def test_dns_resolver_configured_with_nameserver_and_lifetime():
    from towerwatch.probes.dns import DNSProbe
    resolver = FakeResolver(result=[])
    probe = DNSProbe(
        "1.1.1.1",
        resolver_factory=lambda: resolver,
        clock=FakeClock(perf=[0.0, 0.01]),
        loki=FakeLoki(),
        lifetime_s=3,
    )
    probe.measure()
    assert resolver.nameservers == ["1.1.1.1"]
    assert resolver.lifetime == 3
