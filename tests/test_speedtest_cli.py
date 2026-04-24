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


def test_cli_success_prints_result_and_pushes_metric(capsys):
    from towerwatch import speedtest_cli

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
    assert "Download: 100.5 Mbps" in out
    assert "Upload:   25.0 Mbps" in out
    fake_grafana.push_metrics.assert_called_once()
    pushed_lines = fake_grafana.push_metrics.call_args[0][0]
    assert len(pushed_lines) == 1
    assert "triggered_by=alice" in pushed_lines[0]
    assert "speedtest_download_mbps=100.5" in pushed_lines[0]


def test_cli_speedtest_failure_exits_nonzero(capsys):
    from towerwatch import speedtest_cli

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


def test_cli_metric_push_failure_exits_nonzero(capsys):
    from towerwatch import speedtest_cli

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
    assert "metric push failed" in err.lower()


def test_cli_defaults_triggered_by_to_env(monkeypatch):
    from towerwatch import speedtest_cli

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
