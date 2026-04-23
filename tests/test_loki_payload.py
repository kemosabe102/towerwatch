"""Characterization tests for loki.py payload shape and constraints — no patch.

Constraints enforced:
  #3 — buffer capped at 256 KB
  #4 — WARN level filter (INFO dropped)
"""
import json
import sys
from pathlib import Path

_PI = Path(__file__).resolve().parents[1]
if str(_PI) not in sys.path:
    sys.path.insert(0, str(_PI))


class RecordingPost:
    def __init__(self, status=204):
        self._status = status
        self.calls = []

    def __call__(self, url, **kwargs):
        from tests.fakes import FakeResponse
        self.calls.append((url, kwargs))
        return FakeResponse(status_code=self._status)


def _client(tmp_path, *, push_level="WARN", url="http://fake", post_fn=None):
    from towerwatch.clients.loki import LokiClient
    return LokiClient(
        url=url, user="u", token="t",
        buffer_path=str(tmp_path / "buffer" / "loki.jsonl"),
        buffer_max_bytes=256 * 1024,
        push_level=push_level,
        post_fn=post_fn if post_fn is not None else RecordingPost(),
    )


def test_loki_payload_stream_labels(tmp_path):
    client = _client(tmp_path)
    payload = client._build_payload("WARN", "test message")
    stream = payload["streams"][0]["stream"]
    assert stream["job"] == "towerwatch"
    assert stream["level"] == "warn"
    assert "host" in stream


def test_loki_payload_value_is_json_with_msg(tmp_path):
    client = _client(tmp_path)
    payload = client._build_payload("WARN", "hello", {"event": "test_event"})
    val = json.loads(payload["streams"][0]["values"][0][1])
    assert val["msg"] == "hello"
    assert val["event"] == "test_event"


def test_loki_warn_level_filter_passes(tmp_path):
    """Constraint #4: WARN messages are pushed (LOKI_PUSH_LEVEL=WARN)."""
    post = RecordingPost(status=204)
    client = _client(tmp_path, push_level="WARN", post_fn=post)
    client.push("WARN", "should pass")
    assert len(post.calls) == 1


def test_loki_info_level_filter_dropped(tmp_path):
    """Constraint #4: INFO messages are dropped when LOKI_PUSH_LEVEL=WARN."""
    post = RecordingPost(status=204)
    client = _client(tmp_path, push_level="WARN", post_fn=post)
    client.push("INFO", "should be dropped")
    assert len(post.calls) == 0


def test_loki_buffer_256kb_eviction(tmp_path):
    """Constraint #3: buffer evicts oldest entries when size >= 256 KB."""
    buf = tmp_path / "buffer" / "loki.jsonl"
    buf.parent.mkdir(parents=True)

    client = _client(tmp_path)

    big_payload = {"streams": [{"stream": {}, "values": [["0", "x" * 512]]}]}
    line = json.dumps(big_payload) + "\n"
    content = line * (int(260 * 1024 / len(line)) + 1)
    buf.write_text(content, encoding="utf-8")
    lines_before = len(buf.read_text().splitlines())

    new_payload = client._build_payload("WARN", "newest entry")
    client._buffer(new_payload)

    lines_after = buf.read_text(encoding="utf-8").splitlines()
    assert len(lines_after) < lines_before

    last = json.loads(lines_after[-1])
    assert last["streams"][0]["values"][0][1] == json.dumps({"msg": "newest entry"})
