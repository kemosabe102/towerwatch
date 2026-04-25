"""Characterization tests for format_influx_line — 4 tests.

Constraints enforced here:
  #1 — units are _ms (not seconds)
  #2 — target labels baked into field names (not Prometheus labels)

LOCATION pinning: INFLUX_HOST_TAG is sourced from credentials.LOCATION, which
varies by deployment (e.g. "towerwatch" at home, "standstill" remote). These
tests assert on the exact line string, so the autouse fixture below pins it to
"towerwatch" for this module — otherwise CI breaks whenever you swap to a
different per-site credentials file before deploying.
"""

import pytest


@pytest.fixture(autouse=True)
def _pin_host_tag(monkeypatch):
    from towerwatch import config as _config

    monkeypatch.setattr(_config, "INFLUX_HOST_TAG", "towerwatch")


def _fmt(fields, ts=1700000000):
    from towerwatch.tick import format_influx_line

    return format_influx_line(fields, ts)


def test_influx_measurement_and_host_tag():
    line = _fmt({"rtt_avg_google": 12})
    assert line.startswith("towerwatch,host=towerwatch ")


def test_influx_field_ordering_stable():
    fields = {"rtt_avg_google": 12, "rtt_avg_cloudflare": 11, "connected": 1}
    line = _fmt(fields)
    # All fields present
    assert "rtt_avg_google=12" in line
    assert "rtt_avg_cloudflare=11" in line
    assert "connected=1" in line


def test_influx_ms_units_in_field_names():
    """Constraint #1: latency fields must carry _ms suffix, not be in seconds."""
    fields = {
        "rtt_avg_google": 12,
        "tcp_connect_ms": 15,
        "dns_resolve_ms_8_8_8_8": 25,
        "http_latency_ms": 80,
    }
    line = _fmt(fields)
    assert "tcp_connect_ms=15" in line
    assert "dns_resolve_ms_8_8_8_8=25" in line
    assert "http_latency_ms=80" in line
    # Must NOT contain bare seconds-style names
    assert "tcp_connect_s=" not in line


def test_influx_target_labels_baked_in_field_names():
    """Constraint #2: labels are in field names, not separate Prometheus labels."""
    fields = {"rtt_avg_google": 12, "rtt_avg_cloudflare": 11, "rtt_avg_gateway": 5}
    line = _fmt(fields)
    # Field names contain the target label
    assert "rtt_avg_google=12" in line
    assert "rtt_avg_cloudflare=11" in line
    assert "rtt_avg_gateway=5" in line
    # No separate label= key-value pairs (would look like target="google")
    assert 'target="google"' not in line
    assert "target=google" not in line


def test_influx_timestamp_seconds_precision():
    line = _fmt({"connected": 1}, ts=1700000000)
    assert line.endswith(" 1700000000")


def test_influx_none_values_dropped():
    """None-valued fields must be omitted — they'd crash Influx."""
    line = _fmt({"rtt_avg_google": 12, "http_latency_ms": None, "connected": 1})
    assert "http_latency_ms" not in line
    assert "rtt_avg_google=12" in line
    assert "connected=1" in line


def test_influx_small_float_no_scientific_notation():
    """Tiny floats must not produce '1e-12' which Influx rejects."""
    line = _fmt({"jitter_google": 0.001})
    # Python's default float repr for 0.001 is '0.001' — not scientific
    assert "jitter_google=0.001" in line
    assert "e-" not in line and "E-" not in line


def test_influx_string_field_value_pinned():
    """String values are currently serialised as-is (no quoting).
    This test pins the current behaviour so a future change is visible.
    NOTE: unquoted strings are technically invalid Influx line protocol.
    Fix is tracked as a follow-up; do not silently change without updating this test."""
    line = _fmt({"version": "abc1234"})
    # Current behaviour: value appears unquoted
    assert "version=abc1234" in line


def test_build_info_line_shape():
    """Prom build_info gauge line: version and build_date are TAGS, build_info is the field."""
    from towerwatch.tick import format_build_info_line

    line = format_build_info_line(
        ts=1700000000, version="abc1234", build_date="2026-04-23T16:30:25-07:00"
    )
    # Starts with measurement + host tag
    assert line.startswith("towerwatch,host=towerwatch,")
    # version and build_date are tags (before the first space)
    tag_section = line.split(" ", 1)[0]
    assert "version=abc1234" in tag_section
    assert "build_date=2026-04-23T16:30:25-07:00" in tag_section
    # build_info=1 is the only field (after the first space, before the timestamp)
    field_section = line.split(" ")[1]
    assert field_section == "build_info=1"
    # Timestamp last
    assert line.endswith(" 1700000000")


def test_build_info_line_uses_config_defaults():
    """When version/build_date are omitted, falls back to config.BUILD_VERSION/BUILD_DATE."""
    from towerwatch import config
    from towerwatch.tick import format_build_info_line

    line = format_build_info_line(ts=1700000000)
    assert f"version={config.BUILD_VERSION}" in line
    assert f"build_date={config.BUILD_DATE}" in line


def test_speedtest_line_shape():
    """Manual speedtest line: host + triggered_by are tags; dl/ul Mbps are fields."""
    from towerwatch.tick import format_speedtest_line

    line = format_speedtest_line(
        ts=1700000000,
        download_mbps=123.45,
        upload_mbps=45.67,
        triggered_by="alice",
    )
    assert line.startswith("towerwatch,host=towerwatch,")
    tag_section = line.split(" ", 1)[0]
    assert "triggered_by=alice" in tag_section
    field_section = line.split(" ")[1]
    assert "speedtest_download_mbps=123.45" in field_section
    assert "speedtest_upload_mbps=45.67" in field_section
    assert line.endswith(" 1700000000")
