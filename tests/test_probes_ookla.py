"""Tests for ookla.run_speedtest — no patch, fakes injected directly."""
import json
import subprocess
import sys
from pathlib import Path

_PI = Path(__file__).resolve().parents[1]
if str(_PI) not in sys.path:
    sys.path.insert(0, str(_PI))

from tests.fakes import FakeCompletedProcess, FakeLoki, FakeSubprocess

_FIXTURES = Path(__file__).parent / "fixtures"


def test_ookla_ok_parses_speeds():
    import towerwatch.probes.ookla as ookla_mod
    raw = (_FIXTURES / "ookla_ok.json").read_text()
    data = json.loads(raw)
    expected_dl = round(data["download"]["bandwidth"] * 8 / 1_000_000, 2)
    expected_ul = round(data["upload"]["bandwidth"] * 8 / 1_000_000, 2)

    result = ookla_mod.run_speedtest(
        subprocess_run=FakeSubprocess(
            FakeCompletedProcess(stdout=raw, returncode=0)),
        loki=FakeLoki(),
        server_id=0, timeout_s=120, binary="speedtest",
    )
    assert result["download_mbps"] == expected_dl
    assert result["upload_mbps"] == expected_ul
    assert result["success"] == 1


def test_ookla_nonzero_returncode_returns_failure():
    import towerwatch.probes.ookla as ookla_mod
    raw = (_FIXTURES / "ookla_bad_returncode.json").read_text()
    result = ookla_mod.run_speedtest(
        subprocess_run=FakeSubprocess(
            FakeCompletedProcess(stdout=raw, returncode=1)),
        loki=FakeLoki(),
        server_id=0, timeout_s=120, binary="speedtest",
    )
    assert result["success"] == 0


def test_ookla_json_decode_error_returns_failure():
    import towerwatch.probes.ookla as ookla_mod
    result = ookla_mod.run_speedtest(
        subprocess_run=FakeSubprocess(
            FakeCompletedProcess(stdout="not json", returncode=0)),
        loki=FakeLoki(),
        server_id=0, timeout_s=120, binary="speedtest",
    )
    assert result["success"] == 0


def test_ookla_timeout_returns_failure():
    import towerwatch.probes.ookla as ookla_mod
    result = ookla_mod.run_speedtest(
        subprocess_run=FakeSubprocess(
            subprocess.TimeoutExpired("speedtest", 120)),
        loki=FakeLoki(),
        server_id=0, timeout_s=120, binary="speedtest",
    )
    assert result["success"] == 0


def test_ookla_missing_download_key_returns_failure():
    import towerwatch.probes.ookla as ookla_mod
    bad_json = json.dumps({"upload": {"bandwidth": 1000000}})
    result = ookla_mod.run_speedtest(
        subprocess_run=FakeSubprocess(
            FakeCompletedProcess(stdout=bad_json, returncode=0)),
        loki=FakeLoki(),
        server_id=0, timeout_s=120, binary="speedtest",
    )
    assert result["success"] == 0


def test_ookla_zero_bandwidth_returns_zero_mbps():
    import towerwatch.probes.ookla as ookla_mod
    data = {"download": {"bandwidth": 0}, "upload": {"bandwidth": 0}}
    result = ookla_mod.run_speedtest(
        subprocess_run=FakeSubprocess(
            FakeCompletedProcess(stdout=json.dumps(data), returncode=0)),
        loki=FakeLoki(),
        server_id=0, timeout_s=120, binary="speedtest",
    )
    assert result["download_mbps"] == 0.0
    assert result["upload_mbps"] == 0.0
    assert result["success"] == 1


def test_ookla_file_not_found_returns_failure():
    import towerwatch.probes.ookla as ookla_mod
    result = ookla_mod.run_speedtest(
        subprocess_run=FakeSubprocess(
            FileNotFoundError("speedtest not found")),
        loki=FakeLoki(),
        server_id=0, timeout_s=120, binary="speedtest",
    )
    assert result["success"] == 0


def test_ookla_server_id_included_in_cmd():
    import towerwatch.probes.ookla as ookla_mod
    data = {"download": {"bandwidth": 5_000_000}, "upload": {"bandwidth": 2_000_000}}
    runner = FakeSubprocess(
        FakeCompletedProcess(stdout=json.dumps(data), returncode=0))
    ookla_mod.run_speedtest(
        subprocess_run=runner,
        loki=FakeLoki(),
        server_id=12345, timeout_s=120, binary="speedtest",
    )
    cmd = runner.calls[0][0]
    assert "--server-id" in cmd
    assert "12345" in cmd


def test_ookla_failure_emits_event():
    import towerwatch.probes.ookla as ookla_mod
    loki = FakeLoki()
    ookla_mod.run_speedtest(
        subprocess_run=FakeSubprocess(
            FakeCompletedProcess(stdout="not json", returncode=0)),
        loki=loki,
        server_id=0, timeout_s=120, binary="speedtest",
    )
    assert any("speedtest" in (e or "") for e in loki.events())
