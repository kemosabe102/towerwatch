"""Characterization tests for probes/ookla.py — 4 tests."""
import json
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_FIXTURES = Path(__file__).parent / "fixtures"


def _fake_run(stdout="", returncode=0):
    result = MagicMock()
    result.stdout = stdout
    result.returncode = returncode
    return result


def test_ookla_ok_parses_speeds():
    import probes.ookla as ookla_mod

    raw = (_FIXTURES / "ookla_ok.json").read_text()
    data = json.loads(raw)
    expected_dl = round(data["download"]["bandwidth"] * 8 / 1_000_000, 2)
    expected_ul = round(data["upload"]["bandwidth"] * 8 / 1_000_000, 2)

    with patch("probes.ookla.subprocess.run", return_value=_fake_run(stdout=raw, returncode=0)):
        result = ookla_mod.run_speedtest()

    assert result["download_mbps"] == expected_dl
    assert result["upload_mbps"] == expected_ul
    assert result["success"] == 1

def test_ookla_nonzero_returncode_returns_failure():
    """Non-zero returncode returns success=0 via explicit returncode check."""
    import probes.ookla as ookla_mod

    raw = (_FIXTURES / "ookla_bad_returncode.json").read_text()
    with patch("probes.ookla.subprocess.run", return_value=_fake_run(stdout=raw, returncode=1)):
        result = ookla_mod.run_speedtest()
    assert result["success"] == 0

def test_ookla_json_decode_error_returns_failure():
    import probes.ookla as ookla_mod

    with patch("probes.ookla.subprocess.run", return_value=_fake_run(stdout="not json", returncode=0)):
        result = ookla_mod.run_speedtest()
    assert result["success"] == 0

def test_ookla_timeout_returns_failure():
    import probes.ookla as ookla_mod

    with patch("probes.ookla.subprocess.run", side_effect=subprocess.TimeoutExpired("speedtest", 120)):
        result = ookla_mod.run_speedtest()
    assert result["success"] == 0
