"""Tests for events.py — one per event function, asserting payload shape and level."""

from unittest.mock import MagicMock

from towerwatch import config, events


def _make_loki():
    """Return a fake LokiClient that records push() calls."""
    loki = MagicMock()
    loki.push.return_value = None
    loki.log_and_push.return_value = None
    return loki


def _pushed(loki):
    """Return (level, message, extra) from the most recent push() call."""
    assert loki.push.called, "loki.push was not called"
    args = loki.push.call_args
    return args[0][0], args[0][1], args[0][2] if len(args[0]) > 2 else (args[1] or {})


# ---------------------------------------------------------------------------
def test_service_restarted_level_and_event():
    loki = _make_loki()
    events.service_restarted(loki, version="abc1234", build_date="2026-01-01", platform="linux")
    level, msg, extra = _pushed(loki)
    assert level == "WARN"
    assert extra["event"] == config.LOG_EVENT_SERVICE_RESTARTED
    assert extra["version"] == "abc1234"


def test_service_started_level_and_event():
    loki = _make_loki()
    events.service_started(loki, log_level="WARN", platform="linux")
    level, msg, extra = _pushed(loki)
    assert level == "INFO"
    assert extra["event"] == config.LOG_EVENT_SERVICE_STARTED
    assert extra["log_level"] == "WARN"


def test_connection_down_level_and_event():
    loki = _make_loki()
    events.connection_down(loki)
    level, msg, extra = _pushed(loki)
    assert level == "ERROR"
    assert extra["event"] == config.LOG_EVENT_CONN_DOWN


def test_connection_restored_uses_log_and_push():
    loki = _make_loki()
    events.connection_restored(loki, down_duration_s=300)
    assert loki.log_and_push.called
    kw = loki.log_and_push.call_args[1]
    assert kw["event"] == config.LOG_EVENT_CONN_RESTORED
    assert kw["down_duration_s"] == 300


def test_outage_recorded_gap_fields():
    loki = _make_loki()
    events.outage_recorded(loki, gap_seconds=900, reason="network_unreachable", version="abc")
    level, msg, extra = _pushed(loki)
    assert level == "WARN"
    assert extra["event"] == config.LOG_EVENT_OUTAGE_RECORDED
    assert extra["gap_seconds"] == 900
    assert extra["reason"] == "network_unreachable"


def test_service_heartbeat_level_and_uptime():
    loki = _make_loki()
    events.service_heartbeat(loki, uptime_h=2.5)
    level, msg, extra = _pushed(loki)
    assert level == "WARN"
    assert extra["event"] == config.LOG_EVENT_HEARTBEAT
    assert extra["uptime_h"] == 2.5


def test_partition_missing_includes_path():
    loki = _make_loki()
    events.partition_missing(loki, path="/opt/towerwatch/data")
    level, msg, extra = _pushed(loki)
    assert level == "WARN"
    assert extra["event"] == config.LOG_EVENT_PARTITION_MISSING
    assert extra["path"] == "/opt/towerwatch/data"


def test_metrics_push_failed_with_http_status():
    loki = _make_loki()
    events.metrics_push_failed(loki, http_status=429)
    level, msg, extra = _pushed(loki)
    assert level == "WARN"
    assert extra["event"] == config.LOG_EVENT_METRICS_PUSH_FAIL
    assert extra["http_status"] == 429


def test_annotation_failed_with_error():
    loki = _make_loki()
    events.annotation_failed(loki, error="timeout")
    level, msg, extra = _pushed(loki)
    assert level == "WARN"
    assert extra["event"] == config.LOG_EVENT_ANNOTATION_FAILED
    assert extra["error"] == "timeout"


def test_log_buffer_flushed_count():
    loki = _make_loki()
    events.log_buffer_flushed(loki, count=7)
    level, msg, extra = _pushed(loki)
    assert level == "WARN"
    assert extra["event"] == config.LOG_EVENT_LOG_BUFFER_FLUSHED
    assert extra["count"] == 7
