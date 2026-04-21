"""
Contract tests: every Probe class returns a ProbeResult with expected field keys.
Uses fakes/stubs so no real network calls occur.
"""
import json
import socket
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PI = Path(__file__).resolve().parents[1]
if str(_PI) not in sys.path:
    sys.path.insert(0, str(_PI))

from probes.base import ProbeResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _fake_resp(content=b"x" * 10_000, status=200):
    r = MagicMock()
    r.content = content
    r.status_code = status
    r.raise_for_status.return_value = None
    r.json.return_value = {}
    return r


# ---------------------------------------------------------------------------
# PingProbe
# ---------------------------------------------------------------------------
def test_ping_probe_returns_probe_result():
    from probes.ping import PingProbe
    probe = PingProbe("8.8.8.8", "google")
    fake_out = (
        "10 packets transmitted, 10 received, 0% packet loss\n"
        "rtt min/avg/max/mdev = 11.500/12.000/13.000/0.400 ms\n"
        "64 bytes from 8.8.8.8: icmp_seq=1 ttl=55 time=12.0 ms\n"
    )
    with patch("probes.ping.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=fake_out, returncode=0)
        result = probe.run()
    assert isinstance(result, ProbeResult)
    assert "rtt_avg_google" in result.fields
    assert "connected_google" in result.fields
    assert result.ok is True


# ---------------------------------------------------------------------------
# TCPProbe
# ---------------------------------------------------------------------------
def test_tcp_probe_returns_probe_result():
    from probes.tcp import TCPProbe
    probe = TCPProbe()
    mock_sock = MagicMock(spec=socket.socket)
    with patch("probes.tcp.socket.socket", return_value=mock_sock):
        with patch("probes.tcp.time.perf_counter", side_effect=[0.0, 0.015]):
            result = probe.run()
    assert isinstance(result, ProbeResult)
    assert "tcp_connect_ms" in result.fields
    assert result.ok is True


# ---------------------------------------------------------------------------
# DNSProbe
# ---------------------------------------------------------------------------
def test_dns_probe_returns_probe_result():
    from probes.dns import DNSProbe
    probe = DNSProbe("8.8.8.8")
    with patch("probes.dns.dns.resolver.Resolver") as MockResolver:
        instance = MockResolver.return_value
        instance.resolve.return_value = []
        with patch("probes.dns.time.perf_counter", side_effect=[0.0, 0.025]):
            result = probe.run()
    assert isinstance(result, ProbeResult)
    assert "dns_resolve_ms_8_8_8_8" in result.fields
    assert result.ok is True


# ---------------------------------------------------------------------------
# HTTPLatencyProbe
# ---------------------------------------------------------------------------
def test_http_latency_probe_returns_probe_result():
    import probes.http as http_mod
    from probes.http import HTTPLatencyProbe
    http_mod._http_session = None
    probe = HTTPLatencyProbe()
    with patch("probes.http.requests.Session") as MockSession:
        MockSession.return_value.get.return_value = _fake_resp()
        with patch("probes.http.time.perf_counter", side_effect=[0.0, 0.080]):
            result = probe.run()
    assert isinstance(result, ProbeResult)
    assert "http_latency_ms" in result.fields
    assert result.ok is True
    http_mod._http_session = None


# ---------------------------------------------------------------------------
# HTTPThroughputProbe
# ---------------------------------------------------------------------------
def test_http_throughput_probe_returns_probe_result():
    import probes.http as http_mod
    from probes.http import HTTPThroughputProbe
    http_mod._http_session = None
    probe = HTTPThroughputProbe()
    with patch("probes.http.requests.Session") as MockSession:
        MockSession.return_value.get.return_value = _fake_resp(content=b"x" * 1_000_000)
        with patch("probes.http.time.perf_counter", side_effect=[0.0, 1.0]):
            result = probe.run()
    assert isinstance(result, ProbeResult)
    assert "http_throughput_ms" in result.fields
    assert "http_throughput_mbps" in result.fields
    http_mod._http_session = None


# ---------------------------------------------------------------------------
# M6Probe
# ---------------------------------------------------------------------------
def test_m6_probe_returns_probe_result():
    from probes.m6 import M6Probe
    probe = M6Probe()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"RSRP": -85, "RSRQ": -10, "SINR": 15, "curBand": "66"}
    with patch("probes.m6.requests.Session") as MockSession:
        MockSession.return_value.get.return_value = mock_resp
        import probes.m6 as m6_mod
        m6_mod._m6_session = None
        result = probe.run()
    assert isinstance(result, ProbeResult)
    assert "m6_rsrp" in result.fields
    assert result.ok is True
    m6_mod._m6_session = None


# ---------------------------------------------------------------------------
# OoklaProbe
# ---------------------------------------------------------------------------
def test_ookla_probe_returns_probe_result():
    from probes.ookla import OoklaProbe
    import json as _json
    probe = OoklaProbe()
    data = {"download": {"bandwidth": 50_000_000}, "upload": {"bandwidth": 10_000_000}}
    with patch("probes.ookla.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout=_json.dumps(data), returncode=0
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
    from probes.ping import PingProbe
    from probes.tcp import TCPProbe
    from probes.dns import DNSProbe
    from probes.http import HTTPLatencyProbe, HTTPThroughputProbe
    from probes.m6 import M6Probe
    from probes.ookla import OoklaProbe

    probes = [
        PingProbe("8.8.8.8", "google"),
        TCPProbe(),
        DNSProbe("8.8.8.8"),
        HTTPLatencyProbe(),
        HTTPThroughputProbe(),
        M6Probe(),
        OoklaProbe(),
    ]
    for p in probes:
        assert isinstance(p.name, str) and p.name, f"{type(p).__name__} missing name"
