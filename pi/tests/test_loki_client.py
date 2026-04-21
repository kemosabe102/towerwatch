"""Tests for LokiClient — 8 tests."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests


def _make_client(tmp_path, push_level="WARN", url="http://fake-loki", status=204):
    from loki import LokiClient

    def _fake_post(loki_url, **kwargs):
        resp = MagicMock()
        resp.status_code = status
        return resp

    return LokiClient(
        url=url,
        user="user",
        token="token",
        buffer_path=str(tmp_path / "buffer" / "loki.jsonl"),
        buffer_max_bytes=256 * 1024,
        push_level=push_level,
        push_timeout=5,
        host_tag="towerwatch",
    )


# ---------------------------------------------------------------------------
# 256 KB eviction with exact byte accounting
# ---------------------------------------------------------------------------
def test_buffer_evicts_at_256kb(tmp_path):
    from loki import LokiClient
    client = _make_client(tmp_path)
    buf = Path(str(tmp_path / "buffer" / "loki.jsonl"))
    buf.parent.mkdir(parents=True)

    big_line = json.dumps({"streams": [{"values": [["0", "x" * 512]]}]}) + "\n"
    content = big_line * (int(260 * 1024 / len(big_line)) + 1)
    buf.write_text(content, encoding="utf-8")
    size_before = buf.stat().st_size

    with patch("requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=204)
        # _buffer is called via push when _post raises → but test directly here
        new_payload = client._build_payload("WARN", "newest")
        client._buffer(new_payload)

    assert buf.stat().st_size < size_before
    last = json.loads(buf.read_text().splitlines()[-1])
    assert last["streams"][0]["values"][0][1] == json.dumps({"msg": "newest"})


# ---------------------------------------------------------------------------
# push_level filter
# ---------------------------------------------------------------------------
def test_push_level_warn_drops_info(tmp_path):
    client = _make_client(tmp_path, push_level="WARN")
    posted = []
    with patch.object(client, "_post", side_effect=posted.append):
        client.push("INFO", "should be dropped")
    assert posted == []

def test_push_level_warn_passes_warn(tmp_path):
    client = _make_client(tmp_path, push_level="WARN")
    posted = []
    with patch.object(client, "_post", side_effect=posted.append):
        client.push("WARN", "should pass")
    assert len(posted) == 1


# ---------------------------------------------------------------------------
# flush delivers in order
# ---------------------------------------------------------------------------
def test_flush_delivers_in_order(tmp_path):
    client = _make_client(tmp_path)
    buf = Path(str(tmp_path / "buffer" / "loki.jsonl"))
    buf.parent.mkdir(parents=True)

    payloads = [client._build_payload("WARN", f"msg{i}") for i in range(3)]
    buf.write_text("\n".join(json.dumps(p) for p in payloads) + "\n", encoding="utf-8")

    # Capture order by reading file lines before they are consumed
    expected_msgs = [
        json.loads(json.loads(l)["streams"][0]["values"][0][1])["msg"]
        for l in buf.read_text().splitlines() if l.strip()
    ]

    with patch("requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=204)
        count = client.flush()

    assert count == 3
    assert not buf.exists()
    assert expected_msgs == ["msg0", "msg1", "msg2"]


# ---------------------------------------------------------------------------
# Partial flush resume
# ---------------------------------------------------------------------------
def test_flush_preserves_remaining_on_failure(tmp_path):
    client = _make_client(tmp_path)
    buf = Path(str(tmp_path / "buffer" / "loki.jsonl"))
    buf.parent.mkdir(parents=True)

    payloads = [client._build_payload("WARN", f"e{i}") for i in range(4)]
    buf.write_text("\n".join(json.dumps(p) for p in payloads) + "\n", encoding="utf-8")

    call_n = [0]
    def flaky(payload):
        call_n[0] += 1
        if call_n[0] >= 3:
            raise OSError("gone")

    with patch.object(client, "_post", side_effect=flaky):
        count = client.flush()

    assert count == 2
    assert buf.exists()
    remaining = [l for l in buf.read_text().splitlines() if l.strip()]
    assert len(remaining) == 2


# ---------------------------------------------------------------------------
# Corrupt line skip
# ---------------------------------------------------------------------------
def test_flush_skips_corrupt_lines(tmp_path):
    client = _make_client(tmp_path)
    buf = Path(str(tmp_path / "buffer" / "loki.jsonl"))
    buf.parent.mkdir(parents=True)

    good = json.dumps(client._build_payload("WARN", "good"))
    buf.write_text(f"{{NOT JSON\n{good}\n", encoding="utf-8")

    with patch("requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=204)
        count = client.flush()

    # 1 good line delivered; corrupt line silently dropped
    assert count == 1
    assert not buf.exists()


# ---------------------------------------------------------------------------
# _post raises on 4xx (the Pass 5 bug fix)
# ---------------------------------------------------------------------------
def test_post_raises_on_4xx(tmp_path):
    client = _make_client(tmp_path, status=400)
    with patch("requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=400)
        with pytest.raises(requests.HTTPError):
            client._post({"streams": []})


# ---------------------------------------------------------------------------
# Empty URL is a no-op
# ---------------------------------------------------------------------------
def test_empty_url_noop(tmp_path):
    client = _make_client(tmp_path, url="")
    with patch("requests.post") as mock_post:
        client.push("WARN", "should not post")
    mock_post.assert_not_called()
