"""Tests for HTTPLatencyProbe / HTTPThroughputProbe.

Every collaborator is injected — no `patch`, no `monkeypatch`.
"""

import requests

from tests.fakes import FakeClock, FakeLoki, FakeResponse, FakeSession


def _ok_resp(content=b"x" * 10_000):
    return FakeResponse(status_code=200, content=content)


# ---------------------------------------------------------------------------
# Latency
# ---------------------------------------------------------------------------
def test_http_latency_happy_path():
    from towerwatch.probes.http import HTTPLatencyProbe

    session = FakeSession(get_responses=[_ok_resp()])
    probe = HTTPLatencyProbe(
        session=session,
        clock=FakeClock(perf=[0.0, 0.080]),
        loki=FakeLoki(),
    )
    assert probe.measure() == 80
    assert len(session.get_calls) == 1


def test_http_latency_connection_error_returns_zero():
    from towerwatch.probes.http import HTTPLatencyProbe

    probe = HTTPLatencyProbe(
        session=FakeSession(get_responses=[requests.ConnectionError("down")]),
        clock=FakeClock(perf=[0.0]),
        loki=FakeLoki(),
    )
    assert probe.measure() == 0


def test_http_latency_timeout_returns_zero():
    from towerwatch.probes.http import HTTPLatencyProbe

    probe = HTTPLatencyProbe(
        session=FakeSession(get_responses=[requests.Timeout("slow")]),
        clock=FakeClock(perf=[0.0]),
        loki=FakeLoki(),
    )
    assert probe.measure() == 0


def test_http_latency_raises_for_status_caught():
    from towerwatch.probes.http import HTTPLatencyProbe

    bad = FakeResponse(status_code=500, content=b"")
    bad._raise = requests.HTTPError("500")
    probe = HTTPLatencyProbe(
        session=FakeSession(get_responses=[bad]),
        clock=FakeClock(perf=[0.0, 0.010]),
        loki=FakeLoki(),
    )
    assert probe.measure() == 0


# ---------------------------------------------------------------------------
# Throughput happy + guard paths
# ---------------------------------------------------------------------------
def test_http_throughput_happy_path():
    from towerwatch.probes.http import HTTPThroughputProbe

    loki = FakeLoki()
    probe = HTTPThroughputProbe(
        session=FakeSession(get_responses=[_ok_resp(content=b"x" * 1_000_000)]),
        clock=FakeClock(perf=[0.0, 1.0]),
        loki=loki,
    )
    result = probe.measure()
    assert result == {
        "http_throughput_ms": 1000,
        "http_throughput_mbps": 8.0,
        "http_throughput_bytes": 1_000_000,
    }
    # On success we emit the OK event with bytes_used so the dashboard can
    # show real per-test data cost (no more guessing at "~400 MB").
    ok_pushes = [lp for lp in loki.log_and_pushes if "throughput" in str(lp[2].get("event", ""))]
    assert len(ok_pushes) == 1
    assert ok_pushes[0][2]["bytes_used"] == 1_000_000


def test_http_throughput_zero_elapsed_returns_zeros():
    """Guard against division by zero when perf_counter returns identical values."""
    from towerwatch.probes.http import HTTPThroughputProbe

    loki = FakeLoki()
    probe = HTTPThroughputProbe(
        session=FakeSession(get_responses=[_ok_resp(content=b"x" * 1_000_000)]),
        clock=FakeClock(perf=[0.0, 0.0]),
        loki=loki,
    )
    assert probe.measure() == {
        "http_throughput_ms": 0,
        "http_throughput_mbps": 0,
        "http_throughput_bytes": 0,
    }
    # Failed event emitted with an `error=` containing the diagnostic message
    assert any("invalid sample" in (lp[2].get("error") or "") for lp in loki.log_and_pushes)


