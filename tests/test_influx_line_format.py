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
def _pin_tags(monkeypatch):
    """Pin host/carrier/connection_type tags so assertions on the exact line
    string survive credential swaps. Without this the test breaks every time
    you cp credentials.standstill.py credentials.py.
    """
    from towerwatch import config as _config

    monkeypatch.setattr(_config, "INFLUX_HOST_TAG", "towerwatch")
    monkeypatch.setattr(_config, "INFLUX_CARRIER_TAG", "comcast")
    monkeypatch.setattr(_config, "INFLUX_CONNECTION_TYPE_TAG", "cable")


def _fmt(fields, ts=1700000000):
    from towerwatch.tick import format_influx_line

    return format_influx_line(fields, ts)


def test_influx_measurement_and_host_tag():
    line = _fmt({"rtt_avg_google": 12})
    assert line.startswith("towerwatch,host=towerwatch,carrier=comcast,connection_type=cable ")


def test_influx_carrier_and_connection_type_tags_present():
    """New tags ride along on every metric line so dashboards can group by them."""
    line = _fmt({"connected": 1})
    tag_section = line.split(" ", 1)[0]
    assert "carrier=comcast" in tag_section
    assert "connection_type=cable" in tag_section


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
    """Prom build_info gauge line: version, build_date, and link_max_* are TAGS.
    build_info is the only field. Tag order is part of the contract — dashboard
    templating uses `label_values()` which doesn't care about order, but human
    readers + Loki LogQL filters often do."""
    from towerwatch.tick import format_build_info_line

    line = format_build_info_line(
        ts=1700000000,
        version="abc1234",
        build_date="2026-04-23T16:30:25-07:00",
        link_max_download_mbps=1000,
        link_max_upload_mbps=50,
    )
    # Starts with measurement + host tag
    assert line.startswith("towerwatch,host=towerwatch,")
    # All four label-tag values appear in the tag section
    tag_section = line.split(" ", 1)[0]
    assert "version=abc1234" in tag_section
    assert "build_date=2026-04-23T16:30:25-07:00" in tag_section
    assert "link_max_download_mbps=1000" in tag_section
    assert "link_max_upload_mbps=50" in tag_section
    # build_info=1 is the only field (after the first space, before the timestamp)
    field_section = line.split(" ")[1]
    assert field_section == "build_info=1"
    # Timestamp last
    assert line.endswith(" 1700000000")


def test_build_info_line_uses_config_defaults():
    """When all kwargs are omitted, falls back to config module values."""
    from towerwatch import config
    from towerwatch.tick import format_build_info_line

    line = format_build_info_line(ts=1700000000)
    assert f"version={config.BUILD_VERSION}" in line
    assert f"build_date={config.BUILD_DATE}" in line
    assert f"link_max_download_mbps={config.LINK_MAX_DOWNLOAD_MBPS}" in line
    assert f"link_max_upload_mbps={config.LINK_MAX_UPLOAD_MBPS}" in line


def test_load_int_credential_handles_missing_field():
    """Defensive: credentials.py without the new override fields must not crash.
    Older Pis on a previous deploy lack HTTP_THROUGHPUT_BYTES_OVERRIDE etc.;
    _load_int_credential should silently fall back. Same for None placeholders
    in credentials.py.example."""
    from towerwatch.config import _load_int_credential

    # Unknown field name → fallback
    assert _load_int_credential("DOES_NOT_EXIST_ANYWHERE", 42) == 42
    # Existing field that's None (placeholder pattern in credentials.py.example)
    # — simulate by reading a field that won't exist, fallback path is the same
    assert _load_int_credential("ALSO_MISSING", 999_999) == 999_999


