"""Smoke tests for `towerwatch-speedtest` CLI — patches run_speedtest + clients."""

from unittest.mock import MagicMock, patch

import pytest


def _fake_loki_client():
    m = MagicMock()
    m.flush.return_value = None
    return m


def _fake_grafana_client(push_return=True):
    m = MagicMock()
    m.push_metrics.return_value = push_return
    return m


def _isolate_ssh_env(monkeypatch):
    """Clear SSH peer env vars so _resolve_operator doesn't call tailscale whois."""
    for var in ("SSH_CLIENT", "SSH_CONNECTION"):
        monkeypatch.delenv(var, raising=False)


def test_cli_success_prints_minimal_and_pushes_metric(capsys, monkeypatch):
    from towerwatch import speedtest_cli

    _isolate_ssh_env(monkeypatch)
    fake_grafana = _fake_grafana_client(push_return=True)
    fake_loki = _fake_loki_client()

    with (
        patch.object(
            speedtest_cli.grafana_mod.GrafanaClient, "from_config", return_value=fake_grafana
        ),
        patch.object(speedtest_cli.loki_mod.LokiClient, "from_config", return_value=fake_loki),
        patch.object(
            speedtest_cli,
            "run_speedtest",
            return_value={"download_mbps": 100.5, "upload_mbps": 25.0, "success": 1},
        ),
    ):
        rc = speedtest_cli.main(["--triggered-by", "alice"])

    assert rc == 0
    out = capsys.readouterr().out
    # Black-box output: started + success, no Mbps numbers visible to the user.
    assert "Speedtest started" in out
    assert "✓ Success" in out
    assert "Mbps" not in out
    fake_grafana.push_metrics.assert_called_once()
    pushed_lines = fake_grafana.push_metrics.call_args[0][0]
    assert len(pushed_lines) == 1
    assert "triggered_by=alice" in pushed_lines[0]
    assert "speedtest_download_mbps=100.5" in pushed_lines[0]


def test_cli_speedtest_failure_exits_nonzero(capsys, monkeypatch):
    from towerwatch import speedtest_cli

    _isolate_ssh_env(monkeypatch)
    fake_grafana = _fake_grafana_client()
    fake_loki = _fake_loki_client()

    with (
        patch.object(
            speedtest_cli.grafana_mod.GrafanaClient, "from_config", return_value=fake_grafana
        ),
        patch.object(speedtest_cli.loki_mod.LokiClient, "from_config", return_value=fake_loki),
        patch.object(
            speedtest_cli,
            "run_speedtest",
            return_value={"download_mbps": 0, "upload_mbps": 0, "success": 0},
        ),
    ):
        rc = speedtest_cli.main(["--triggered-by", "bob"])

    assert rc == 1
    fake_grafana.push_metrics.assert_not_called()
    err = capsys.readouterr().err
    assert "✗ Failed" in err


def test_cli_metric_push_failure_exits_nonzero(capsys, monkeypatch):
    from towerwatch import speedtest_cli

    _isolate_ssh_env(monkeypatch)
    fake_grafana = _fake_grafana_client(push_return=False)
    fake_loki = _fake_loki_client()

    with (
        patch.object(
            speedtest_cli.grafana_mod.GrafanaClient, "from_config", return_value=fake_grafana
        ),
        patch.object(speedtest_cli.loki_mod.LokiClient, "from_config", return_value=fake_loki),
        patch.object(
            speedtest_cli,
            "run_speedtest",
            return_value={"download_mbps": 50.0, "upload_mbps": 10.0, "success": 1},
        ),
    ):
        rc = speedtest_cli.main(["--triggered-by", "charlie"])

    assert rc == 1
    err = capsys.readouterr().err
    # User sees the same black-box failure message; operator disambiguates via Loki.
    assert "✗ Failed" in err


def test_cli_defaults_triggered_by_to_env(monkeypatch):
    from towerwatch import speedtest_cli

    _isolate_ssh_env(monkeypatch)
    monkeypatch.setenv("USER", "envuser")
    monkeypatch.delenv("SUDO_USER", raising=False)

    fake_grafana = _fake_grafana_client()
    fake_loki = _fake_loki_client()

    with (
        patch.object(
            speedtest_cli.grafana_mod.GrafanaClient, "from_config", return_value=fake_grafana
        ),
        patch.object(speedtest_cli.loki_mod.LokiClient, "from_config", return_value=fake_loki),
        patch.object(
            speedtest_cli,
            "run_speedtest",
            return_value={"download_mbps": 1.0, "upload_mbps": 1.0, "success": 1},
        ),
    ):
        rc = speedtest_cli.main([])

    assert rc == 0
    pushed_lines = fake_grafana.push_metrics.call_args[0][0]
    assert "triggered_by=envuser" in pushed_lines[0]


def test_cli_auto_detects_operator_from_tailscale_whois(monkeypatch):
    """SSH_CLIENT set + tailscale whois returns a LoginName -> used as triggered_by."""
    from towerwatch import speedtest_cli

    monkeypatch.setenv("SSH_CLIENT", "100.64.0.5 51234 22")
    monkeypatch.setenv("USER", "towerwatch-user")  # would be the fallback if whois failed

    fake_grafana = _fake_grafana_client()
    fake_loki = _fake_loki_client()

    with (
        patch.object(
            speedtest_cli.grafana_mod.GrafanaClient, "from_config", return_value=fake_grafana
        ),
        patch.object(speedtest_cli.loki_mod.LokiClient, "from_config", return_value=fake_loki),
        patch.object(
            speedtest_cli,
            "run_speedtest",
            return_value={"download_mbps": 9.0, "upload_mbps": 4.0, "success": 1},
        ),
        patch.object(speedtest_cli, "_tailscale_whois", return_value="alice@example.com"),
    ):
        rc = speedtest_cli.main([])

    assert rc == 0
    pushed_lines = fake_grafana.push_metrics.call_args[0][0]
    assert "triggered_by=alice@example.com" in pushed_lines[0]