def test_http_throughput_empty_body_returns_zeros():
    from towerwatch.probes.http import HTTPThroughputProbe

    loki = FakeLoki()
    probe = HTTPThroughputProbe(
        session=FakeSession(get_responses=[_ok_resp(content=b"")]),
        clock=FakeClock(perf=[0.0, 1.0]),
        loki=loki,
    )
    assert probe.measure() == {
        "http_throughput_ms": 0,
        "http_throughput_mbps": 0,
        "http_throughput_bytes": 0,
    }
    assert any("invalid sample" in (lp[2].get("error") or "") for lp in loki.log_and_pushes)


# ---------------------------------------------------------------------------
# Throughput error paths
# ---------------------------------------------------------------------------
def test_http_throughput_timeout_returns_zeros():
    from towerwatch.probes.http import HTTPThroughputProbe

    loki = FakeLoki()
    probe = HTTPThroughputProbe(
        session=FakeSession(get_responses=[requests.Timeout("timed out")]),
        clock=FakeClock(perf=[0.0]),
        loki=loki,
    )
    assert probe.measure() == {
        "http_throughput_ms": 0,
        "http_throughput_mbps": 0,
        "http_throughput_bytes": 0,
    }
    assert any("timed out" in (lp[2].get("error") or "") for lp in loki.log_and_pushes)


def test_http_throughput_connection_error_returns_zeros():
    from towerwatch.probes.http import HTTPThroughputProbe

    probe = HTTPThroughputProbe(
        session=FakeSession(get_responses=[requests.ConnectionError("reset")]),
        clock=FakeClock(perf=[0.0]),
        loki=FakeLoki(),
    )
    assert probe.measure() == {
        "http_throughput_ms": 0,
        "http_throughput_mbps": 0,
        "http_throughput_bytes": 0,
    }


def test_http_throughput_4xx_returns_zeros():
    from towerwatch.probes.http import HTTPThroughputProbe

    bad = FakeResponse(status_code=404, content=b"")
    bad._raise = requests.HTTPError("404")
    probe = HTTPThroughputProbe(
        session=FakeSession(get_responses=[bad]),
        clock=FakeClock(perf=[0.0, 0.01]),
        loki=FakeLoki(),
    )
    assert probe.measure() == {
        "http_throughput_ms": 0,
        "http_throughput_mbps": 0,
        "http_throughput_bytes": 0,
    }


def test_http_throughput_short_body_uses_actual_bytes():
    """Pins current behaviour: short body computes mbps off what arrived."""
    from towerwatch.probes.http import HTTPThroughputProbe

    probe = HTTPThroughputProbe(
        session=FakeSession(get_responses=[_ok_resp(content=b"x" * 100)]),
        clock=FakeClock(perf=[0.0, 1.0]),
        loki=FakeLoki(),
    )
    result = probe.measure()
    assert result["http_throughput_ms"] == 1000
    assert result["http_throughput_mbps"] == round((100 * 8) / 1.0 / 1_000_000, 2)
    assert result["http_throughput_bytes"] == 100


# ---------------------------------------------------------------------------
# Upload probe — symmetric tests with download (uses POST, fixed payload size)
# ---------------------------------------------------------------------------
def _fake_rand(payload):
    """Return a callable matching os.urandom signature, returning fixed bytes."""

    def _gen(n):
        return payload[:n] if len(payload) >= n else payload + b"\x00" * (n - len(payload))

    return _gen


def test_http_upload_happy_path():
    from towerwatch.probes.http import HTTPUploadProbe

    loki = FakeLoki()
    probe = HTTPUploadProbe(
        session=FakeSession(post_responses=[_ok_resp(content=b"")]),
        clock=FakeClock(perf=[0.0, 1.0]),
        loki=loki,
        bytes_to_upload=2_000_000,
        rand_bytes=_fake_rand(b"x" * 2_000_000),
    )
    result = probe.measure()
    assert result == {
        "http_upload_ms": 1000,
        "http_upload_mbps": 16.0,
        "http_upload_bytes": 2_000_000,
    }
    ok = [lp for lp in loki.log_and_pushes if "upload" in str(lp[2].get("event", ""))]
    assert len(ok) == 1
    assert ok[0][2]["bytes_used"] == 2_000_000


