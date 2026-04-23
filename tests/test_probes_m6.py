"""Tests for M6Probe — no patch, fakes injected directly."""

import json
from pathlib import Path

from tests.fakes import FakeLoki, FakeResponse, FakeSession

_FIXTURES = Path(__file__).parent / "fixtures"


def _build_probe(responses=None, loki=None):
    """Build an M6Probe with a session_factory returning a FakeSession queued
    with the given responses."""
    from towerwatch.probes.m6 import M6Probe

    session = FakeSession(get_responses=responses or [])
    return M6Probe(
        session_factory=lambda: session,
        loki=loki or FakeLoki(),
        url="http://fake-m6/wwan",
        timeout_s=5,
    ), session


def _ok_resp(data, status=200):
    r = FakeResponse(status_code=status, _json=data)
    return r


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
def test_m6_parses_wwan_fields():
    wwan = json.loads((_FIXTURES / "m6_wwan.json").read_text())
    probe, _ = _build_probe(responses=[_ok_resp(wwan)])
    result = probe.poll()
    assert result["m6_rsrp"] == -85
    assert result["m6_rsrq"] == -10
    assert result["m6_sinr"] == 15
    assert result["m6_band"] == 66


def test_m6_missing_keys_returns_empty():
    probe, _ = _build_probe(responses=[_ok_resp({})])
    assert probe.poll() == {}


def test_m6_malformed_json_returns_empty():
    """resp.json() raises ValueError → empty dict."""
    probe, _ = _build_probe(
        responses=[FakeResponse(status_code=200, _json=ValueError("malformed"))]
    )
    assert probe.poll() == {}


# ---------------------------------------------------------------------------
# 401 clears session + emits auth-expired log
# ---------------------------------------------------------------------------
def test_m6_401_returns_empty_and_clears_session():
    loki = FakeLoki()
    probe, _ = _build_probe(responses=[_ok_resp({}, status=401)], loki=loki)
    # Force a session to exist before the call so we can see it get cleared
    probe._get_session()
    assert probe._session is not None
    assert probe.poll() == {}
    assert probe._session is None
    assert any(lp[2].get("event") == "m6_auth_expired" for lp in loki.log_and_pushes)


# ---------------------------------------------------------------------------
# 403 does NOT clear session and does NOT emit auth-expired
# ---------------------------------------------------------------------------
def test_m6_403_does_not_clear_session_or_emit_auth_expired():
    loki = FakeLoki()
    bad = FakeResponse(status_code=403, _raise=Exception("403 Forbidden"))
    probe, _ = _build_probe(responses=[bad], loki=loki)
    probe._get_session()
    assert probe.poll() == {}
    # 403 goes through generic except, not the 401 branch
    assert probe._session is not None
    assert not any(lp[2].get("event") == "m6_auth_expired" for lp in loki.log_and_pushes)


# ---------------------------------------------------------------------------
# Non-numeric band returns 0
# ---------------------------------------------------------------------------
def test_m6_non_numeric_band_returns_zero():
    wwan = {"RSRP": "-80", "RSRQ": "-9", "SINR": "12", "curBand": "n77"}
    probe, _ = _build_probe(responses=[_ok_resp(wwan)])
    result = probe.poll()
    assert result["m6_band"] == 0


# ---------------------------------------------------------------------------
# RSRP float-string raises in int() → generic except → empty
# ---------------------------------------------------------------------------
def test_m6_rsrp_float_string_returns_empty():
    wwan = {"RSRP": "-85.5", "RSRQ": "-9", "SINR": "12", "curBand": "66"}
    probe, _ = _build_probe(responses=[_ok_resp(wwan)])
    assert probe.poll() == {}