def test_resolve_operator_falls_back_when_whois_fails(monkeypatch):
    """SSH_CLIENT set but whois returns None -> fall back to $USER."""
    from towerwatch import speedtest_cli

    monkeypatch.setenv("SSH_CLIENT", "100.64.0.5 51234 22")
    monkeypatch.setenv("USER", "fallback-user")
    monkeypatch.delenv("SUDO_USER", raising=False)

    with patch.object(speedtest_cli, "_tailscale_whois", return_value=None):
        assert speedtest_cli._resolve_operator() == "fallback-user"


def test_tailscale_whois_parses_login_name():
    """_tailscale_whois extracts UserProfile.LoginName from JSON output."""
    from unittest.mock import MagicMock

    from towerwatch import speedtest_cli

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stdout = '{"UserProfile": {"LoginName": "kemosabe102@github"}}'
    fake_run = MagicMock(return_value=fake_proc)

    with patch.object(speedtest_cli.shutil, "which", return_value="/usr/bin/tailscale"):
        result = speedtest_cli._tailscale_whois("100.64.0.5", run=fake_run)

    assert result == "kemosabe102@github"


def test_tailscale_whois_returns_none_when_binary_missing():
    from towerwatch import speedtest_cli

    with patch.object(speedtest_cli.shutil, "which", return_value=None):
        assert speedtest_cli._tailscale_whois("100.64.0.5") is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("50M", 50_000_000),
        ("25_000_000", 25_000_000),
        ("1G", 1_000_000_000),
        ("500k", 500_000),
        ("1234", 1234),
        ("1.5M", 1_500_000),
    ],
)
def test_parse_size_accepts_suffixes(raw, expected):
    from towerwatch import speedtest_cli

    assert speedtest_cli._parse_size(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "0", "-5M", "5X"])
def test_parse_size_rejects_bad_input(raw):
    from towerwatch import speedtest_cli

    with pytest.raises(ValueError):
        speedtest_cli._parse_size(raw)


def test_cli_max_bytes_threads_into_run_speedtest(capsys, monkeypatch):
    """--max-bytes 50M reaches run_speedtest as max_total_bytes=50_000_000."""
    from towerwatch import speedtest_cli

    _isolate_ssh_env(monkeypatch)
    fake_grafana = _fake_grafana_client(push_return=True)
    fake_loki = _fake_loki_client()
    fake_run = MagicMock(return_value={"download_mbps": 12.0, "upload_mbps": 3.0, "success": 1})

    with (
        patch.object(
            speedtest_cli.grafana_mod.GrafanaClient, "from_config", return_value=fake_grafana
        ),
        patch.object(speedtest_cli.loki_mod.LokiClient, "from_config", return_value=fake_loki),
        patch.object(speedtest_cli, "run_speedtest", fake_run),
    ):
        rc = speedtest_cli.main(["--triggered-by", "alice", "--max-bytes", "50M"])

    assert rc == 0
    assert fake_run.call_args.kwargs["max_total_bytes"] == 50_000_000
    # Started line reflects the cap, not the default ~550 MB.
    out = capsys.readouterr().out
    assert "capped at ~50 MB" in out


def test_cli_no_max_bytes_passes_none(monkeypatch):
    """Without --max-bytes, run_speedtest gets max_total_bytes=None (config defaults)."""
    from towerwatch import speedtest_cli

    _isolate_ssh_env(monkeypatch)
    monkeypatch.setenv("USER", "envuser")
    fake_run = MagicMock(return_value={"download_mbps": 1.0, "upload_mbps": 1.0, "success": 1})

    with (
        patch.object(
            speedtest_cli.grafana_mod.GrafanaClient,
            "from_config",
            return_value=_fake_grafana_client(),
        ),
        patch.object(
            speedtest_cli.loki_mod.LokiClient, "from_config", return_value=_fake_loki_client()
        ),
        patch.object(speedtest_cli, "run_speedtest", fake_run),
    ):
        rc = speedtest_cli.main([])

    assert rc == 0
    assert fake_run.call_args.kwargs["max_total_bytes"] is None


def test_run_speedtest_respects_byte_cap_override():
    """run_speedtest(max_total_bytes=N) builds a probe capped at N both directions."""
    from towerwatch.probes import cloudflare

    captured = {}

    class _FakeProbe:
        def __init__(self, *, loki=None, dl_max_total_bytes=None, ul_max_total_bytes=None):
            captured["loki"] = loki
            captured["dl"] = dl_max_total_bytes
            captured["ul"] = ul_max_total_bytes

        def measure_download(self):
            return {"http_throughput_mbps": 10.0, "http_throughput_bytes": 5}

        def measure_upload(self):
            return {"http_upload_mbps": 5.0, "http_upload_bytes": 3}

    with patch.object(cloudflare, "CloudflareThroughputProbe", _FakeProbe):
        result = cloudflare.run_speedtest(triggered_by="t", max_total_bytes=40_000_000)

    assert captured["dl"] == 40_000_000
    assert captured["ul"] == 40_000_000
    assert result["success"] == 1
