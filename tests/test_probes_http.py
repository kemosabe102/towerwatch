"""Tests for HTTPLatencyProbe.

Throughput + upload moved to test_probes_cloudflare.py — they test the
multi-stream adaptive Cloudflare probe that replaced the old single-stream
implementations.
"""

import requests

from tests.fakes import FakeClock, FakeResponse, FakeSession


def _ok_resp(content=b"x" * 10_000):
    return FakeResponse(status_code=200, content=content)


def test_http_latency_happy_path():
    from towerwatch.probes.http import HTTPLatencyProbe

    session = FakeSession(get_responses=[_ok_resp()])
    probe = HTTPLatencyProbe(
        session=session,
        clock=FakeClock(perf=[0.0, 0.080]),
    )
    assert probe.measure() == 80
    assert len(session.get_calls) == 1


def test_http_latency_connection_error_returns_zero():
    from towerwatch.probes.http import HTTPLatencyProbe

    probe = HTTPLatencyProbe(
        session=FakeSession(get_responses=[requests.ConnectionError("down")]),
        clock=FakeClock(perf=[0.0]),
    )
    assert probe.measure() == 0


def test_http_latency_timeout_returns_zero():
    from towerwatch.probes.http import HTTPLatencyProbe

    probe = HTTPLatencyProbe(
        session=FakeSession(get_responses=[requests.Timeout("slow")]),
        clock=FakeClock(perf=[0.0]),
    )
    assert probe.measure() == 0


def test_http_latency_raises_for_status_caught():
    from towerwatch.probes.http import HTTPLatencyProbe

    bad = FakeResponse(status_code=500, content=b"")
    bad._raise = requests.HTTPError("500")
    probe = HTTPLatencyProbe(
        session=FakeSession(get_responses=[bad]),
        clock=FakeClock(perf=[0.0, 0.010]),
    )
    assert probe.measure() == 0


def test_probe_uses_injected_session_across_measure_calls():
    from towerwatch.probes.http import HTTPLatencyProbe

    session = FakeSession(get_responses=[_ok_resp(), _ok_resp()])
    probe = HTTPLatencyProbe(
        session=session,
        clock=FakeClock(perf=[0.0, 0.01, 0.0, 0.02]),
    )
    probe.measure()
    probe.measure()
    assert len(session.get_calls) == 2
