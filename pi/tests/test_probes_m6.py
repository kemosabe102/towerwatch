"""Characterization tests for probes/m6.py — 5 tests."""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_FIXTURES = Path(__file__).parent / "fixtures"


def _fake_session_get(json_data, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    return resp


def test_m6_parses_wwan_fields():
    import probes.m6 as m6_mod

    wwan = json.loads((_FIXTURES / "m6_wwan.json").read_text())
    mock_sess = MagicMock()
    mock_sess.get.return_value = _fake_session_get(wwan)

    with patch("probes.m6._ensure_m6_session", return_value=mock_sess):
        result = m6_mod.poll_m6_signal()

    assert result["m6_rsrp"] == -85
    assert result["m6_rsrq"] == -10
    assert result["m6_sinr"] == 15
    assert result["m6_band"] == 66

def test_m6_missing_keys_returns_empty():
    import probes.m6 as m6_mod

    mock_sess = MagicMock()
    mock_sess.get.return_value = _fake_session_get({})
    with patch("probes.m6._ensure_m6_session", return_value=mock_sess):
        result = m6_mod.poll_m6_signal()
    assert result == {}

def test_m6_malformed_json_returns_empty():
    import probes.m6 as m6_mod

    mock_sess = MagicMock()
    mock_sess.get.side_effect = Exception("json decode error")
    with patch("probes.m6._ensure_m6_session", return_value=mock_sess):
        result = m6_mod.poll_m6_signal()
    assert result == {}

def test_m6_401_returns_empty_and_clears_session():
    import probes.m6 as m6_mod

    mock_sess = MagicMock()
    mock_sess.get.return_value = _fake_session_get({}, status=401)
    with patch("probes.m6._ensure_m6_session", return_value=mock_sess):
        result = m6_mod.poll_m6_signal()
    assert result == {}
    assert m6_mod._m6_session is None

def test_m6_non_numeric_band_returns_zero():
    import probes.m6 as m6_mod

    wwan = {"RSRP": "-80", "RSRQ": "-9", "SINR": "12", "curBand": "n77"}
    mock_sess = MagicMock()
    mock_sess.get.return_value = _fake_session_get(wwan)
    with patch("probes.m6._ensure_m6_session", return_value=mock_sess):
        result = m6_mod.poll_m6_signal()
    assert result["m6_band"] == 0