def test_http_upload_zero_elapsed_returns_zeros():
    from towerwatch.probes.http import HTTPUploadProbe

    loki = FakeLoki()
    probe = HTTPUploadProbe(
        session=FakeSession(post_responses=[_ok_resp(content=b"")]),
        clock=FakeClock(perf=[0.0, 0.0]),
        loki=loki,
        bytes_to_upload=1_000_000,
        rand_bytes=_fake_rand(b"x" * 1_000_000),
    )
    assert probe.measure() == {
        "http_upload_ms": 0,
        "http_upload_mbps": 0,
        "http_upload_bytes": 0,
    }
    assert any("invalid sample" in (lp[2].get("error") or "") for lp in loki.log_and_pushes)


def test_http_upload_timeout_returns_zeros():
    from towerwatch.probes.http import HTTPUploadProbe

    loki = FakeLoki()
    probe = HTTPUploadProbe(
        session=FakeSession(post_responses=[requests.Timeout("slow upload")]),
        clock=FakeClock(perf=[0.0]),
        loki=loki,
        bytes_to_upload=1_000_000,
        rand_bytes=_fake_rand(b"x" * 1_000_000),
    )
    assert probe.measure() == {
        "http_upload_ms": 0,
        "http_upload_mbps": 0,
        "http_upload_bytes": 0,
    }
    assert any("slow upload" in (lp[2].get("error") or "") for lp in loki.log_and_pushes)


def test_http_upload_5xx_returns_zeros():
    from towerwatch.probes.http import HTTPUploadProbe

    bad = FakeResponse(status_code=503, content=b"")
    bad._raise = requests.HTTPError("503")
    probe = HTTPUploadProbe(
        session=FakeSession(post_responses=[bad]),
        clock=FakeClock(perf=[0.0, 0.5]),
        loki=FakeLoki(),
        bytes_to_upload=1_000_000,
        rand_bytes=_fake_rand(b"x" * 1_000_000),
    )
    assert probe.measure() == {
        "http_upload_ms": 0,
        "http_upload_mbps": 0,
        "http_upload_bytes": 0,
    }


def test_http_upload_uses_post_with_octet_stream():
    """Upload posts to the configured URL with octet-stream content type."""
    from towerwatch.probes.http import HTTPUploadProbe

    session = FakeSession(post_responses=[_ok_resp(content=b"")])
    probe = HTTPUploadProbe(
        session=session,
        clock=FakeClock(perf=[0.0, 0.5]),
        loki=FakeLoki(),
        url="https://example.invalid/__up",
        bytes_to_upload=500,
        rand_bytes=_fake_rand(b"x" * 500),
    )
    probe.measure()
    assert len(session.post_calls) == 1
    url, kwargs = session.post_calls[0]
    assert url == "https://example.invalid/__up"
    assert kwargs["headers"]["Content-Type"] == "application/octet-stream"
    assert kwargs["data"] == b"x" * 500


# ---------------------------------------------------------------------------
# Session injection pins: probe does NOT cache / share sessions across calls
# ---------------------------------------------------------------------------
def test_probe_uses_injected_session_across_measure_calls():
    """Each call to .measure() reuses the injected session."""
    from towerwatch.probes.http import HTTPLatencyProbe

    session = FakeSession(get_responses=[_ok_resp(), _ok_resp()])
    probe = HTTPLatencyProbe(
        session=session,
        clock=FakeClock(perf=[0.0, 0.01, 0.0, 0.02]),
        loki=FakeLoki(),
    )
    probe.measure()
    probe.measure()
    assert len(session.get_calls) == 2
