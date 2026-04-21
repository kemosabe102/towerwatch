"""
Regression test for the silent Loki-flush NameError (Pass 0 hotfix).

Before the fix, _flush_log_buffer() called _post_loki / _buffer_log_entry /
_build_loki_payload which were not imported into towerwatch.py — the resulting
NameError was swallowed by `except Exception: break`, so the buffer never drained.
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure pi/ is importable
_PI = Path(__file__).resolve().parents[1]
if str(_PI) not in sys.path:
    sys.path.insert(0, str(_PI))


def _make_payload(msg: str) -> dict:
    return {
        "streams": [{
            "stream": {"job": "towerwatch", "host": "towerwatch", "level": "warn"},
            "values": [["1700000000000000000", json.dumps({"msg": msg})]],
        }]
    }


# ---------------------------------------------------------------------------
# Happy path: 3 buffered lines, all delivered, buffer file removed
# ---------------------------------------------------------------------------
def test_flush_delivers_all_lines_and_removes_buffer(tmp_marker_dir, monkeypatch):
    import config
    import towerwatch

    # Wire config to use tmp_marker_dir's buffer
    buf_path = tmp_marker_dir.buffer_dir / "loki.jsonl"
    monkeypatch.setattr(config, "LOKI_BUFFER_FILE", str(buf_path))

    # Seed buffer with 3 valid JSON payloads
    payloads = [_make_payload(f"entry {i}") for i in range(3)]
    tmp_marker_dir.seed_loki_buffer(payloads)
    assert buf_path.exists()

    import loki as loki_mod

    posted = []

    def fake_post(payload):
        posted.append(payload)

    # Pass 5: flush now lives in LokiClient; build a client pointed at tmp buffer
    client = loki_mod.LokiClient(
        url="http://fake", user="u", token="t",
        buffer_path=str(buf_path),
        buffer_max_bytes=256 * 1024,
    )
    monkeypatch.setattr(client, "_post", fake_post)
    client.flush()

    # flush() delivers the 3 buffered entries then self.push() sends a
    # "Log buffer flushed" confirmation — so >= 3 posts total.
    assert len(posted) >= 3, f"Expected at least 3 posts, got {len(posted)}"
    msgs = [json.loads(p["streams"][0]["values"][0][1]).get("msg", "") for p in posted[:3]]
    assert msgs == ["entry 0", "entry 1", "entry 2"]
    assert not buf_path.exists(), "Buffer file should be removed after full flush"


# ---------------------------------------------------------------------------
# Partial flush: network fails on 2nd entry — first delivered, rest preserved
# ---------------------------------------------------------------------------
def test_flush_preserves_remaining_on_network_failure(tmp_marker_dir, monkeypatch):
    import config
    import towerwatch

    buf_path = tmp_marker_dir.buffer_dir / "loki.jsonl"
    monkeypatch.setattr(config, "LOKI_BUFFER_FILE", str(buf_path))

    payloads = [_make_payload(f"entry {i}") for i in range(3)]
    tmp_marker_dir.seed_loki_buffer(payloads)

    import loki as loki_mod

    call_count = [0]

    def flaky_post(payload):
        call_count[0] += 1
        if call_count[0] >= 2:
            raise OSError("network gone")

    client = loki_mod.LokiClient(
        url="http://fake", user="u", token="t",
        buffer_path=str(buf_path),
        buffer_max_bytes=256 * 1024,
    )
    monkeypatch.setattr(client, "_post", flaky_post)
    client.flush()

    assert call_count[0] == 2
    assert buf_path.exists()
    remaining = [l for l in buf_path.read_text().splitlines() if l.strip()]
    assert len(remaining) == 2


# ---------------------------------------------------------------------------
# No NameError from wait_for_data_partition's partition-missing branch
# (Windows skips the mountpoint check, so we test the buffer call on Linux path
# by patching IS_WINDOWS=False and making the data dir appear missing)
# ---------------------------------------------------------------------------
def test_wait_for_data_partition_no_nameerror(tmp_path, monkeypatch):
    import config
    import towerwatch

    # Use a path that doesn't exist so it hits the "not detected" branch
    missing = tmp_path / "nonexistent_data"
    monkeypatch.setattr(config, "DATA_DIR", str(missing))
    monkeypatch.setattr(config, "LOKI_BUFFER_FILE", str(tmp_path / "buffer" / "loki.jsonl"))
    monkeypatch.setattr(towerwatch, "IS_WINDOWS", False)

    # Patch subprocess so mountpoint check fails quickly
    import subprocess
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: type("R", (), {"returncode": 1})(),
    )

    # Patch time so the 30s timeout expires immediately
    import time as time_mod
    _start = [0.0]

    def fake_time():
        v = _start[0]
        _start[0] += 31.0  # instant timeout
        return v

    monkeypatch.setattr(time_mod, "time", fake_time)
    monkeypatch.setattr(time_mod, "sleep", lambda s: None)

    import startup
    # Should not raise NameError
    startup.wait_for_data_partition(missing, timeout_s=1)
