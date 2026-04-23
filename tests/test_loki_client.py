"""Tests for LokiClient — no patch, post_fn injected."""
import json
import sys
from pathlib import Path

import pytest
import requests

_PI = Path(__file__).resolve().parents[1]
if str(_PI) not in sys.path:
    sys.path.insert(0, str(_PI))

from tests.fakes import FakeResponse


class RecordingPost:
    """Callable stand-in for requests.post."""

    def __init__(self, status=204, body=None, raises=None):
        self._status = status
        self._body = body
        self._raises = raises
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self._raises:
            raise self._raises
        return FakeResponse(status_code=self._status, text=self._body or "")


def _make_client(tmp_path, *, push_level="WARN", url="http://fake-loki",
                 post_fn=None):
    from towerwatch.clients.loki import LokiClient
    return LokiClient(
        url=url,
        user="user",
        token="token",
        buffer_path=str(tmp_path / "buffer" / "loki.jsonl"),
        buffer_max_bytes=256 * 1024,
        push_level=push_level,
        push_timeout=5,
        host_tag="towerwatch",
        post_fn=post_fn if post_fn is not None else RecordingPost(status=204),
    )


# ---------------------------------------------------------------------------
# 256 KB eviction
# ---------------------------------------------------------------------------
def test_buffer_evicts_at_256kb(tmp_path):
    client = _make_client(tmp_path)
    buf = Path(str(tmp_path / "buffer" / "loki.jsonl"))
    buf.parent.mkdir(parents=True)

    big_line = json.dumps({"streams": [{"values": [["0", "x" * 512]]}]}) + "\n"
    content = big_line * (int(260 * 1024 / len(big_line)) + 1)
    buf.write_text(content, encoding="utf-8")
    size_before = buf.stat().st_size

    new_payload = client._build_payload("WARN", "newest")
    client._buffer(new_payload)

    assert buf.stat().st_size < size_before
    last = json.loads(buf.read_text().splitlines()[-1])
    assert last["streams"][0]["values"][0][1] == json.dumps({"msg": "newest"})


# ---------------------------------------------------------------------------
# push_level filter
# ---------------------------------------------------------------------------
def test_push_level_warn_drops_info(tmp_path):
    post = RecordingPost(status=204)
    client = _make_client(tmp_path, push_level="WARN", post_fn=post)
    client.push("INFO", "should be dropped")
    assert post.calls == []


def test_push_level_warn_passes_warn(tmp_path):
    post = RecordingPost(status=204)
    client = _make_client(tmp_path, push_level="WARN", post_fn=post)
    client.push("WARN", "should pass")
    assert len(post.calls) == 1


# ---------------------------------------------------------------------------
# flush order
# ---------------------------------------------------------------------------
def test_flush_delivers_in_order(tmp_path):
    post = RecordingPost(status=204)
    client = _make_client(tmp_path, post_fn=post)
    buf = Path(str(tmp_path / "buffer" / "loki.jsonl"))
    buf.parent.mkdir(parents=True)

    payloads = [client._build_payload("WARN", f"msg{i}") for i in range(3)]
    buf.write_text("\n".join(json.dumps(p) for p in payloads) + "\n",
                   encoding="utf-8")

    count = client.flush()
    assert count == 3
    assert not buf.exists()


# ---------------------------------------------------------------------------
# Partial flush: _post starts raising mid-way
# ---------------------------------------------------------------------------
def test_flush_preserves_remaining_on_failure(tmp_path):
    from towerwatch.clients.loki import LokiClient

    class FlakyClient(LokiClient):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.call_n = 0

        def _post(self, payload):
            self.call_n += 1
            if self.call_n >= 3:
                raise OSError("gone")

    client = FlakyClient(
        url="http://fake-loki", user="u", token="t",
        buffer_path=str(tmp_path / "buffer" / "loki.jsonl"),
        buffer_max_bytes=256 * 1024,
        push_level="WARN", push_timeout=5, host_tag="towerwatch",
    )
    buf = Path(str(tmp_path / "buffer" / "loki.jsonl"))
    buf.parent.mkdir(parents=True)
    payloads = [client._build_payload("WARN", f"e{i}") for i in range(4)]
    buf.write_text("\n".join(json.dumps(p) for p in payloads) + "\n",
                   encoding="utf-8")

    count = client.flush()
    assert count == 2
    assert buf.exists()
    remaining = [l for l in buf.read_text().splitlines() if l.strip()]
    assert len(remaining) == 2


# ---------------------------------------------------------------------------
# Corrupt line is skipped
# ---------------------------------------------------------------------------
def test_flush_skips_corrupt_lines(tmp_path):
    post = RecordingPost(status=204)
    client = _make_client(tmp_path, post_fn=post)
    buf = Path(str(tmp_path / "buffer" / "loki.jsonl"))
    buf.parent.mkdir(parents=True)

    good = json.dumps(client._build_payload("WARN", "good"))
    buf.write_text(f"{{NOT JSON\n{good}\n", encoding="utf-8")

    count = client.flush()
    assert count == 1
    assert not buf.exists()


# ---------------------------------------------------------------------------
# _post raises on 4xx
# ---------------------------------------------------------------------------
def test_post_raises_on_4xx(tmp_path):
    post = RecordingPost(status=400)
    client = _make_client(tmp_path, post_fn=post)
    with pytest.raises(requests.HTTPError):
        client._post({"streams": []})


# ---------------------------------------------------------------------------
# Empty URL is a no-op
# ---------------------------------------------------------------------------
def test_empty_url_noop(tmp_path):
    post = RecordingPost(status=204)
    client = _make_client(tmp_path, url="", post_fn=post)
    client.push("WARN", "should not post")
    assert post.calls == []


# ---------------------------------------------------------------------------
# Unknown level is dropped
# ---------------------------------------------------------------------------
def test_unknown_level_is_dropped(tmp_path):
    post = RecordingPost(status=204)
    client = _make_client(tmp_path, push_level="WARN", post_fn=post)
    client.push("TRACE", "unknown level should be dropped")
    assert post.calls == []
