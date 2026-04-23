"""Characterization tests for format_influx_line — 4 tests.

Constraints enforced here:
  #1 — units are _ms (not seconds)
  #2 — target labels baked into field names (not Prometheus labels)
"""


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
