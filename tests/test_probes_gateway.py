"""Tests for GatewayProbe — no patch, fakes injected directly."""

from tests.fakes import FakeClock, FakeResponse, fake_socket_factory


def _http_ok(text=""):
    r = FakeResponse(status_code=200, text=text)
    return r


def _recording_get(responses):
    """Return a callable compatible with requests.get that pops responses in order."""
    queue = list(responses)
    calls = []

    def _get(url, **kwargs):
        calls.append((url, kwargs))
        if not queue:
            raise AssertionError(f"no response queued for {url}")
        r = queue.pop(0)
        if isinstance(r, Exception):
            raise r
        if callable(r):
            return r(url, kwargs)
        return r

    _get.calls = calls  # type: ignore[attr-defined]
    return _get


# ---------------------------------------------------------------------------
# Baseline TCP + HTTP
# ---------------------------------------------------------------------------
def test_baseline_tcp_and_http_success():
    from towerwatch.probes.gateway import GatewayProbe

    probe = GatewayProbe(
        vendor="",
        ip="192.168.1.1",
        tcp_port=80,
        timeout_s=2,
        socket_factory=fake_socket_factory(),
        requests_get=_recording_get([_http_ok()]),
        clock=FakeClock(perf=[0.0, 0.005, 0.0, 0.010]),
    )
    result = probe.poll()
    assert result["gateway_tcp_ms"] == 5.0
    assert result["gateway_http_ms"] == 10.0


def test_baseline_http_failure_returns_zero():
    from towerwatch.probes.gateway import GatewayProbe

    probe = GatewayProbe(
        vendor="",
        ip="192.168.1.1",
        tcp_port=80,
        timeout_s=2,
        socket_factory=fake_socket_factory(),
        requests_get=_recording_get([OSError("unreachable")]),
        clock=FakeClock(perf=[0.0, 0.005, 0.0]),
    )
    result = probe.poll()
    assert result["gateway_http_ms"] == 0
    assert result["gateway_tcp_ms"] > 0


def test_baseline_tcp_failure_returns_zero():
    from towerwatch.probes.gateway import GatewayProbe

    probe = GatewayProbe(
        vendor="",
        ip="192.168.1.1",
        tcp_port=80,
        timeout_s=2,
        socket_factory=fake_socket_factory(connect_raises=OSError("refused")),
        requests_get=_recording_get([_http_ok()]),
        clock=FakeClock(perf=[0.0, 0.0, 0.01]),
    )
    result = probe.poll()
    assert result["gateway_tcp_ms"] == 0


# ---------------------------------------------------------------------------
# M6 vendor delegation
# ---------------------------------------------------------------------------
def test_m6_vendor_delegates_via_injected_callable():
    from towerwatch.probes.gateway import GatewayProbe

    probe = GatewayProbe(
        vendor="m6",
        ip="192.168.1.1",
        tcp_port=80,
        timeout_s=2,
        socket_factory=fake_socket_factory(),
        requests_get=_recording_get([_http_ok()]),
        clock=FakeClock(perf=[0.0, 0.005, 0.0, 0.010]),
        m6_poll=lambda: {"m6_rsrp": -85, "m6_rsrq": -12},
    )
    result = probe.poll()
    assert result["m6_rsrp"] == -85
    assert result["m6_rsrq"] == -12
    assert "gateway_tcp_ms" in result


# ---------------------------------------------------------------------------
# Orbi vendor XML parse
# ---------------------------------------------------------------------------
def test_orbi_vendor_parses_client_count():
    from towerwatch.probes.gateway import GatewayProbe

    xml_body = (
        '<?xml version="1.0"?><DevInfo><ConnectedDeviceCount>12</ConnectedDeviceCount></DevInfo>'
    )

    # Two HTTP responses: baseline http fetch then /api/DEV_INFO
    probe = GatewayProbe(
        vendor="orbi",
        ip="192.168.1.1",
        tcp_port=80,
        timeout_s=5,
        socket_factory=fake_socket_factory(),
        requests_get=_recording_get(
            [
                _http_ok(),
                _http_ok(text=xml_body),
            ]
        ),
        clock=FakeClock(perf=[0.0, 0.005, 0.0, 0.010]),
    )
    result = probe.poll()
    assert result["gateway_clients"] == 12


def test_orbi_vendor_missing_element_returns_no_clients():
    from towerwatch.probes.gateway import GatewayProbe

    xml_body = '<?xml version="1.0"?><DevInfo></DevInfo>'
    probe = GatewayProbe(
        vendor="orbi",
        ip="192.168.1.1",
        tcp_port=80,
        timeout_s=5,
        socket_factory=fake_socket_factory(),
        requests_get=_recording_get([_http_ok(), _http_ok(text=xml_body)]),
        clock=FakeClock(perf=[0.0, 0.005, 0.0, 0.010]),
    )
    result = probe.poll()
    assert "gateway_clients" not in result
