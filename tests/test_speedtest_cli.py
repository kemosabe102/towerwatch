"""Smoke tests for `towerwatch-speedtest` CLI — patches run_speedtest + clients."""

from unittest.mock import MagicMock, patch


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
