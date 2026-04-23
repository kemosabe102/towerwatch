"""Contract tests: every Probe class returns a ProbeResult with expected keys.

No patch, no monkeypatch — all collaborators injected directly.
"""

import json

from tests.fakes import (
    FakeClock,
    FakeCompletedProcess,
    FakeLoki,
    FakeResolver,
    FakeResponse,
    FakeSession,
    FakeSubprocess,
    fake_socket_factory,
)
from towerwatch.probes.base import ProbeResult


# ---------------------------------------------------------------------------
# PingProbe
# ---------------------------------------------------------------------------
def test_ping_probe_returns_probe_result():
    from towerwatch.probes.ping import PingProbe

    fake_out = (
        "10 packets transmitted, 10 received, 0% packet loss\n"
        "rtt min/avg/max/mdev = 11.500/12.000/13.000/0.400 ms\n"
        "64 bytes from 8.8.8.8: icmp_seq=1 ttl=55 time=12.0 ms\n"
    )
    probe = PingProbe(
        "8.8.8.8",
        "google",
        subprocess_run=FakeSubprocess(FakeCompletedProcess(stdout=fake_out, returncode=0)),
        loki=FakeLoki(),
        is_windows=False,
    )
    result = probe.run()
    assert isinstance(result, ProbeResult)
    assert "rtt_avg_google" in result.fields
    assert "connected_google" in result.fields
    assert result.ok is True


# ---------------------------------------------------------------------------
# TCPProbe
# ---------------------------------------------------------------------------
def test_tcp_probe_returns_probe_result():
    from towerwatch.probes.tcp import TCPProbe

    probe = TCPProbe(
        socket_factory=fake_socket_factory(),
        clock=FakeClock(perf=[0.0, 0.015]),
    )
    result = probe.run()
    assert isinstance(result, ProbeResult)
    assert "tcp_connect_ms" in result.fields
    assert result.ok is True


# ---------------------------------------------------------------------------
# DNSProbe
# ---------------------------------------------------------------------------
def test_dns_probe_returns_probe_result():
    from towerwatch.probes.dns import DNSProbe

    probe = DNSProbe(
        "8.8.8.8",
        resolver_factory=lambda: FakeResolver(result=[]),
        clock=FakeClock(perf=[0.0, 0.025]),
        loki=FakeLoki(),
    )
    result = probe.run()
    assert isinstance(result, ProbeResult)
    assert "dns_resolve_ms_8_8_8_8" in result.fields
    assert result.ok is True


# ---------------------------------------------------------------------------
# HTTPLatencyProbe
# ---------------------------------------------------------------------------
def test_http_latency_probe_returns_probe_result():
    from towerwatch.probes.http import HTTPLatencyProbe

    probe = HTTPLatencyProbe(
        session=FakeSession(get_responses=[FakeResponse(content=b"x" * 10_000)]),
        clock=FakeClock(perf=[0.0, 0.080]),
        loki=FakeLoki(),
    )
    result = probe.run()
    assert isinstance(result, ProbeResult)
    assert "http_latency_ms" in result.fields
    assert result.ok is True


# ---------------------------------------------------------------------------
# HTTPThroughputProbe
# ---------------------------------------------------------------------------
def test_http_throughput_probe_returns_probe_result():
    from towerwatch.probes.http import HTTPThroughputProbe

    probe = HTTPThroughputProbe(
        session=FakeSession(get_responses=[FakeResponse(content=b"x" * 1_000_000)]),
        clock=FakeClock(perf=[0.0, 1.0]),
        loki=FakeLoki(),
    )
    result = probe.run()
    assert isinstance(result, ProbeResult)
    assert "http_throughput_ms" in result.fields
    assert "http_throughput_mbps" in result.fields


# ---------------------------------------------------------------------------
# M6Probe
# ---------------------------------------------------------------------------
def test_m6_probe_returns_probe_result():
    from towerwatch.probes.m6 import M6Probe

    wwan = {"RSRP": -85, "RSRQ": -10, "SINR": 15, "curBand": "66"}
    session = FakeSession(get_responses=[FakeResponse(_json=wwan)])
    probe = M6Probe(
        session_factory=lambda: session,
        loki=FakeLoki(),
        url="http://fake/m6",
        timeout_s=5,
    )
    result = probe.run()
    assert isinstance(result, ProbeResult)
    assert "m6_rsrp" in result.fields
    assert result.ok is True


# ---------------------------------------------------------------------------
# OoklaProbe
# ---------------------------------------------------------------------------
def test_ookla_probe_returns_probe_result():
    from towerwatch.probes.ookla import OoklaProbe

    data = {"download": {"bandwidth": 50_000_000}, "upload": {"bandwidth": 10_000_000}}
    probe = OoklaProbe(
        binary="speedtest",
        server_id=0,
        timeout_s=120,
        subprocess_run=FakeSubprocess(FakeCompletedProcess(stdout=json.dumps(data), returncode=0)),
        loki=FakeLoki(),
    )
    result = probe.run()
    assert isinstance(result, ProbeResult)
    assert "download_mbps" in result.fields
    assert "success" in result.fields
    assert result.ok is True


# ---------------------------------------------------------------------------
# All probes have a name attribute
# ---------------------------------------------------------------------------
def test_all_probes_have_name():
    from towerwatch.probes.dns import DNSProbe
    from towerwatch.probes.http import HTTPLatencyProbe, HTTPThroughputProbe
    from towerwatch.probes.m6 import M6Probe
    from towerwatch.probes.ookla import OoklaProbe
    from towerwatch.probes.ping import PingProbe
    from towerwatch.probes.tcp import TCPProbe

    probes = [
        PingProbe("8.8.8.8", "google", subprocess_run=FakeSubprocess(), loki=FakeLoki()),
        TCPProbe(socket_factory=fake_socket_factory(), clock=FakeClock()),
        DNSProbe(
            "8.8.8.8", resolver_factory=lambda: FakeResolver(), clock=FakeClock(), loki=FakeLoki()
        ),
        HTTPLatencyProbe(session=FakeSession(), clock=FakeClock(), loki=FakeLoki()),
        HTTPThroughputProbe(session=FakeSession(), clock=FakeClock(), loki=FakeLoki()),
        M6Probe(session_factory=FakeSession, loki=FakeLoki(), url="http://fake", timeout_s=5),
        OoklaProbe(
            binary="speedtest",
            server_id=0,
            timeout_s=120,
            subprocess_run=FakeSubprocess(),
            loki=FakeLoki(),
        ),
    ]
    for p in probes:
        assert isinstance(p.name, str) and p.name, f"{type(p).__name__} missing name"
