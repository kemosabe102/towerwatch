"""
Shared pytest fixtures for towerwatch unit tests.

All tests are hermetic: no real secrets, no real network, no real filesystem paths.
"""
import json
import sys
import time as _time_module
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Path setup — insert stubs dir so `import secrets` resolves to the stub,
# and insert pi/ so `import config`, `import loki`, etc. work without install.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PI_DIR = _REPO_ROOT / "pi"
_STUBS_DIR = Path(__file__).resolve().parent / "stubs"


def _ensure_paths():
    # stubs first so `secrets` stub shadows any real secrets.py
    for p in [str(_STUBS_DIR), str(_PI_DIR)]:
        if p not in sys.path:
            sys.path.insert(0, p)


_ensure_paths()


# ---------------------------------------------------------------------------
# Autouse: hermetic secrets + config path redirection
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _stub_secrets(monkeypatch, tmp_path):
    """Ensure stub secrets is on path and redirect config data paths to tmp_path."""
    _ensure_paths()

    # Redirect all data/marker/buffer paths in config to tmp_path so no test
    # touches the real ./data/ directory.
    import config  # noqa: PLC0415
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "LOKI_BUFFER_FILE", str(tmp_path / "buffer" / "loki.jsonl"))
    monkeypatch.setattr(config, "LAST_PUSH_MARKER_FILE", str(tmp_path / "last_push_ts"))
    monkeypatch.setattr(config, "LAST_ALIVE_MARKER_FILE", str(tmp_path / "last_alive_ts"))


# ---------------------------------------------------------------------------
# fake_clock
# ---------------------------------------------------------------------------
class FakeClock:
    """Hand-rolled clock that advances only when the test says so."""

    def __init__(self, start: float = 1_700_000_000.0):
        self._wall = start
        self._mono = 0.0
        self._slept = 0.0

    def time(self) -> float:
        return self._wall

    def monotonic(self) -> float:
        return self._mono

    def sleep(self, seconds: float) -> None:
        self._wall += seconds
        self._mono += seconds
        self._slept += seconds

    def advance(self, seconds: float) -> None:
        """Advance both clocks by the given number of seconds."""
        self._wall += seconds
        self._mono += seconds

    @property
    def total_slept(self) -> float:
        return self._slept


@pytest.fixture
def fake_clock():
    return FakeClock()


# ---------------------------------------------------------------------------
# fake_session
# ---------------------------------------------------------------------------
class FakeResponse:
    def __init__(self, status_code: int = 204, body: str = ""):
        self.status_code = status_code
        self.text = body

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(response=self)

    def json(self):
        return json.loads(self.text)


@pytest.fixture
def fake_session():
    """MagicMock requests.Session with a configurable .post response."""
    session = MagicMock()
    session.post.return_value = FakeResponse(204)
    session.get.return_value = FakeResponse(200)
    return session


# ---------------------------------------------------------------------------
# fake_loki_transport
# ---------------------------------------------------------------------------
class FakeLokiTransport:
    """Captures payloads posted to Loki for assertion."""

    def __init__(self, status_code: int = 204):
        self.calls: list[dict] = []
        self.status_code = status_code

    def post(self, payload: dict) -> None:
        """Mimic _post_loki signature. Raises on non-2xx."""
        self.calls.append(payload)
        if self.status_code >= 300:
            raise RuntimeError(f"Loki returned {self.status_code}")

    @property
    def call_count(self) -> int:
        return len(self.calls)


@pytest.fixture
def fake_loki_transport():
    return FakeLokiTransport()


# ---------------------------------------------------------------------------
# tmp_marker_dir
# ---------------------------------------------------------------------------
@pytest.fixture
def tmp_marker_dir(tmp_path):
    """
    Wraps tmp_path, provides helpers to seed marker files.

    Usage:
        tmp_marker_dir.seed_last_push(1_700_000_000.0)
        tmp_marker_dir.seed_last_alive(1_700_000_000.0)
    """
    class _MarkerDir:
        def __init__(self, base: Path):
            self.base = base
            self.last_push = base / "last_push_ts"
            self.last_alive = base / "last_alive_ts"
            self.buffer_dir = base / "buffer"
            self.buffer_dir.mkdir(parents=True, exist_ok=True)

        def seed_last_push(self, ts: float) -> None:
            self.last_push.write_text(str(ts), encoding="utf-8")

        def seed_last_alive(self, ts: float) -> None:
            self.last_alive.write_text(str(ts), encoding="utf-8")

        def seed_loki_buffer(self, payloads: list[dict]) -> None:
            buf = self.buffer_dir / "loki.jsonl"
            buf.write_text(
                "\n".join(json.dumps(p) for p in payloads) + "\n",
                encoding="utf-8",
            )
            return buf

    return _MarkerDir(tmp_path)
