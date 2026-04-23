"""
Regression tests for log-buffer flush behaviour.

No monkeypatch — all seams are injected directly into LokiClient
and wait_for_data_partition.
"""
import json
import sys
from pathlib import Path

_PI = Path(__file__).resolve().parents[1]
if str(_PI) not in sys.path:
    sys.path.insert(0, str(_PI))

from tests.fakes import FakeClock, FakeCompletedProcess, FakeEvents, FakeLoki, FakeSubprocess


def _make_payload(msg: str) -> dict:
    return {
        "streams": [{
            "stream": {"job": "towerwatch", "host": "towerwatch", "level": "warn"},
            "values": [["1700000000000000000", json.dumps({"msg": msg})]],
        }]
    }


def _make_client(buf_path):
    from towerwatch.clients.loki import LokiClient
    return LokiClient(
        url="http://fake", user="u", token="t",
        buffer_path=str(buf_path),
        buffer_max_bytes=256 * 1024,
    )


def _seed_buffer(buf_path: Path, payloads):
    buf_path.parent.mkdir(parents=True, exist_ok=True)
    buf_path.write_text(
        "\n".join(json.dumps(p) for p in payloads) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Happy path: 3 buffered lines all delivered, buffer removed
# ---------------------------------------------------------------------------
def test_flush_delivers_all_lines_and_removes_buffer(tmp_path):
    buf_path = tmp_path / "buffer" / "loki.jsonl"
    payloads = [_make_payload(f"entry {i}") for i in range(3)]
    _seed_buffer(buf_path, payloads)

    client = _make_client(buf_path)

    posted = []

    # Inject a replacement _post by subclassing rather than monkeypatching
    class TestClient(type(client)):
        def _post(self, payload):
            posted.append(payload)

    test_client = TestClient(
        url="http://fake", user="u", token="t",
        buffer_path=str(buf_path), buffer_max_bytes=256 * 1024,
    )
    test_client.flush()

    assert len(posted) >= 3
    msgs = [json.loads(p["streams"][0]["values"][0][1]).get("msg", "") for p in posted[:3]]
    assert msgs == ["entry 0", "entry 1", "entry 2"]
    assert not buf_path.exists()


# ---------------------------------------------------------------------------
# Partial flush: network fails mid-way → remaining preserved
# ---------------------------------------------------------------------------
def test_flush_preserves_remaining_on_network_failure(tmp_path):
    buf_path = tmp_path / "buffer" / "loki.jsonl"
    payloads = [_make_payload(f"entry {i}") for i in range(3)]
    _seed_buffer(buf_path, payloads)

    # Subclass with a flaky _post
    from towerwatch.clients.loki import LokiClient

    class FlakyClient(LokiClient):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.call_count = 0

        def _post(self, payload):
            self.call_count += 1
            if self.call_count >= 2:
                raise OSError("network gone")

    client = FlakyClient(
        url="http://fake", user="u", token="t",
        buffer_path=str(buf_path), buffer_max_bytes=256 * 1024,
    )
    client.flush()

    assert client.call_count == 2
    assert buf_path.exists()
    remaining = [l for l in buf_path.read_text().splitlines() if l.strip()]
    assert len(remaining) == 2


# ---------------------------------------------------------------------------
# wait_for_data_partition timeout branch — no NameError
# ---------------------------------------------------------------------------
def test_wait_for_data_partition_no_nameerror(tmp_path):
    from towerwatch.startup import wait_for_data_partition
    missing = tmp_path / "nonexistent_data"
    # First clock.time() call = deadline = 0 + 1 = 1
    # Second clock.time() call inside loop = 31 → loop exits
    wait_for_data_partition(
        missing, timeout_s=1,
        is_windows=False,
        clock=FakeClock(wall=[0.0, 31.0]),
        subprocess_run=FakeSubprocess(),
        loki=FakeLoki(),
        events=FakeEvents(),
    )
    assert missing.is_dir()
