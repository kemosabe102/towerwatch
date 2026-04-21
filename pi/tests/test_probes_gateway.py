"""Tests for probes/gateway.py — vendor-agnostic gateway health probe."""
import socket
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PI = Path(__file__).resolve().parents[1]
if str(_PI) not in sys.path:
    sys.path.insert(0, str(_PI))


# ---------------------------------------------------------------------------
# Baseline: TCP success
# ---------------------------------------------------------------------------
def test_baseline_tcp_success(monkeypatch):
    import probes.gateway as gw

    monkeypatch.setattr(gw.config, "GATEWAY_VENDOR", "")

    mock_sock = MagicMock()
    mock_sock.connect.return_value = None
    monkeypatch.setattr(gw.socket, "socket", lambda *a, **kw: mock_sock)

    with patch("probes.gateway.requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200)
        result = gw.poll_gateway()

    assert result["gateway_tcp_ms"] > 0 or result["gateway_tcp_ms"] == 0  # just no KeyError
    assert "gateway_tcp_ms" in result
    assert "gateway_http_ms" in result


# ---------------------------------------------------------------------------
# Baseline: HTTP failure returns 0
# ---------------------------------------------------------------------------
def test_baseline_http_failure(monkeypatch):
    import probes.gateway as gw

    monkeypatch.setattr(gw.config, "GATEWAY_VENDOR", "")

    mock_sock = MagicMock()
    mock_sock.connect.return_value = None
    monkeypatch.setattr(gw.socket, "socket", lambda *a, **kw: mock_sock)

    with patch("probes.gateway.requests.get", side_effect=OSError("unreachable")):
        result = gw.poll_gateway()

    assert result["gateway_http_ms"] == 0
    assert "gateway_tcp_ms" in result


# ---------------------------------------------------------------------------
# M6 vendor: delegates to poll_m6_signal
# ---------------------------------------------------------------------------
def test_m6_vendor_delegates_to_m6_probe(monkeypatch):
    import probes.gateway as gw

    monkeypatch.setattr(gw.config, "GATEWAY_VENDOR", "m6")

    mock_sock = MagicMock()
    monkeypatch.setattr(gw.socket, "socket", lambda *a, **kw: mock_sock)

    with patch("probes.gateway.requests.get", return_value=MagicMock(status_code=200)):
        with patch("probes.m6.poll_m6_signal", return_value={"m6_rsrp": -85, "m6_rsrq": -12}):
            result = gw.poll_gateway()

    assert result["m6_rsrp"] == -85
    assert result["m6_rsrq"] == -12
    assert "gateway_tcp_ms" in result


# ---------------------------------------------------------------------------
# Orbi vendor: parses connected client count from XML
# ---------------------------------------------------------------------------
def test_orbi_vendor_parses_client_count(monkeypatch):
    import probes.gateway as gw

    monkeypatch.setattr(gw.config, "GATEWAY_VENDOR", "orbi")
    monkeypatch.setattr(gw.config, "GATEWAY_IP", "192.168.1.1")
    monkeypatch.setattr(gw.config, "GATEWAY_TIMEOUT_S", 5)

    xml_body = """<?xml version="1.0"?>
<DevInfo>
  <ConnectedDeviceCount>12</ConnectedDeviceCount>
</DevInfo>"""

    mock_sock = MagicMock()
    monkeypatch.setattr(gw.socket, "socket", lambda *a, **kw: mock_sock)

    orbi_resp = MagicMock(status_code=200, text=xml_body)
    orbi_resp.raise_for_status.return_value = None

    def fake_get(url, timeout=5):
        if "DEV_INFO" in url:
            return orbi_resp
        return MagicMock(status_code=200)

    with patch("probes.gateway.requests.get", side_effect=fake_get):
        result = gw.poll_gateway()

    assert result["gateway_clients"] == 12
    assert "gateway_tcp_ms" in result
