"""Characterization tests for probes/http.py — 4 tests."""
from unittest.mock import patch, MagicMock

import pytest
import requests


def _fake_response(content=b"x" * 10000, status=200):
    resp = MagicMock()
    resp.content = content
    resp.status_code = status
    resp.raise_for_status.return_value = None
    return resp


def test_http_latency_happy_path():
    import probes.http as http_mod

    with patch("probes.http.requests.get", return_value=_fake_response()) as mock_get:
        with patch("probes.http.time.perf_counter", side_effect=[0.0, 0.080]):
            result = http_mod.measure_http_latency()
    assert result == 80
    mock_get.assert_called_once()

def test_http_latency_error_returns_zero():
    import probes.http as http_mod

    with patch("probes.http.requests.get", side_effect=requests.ConnectionError("down")):
        result = http_mod.measure_http_latency()
    assert result == 0

def test_http_throughput_happy_path():
    import probes.http as http_mod

    content = b"x" * 1_000_000
    with patch("probes.http.requests.get", return_value=_fake_response(content=content)):
        with patch("probes.http.time.perf_counter", side_effect=[0.0, 1.0]):
            result = http_mod.measure_http_throughput()
    assert result["http_throughput_ms"] == 1000
    assert result["http_throughput_mbps"] == 8.0   # 1MB * 8 / 1s / 1e6

def test_http_throughput_error_returns_zeros():
    import probes.http as http_mod

    with patch("probes.http.requests.get", side_effect=Exception("timeout")):
        result = http_mod.measure_http_throughput()
    assert result == {"http_throughput_ms": 0, "http_throughput_mbps": 0}

@pytest.mark.xfail(reason="Pass 6 fix: http probes create new Session per call — no session reuse")
def test_http_reuses_session():
    """Both latency and throughput probes should share a cached Session."""
    import probes.http as http_mod

    with patch("probes.http.requests.Session") as MockSession:
        instance = MockSession.return_value
        instance.get.return_value = _fake_response()
        http_mod.measure_http_latency()
        http_mod.measure_http_latency()
    # Session constructor called only once if reused
    assert MockSession.call_count == 1