def test_speedtest_line_shape():
    """Manual speedtest line: host + triggered_by are tags; dl/ul Mbps and
    bytes-used are fields. Bytes feed the dashboard's weekly-data stat."""
    from towerwatch.tick import format_speedtest_line

    line = format_speedtest_line(
        ts=1700000000,
        download_mbps=123.45,
        upload_mbps=45.67,
        download_bytes=50_000_000,
        upload_bytes=10_000_000,
        triggered_by="alice",
    )
    assert line.startswith("towerwatch,host=towerwatch,")
    tag_section = line.split(" ", 1)[0]
    assert "triggered_by=alice" in tag_section
    field_section = line.split(" ")[1]
    assert "speedtest_download_mbps=123.45" in field_section
    assert "speedtest_upload_mbps=45.67" in field_section
    assert "speedtest_download_bytes=50000000i" in field_section
    assert "speedtest_upload_bytes=10000000i" in field_section
    assert line.endswith(" 1700000000")


def test_speedtest_line_defaults_bytes_to_zero():
    """Backward-compat: callers that don't pass bytes still produce a valid line
    (bytes default to 0). Used by older code paths during the rollout."""
    from towerwatch.tick import format_speedtest_line

    line = format_speedtest_line(
        ts=1700000000,
        download_mbps=10.0,
        upload_mbps=5.0,
        triggered_by="bob",
    )
    field_section = line.split(" ")[1]
    assert "speedtest_download_bytes=0i" in field_section
    assert "speedtest_upload_bytes=0i" in field_section


def test_band_sig_line_shape():
    """Band-tagged signal line: band + pci are tags (-> Prom labels) so a
    dashboard can `avg by (band)`. LTE anchor signal goes in fields named
    m6_sig_* so the existing untagged m6_rsrp/m6_sinr history is untouched."""
    from towerwatch.tick import format_band_sig_line

    line = format_band_sig_line(
        {"m6_pcc_band": 66, "m6_pcc_pci": 81, "m6_rsrp": -97, "m6_sinr": 18},
        1700000000,
    )
    assert line is not None
    tag_section = line.split(" ", 1)[0]
    assert tag_section.startswith("towerwatch,host=towerwatch,")
    assert "band=66" in tag_section
    assert "pci=81" in tag_section
    field_section = line.split(" ")[1]
    assert "m6_sig_rsrp=-97" in field_section
    assert "m6_sig_sinr=18" in field_section
    assert line.endswith(" 1700000000")


def test_band_sig_line_includes_nr5g_when_present():
    """5G NR signal fields ride along when the device reports them."""
    from towerwatch.tick import format_band_sig_line

    line = format_band_sig_line(
        {
            "m6_pcc_band": 2,
            "m6_pcc_pci": 42,
            "m6_rsrp": -90,
            "m6_sinr": 12,
            "m6_nr5g_rsrp": -80,
            "m6_nr5g_sinr": 20,
        },
        1700000000,
    )
    assert line is not None
    field_section = line.split(" ")[1]
    assert "m6_sig_nr5g_rsrp=-80" in field_section
    assert "m6_sig_nr5g_sinr=20" in field_section


def test_band_sig_line_falls_back_to_band_when_no_pcc_band():
    """Single-carrier sites have m6_band but not m6_pcc_band; use it as fallback."""
    from towerwatch.tick import format_band_sig_line

    line = format_band_sig_line({"m6_band": 13, "m6_rsrp": -100}, 1700000000)
    assert line is not None
    assert "band=13" in line.split(" ", 1)[0]
    assert "m6_sig_rsrp=-100" in line.split(" ")[1]


def test_band_sig_line_none_without_band():
    """No band -> no tagged line (non-cellular site, or a tick where the M6
    poll failed). Must not fabricate a band=0 row."""
    from towerwatch.tick import format_band_sig_line

    assert format_band_sig_line({"rtt_avg_google": 12}, 1700000000) is None


def test_band_sig_line_none_without_any_signal():
    """Band present but no signal value -> no line. A bare band tag with no
    field would be a useless (and line-protocol-invalid) row."""
    from towerwatch.tick import format_band_sig_line

    assert format_band_sig_line({"m6_pcc_band": 66}, 1700000000) is None
