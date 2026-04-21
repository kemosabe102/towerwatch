"""Characterization tests for loki.py payload shape and constraints — 5 tests.

Constraints enforced:
  #3 — buffer capped at 256 KB
  #4 — WARN level filter (INFO dropped)
"""
import json
from unittest.mock import patch

import pytest


def test_loki_payload_stream_labels():
    import loki
    payload = loki._build_loki_payload("WARN", "test message")
    stream = payload["streams"][0]["stream"]
    assert stream["job"] == "towerwatch"
    assert stream["level"] == "warn"
    assert "host" in stream

def test_loki_payload_value_is_json_with_msg():
    import loki
    payload = loki._build_loki_payload("WARN", "hello", {"event": "test_event"})
    val_str = payload["streams"][0]["values"][0][1]
    val = json.loads(val_str)
    assert val["msg"] == "hello"
    assert val["event"] == "test_event"

def test_loki_payload_timestamp_nanoseconds():
    import loki, time
    with patch("loki.time.time", return_value=1_700_000_000.0):
        payload = loki._build_loki_payload("WARN", "ts test")
    ts_str = payload["streams"][0]["values"][0][0]
    assert ts_str == str(int(1_700_000_000.0 * 1e9))

def test_loki_warn_level_filter_passes():
    """Constraint #4: WARN messages are pushed (LOKI_PUSH_LEVEL=WARN)."""
    import loki, config
    posted = []
    # Force a fresh singleton with known config so push_level is WARN
    client = loki.LokiClient(
        url="http://fake", user="u", token="t",
        buffer_path="/tmp/test_loki.jsonl",
        buffer_max_bytes=256 * 1024,
        push_level="WARN",
    )
    with patch.object(client, "_post", side_effect=posted.append):
        client.push("WARN", "should pass")
    assert len(posted) == 1

def test_loki_info_level_filter_dropped():
    """Constraint #4: INFO messages are dropped when LOKI_PUSH_LEVEL=WARN."""
    import loki, config
    posted = []
    client = loki.LokiClient(
        url="http://fake", user="u", token="t",
        buffer_path="/tmp/test_loki.jsonl",
        buffer_max_bytes=256 * 1024,
        push_level="WARN",
    )
    with patch.object(client, "_post", side_effect=posted.append):
        client.push("INFO", "should be dropped")
    assert len(posted) == 0

def test_loki_buffer_256kb_eviction(tmp_path):
    """Constraint #3: buffer evicts oldest entries when size >= 256 KB."""
    import loki

    buf = tmp_path / "buffer" / "loki.jsonl"
    buf.parent.mkdir(parents=True)

    client = loki.LokiClient(
        url="http://fake", user="u", token="t",
        buffer_path=str(buf),
        buffer_max_bytes=256 * 1024,
    )

    # Fill to just over 256 KB
    big_payload = {"streams": [{"stream": {}, "values": [["0", "x" * 512]]}]}
    line = json.dumps(big_payload) + "\n"
    content = line * (int(260 * 1024 / len(line)) + 1)
    buf.write_text(content, encoding="utf-8")
    size_before = buf.stat().st_size
    assert size_before >= 256 * 1024
    lines_before = len(buf.read_text().splitlines())

    # Buffer a new entry — eviction must drop oldest 90% of lines
    new_payload = client._build_payload("WARN", "newest entry")
    client._buffer(new_payload)

    lines_after = buf.read_text(encoding="utf-8").splitlines()
    # Eviction removed ~90% of lines, so count must be much less than before
    assert len(lines_after) < lines_before

    # Newest entry must be retained as the last line
    last = json.loads(lines_after[-1])
    assert last["streams"][0]["values"][0][1] == json.dumps({"msg": "newest entry"})
