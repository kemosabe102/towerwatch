"""Shared test fakes.

These replace the MagicMock / monkeypatch pattern with explicit, injectable
collaborators. Each fake records its interactions so tests can assert
behaviour without reaching into module internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------
class FakeClock:
    """Deterministic Clock. perf/wall values are consumed in order.

    Usage:
        clock = FakeClock(perf=[0.0, 0.08])     # two perf_counter readings
        clock = FakeClock(wall=[1700000000.0])  # one time() reading
    """

    def __init__(self, perf=None, wall=None):
        self._perf = list(perf or [])
        self._wall = list(wall or [])
        self.sleeps: list[float] = []

    def perf_counter(self) -> float:
        if not self._perf:
            raise AssertionError("FakeClock.perf_counter called with no values left")
        return self._perf.pop(0)

    def time(self) -> float:
        if not self._wall:
            raise AssertionError("FakeClock.time called with no values left")
        return self._wall.pop(0)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


# ---------------------------------------------------------------------------
# HTTP response / session
# ---------------------------------------------------------------------------
@dataclass
class FakeResponse:
    """Stand-in for requests.Response."""

    status_code: int = 200
    content: bytes = b""
    text: str = ""
    _json: Any = None
    _raise: Exception | None = None

    def raise_for_status(self) -> None:
        if self._raise is not None:
            raise self._raise

    def json(self):
        if isinstance(self._json, Exception):
            raise self._json
        return self._json if self._json is not None else {}


class FakeSession:
    """Stand-in for requests.Session. Responses are popped in order.

    A list entry may be a FakeResponse, a callable `(url, kwargs) -> FakeResponse`,
    or an Exception (raised instead of returning).
    """

    def __init__(self, get_responses=None, post_responses=None):
        self._get = list(get_responses or [])
        self._post = list(post_responses or [])
        self.get_calls: list[tuple[str, dict]] = []
        self.post_calls: list[tuple[str, dict]] = []
        self.headers: dict = {}
        self.auth = None

    def _consume(self, queue, url, kwargs, recorder):
        recorder.append((url, kwargs))
        if not queue:
            raise AssertionError(f"FakeSession: no queued response for {url!r}")
        r = queue.pop(0)
        if isinstance(r, Exception):
            raise r
        if callable(r):
            return r(url, kwargs)
        return r

    def get(self, url, **kwargs):
        return self._consume(self._get, url, kwargs, self.get_calls)

    def post(self, url, **kwargs):
        return self._consume(self._post, url, kwargs, self.post_calls)

    def close(self):
        pass


# ---------------------------------------------------------------------------
# DNS resolver
# ---------------------------------------------------------------------------
class FakeResolver:
    """Stand-in for dns.resolver.Resolver."""

    def __init__(self, result=None, raises=None):
        self.nameservers: list[str] = []
        self.lifetime: float = 0
        self._result = result if result is not None else []
        self._raises = raises
        self.resolve_calls: list[tuple[str, str]] = []

    def resolve(self, domain, rdtype):
        self.resolve_calls.append((domain, rdtype))
        if self._raises:
            raise self._raises
        return self._result


# ---------------------------------------------------------------------------
# Socket
# ---------------------------------------------------------------------------
class FakeSocket:
    """Stand-in for a TCP socket. Call `.fail_with(exc)` to make connect raise."""

    def __init__(self, connect_raises=None):
        self._connect_raises = connect_raises
        self.timeout: float | None = None
        self.connect_calls: list[tuple] = []
        self.closed = False

    def settimeout(self, timeout):
        self.timeout = timeout

    def connect(self, address):
        self.connect_calls.append(address)
        if self._connect_raises is not None:
            raise self._connect_raises

    def close(self):
        self.closed = True


def fake_socket_factory(connect_raises=None):
    """Returns a callable compatible with `socket.socket(...)`. Each call
    returns a fresh FakeSocket, recorded on the factory's `.sockets` list."""

    class _Factory:
        def __init__(self):
            self.sockets: list[FakeSocket] = []

        def __call__(self, *args, **kwargs):
            s = FakeSocket(connect_raises=connect_raises)
            self.sockets.append(s)
            return s

    return _Factory()


# ---------------------------------------------------------------------------
# Subprocess
# ---------------------------------------------------------------------------
@dataclass
class FakeCompletedProcess:
    """Stand-in for subprocess.CompletedProcess."""

    stdout: str = ""
    stderr: str = ""
    returncode: int = 0


class FakeSubprocess:
    """Callable stand-in for subprocess.run. Each call consumes one entry.

    Entry may be a FakeCompletedProcess or an Exception to raise."""

    def __init__(self, *outcomes):
        self._outcomes = list(outcomes)
        self.calls: list[tuple] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        if not self._outcomes:
            raise AssertionError(f"FakeSubprocess: no queued outcome for {cmd!r}")
        out = self._outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


# ---------------------------------------------------------------------------
# Loki sink
# ---------------------------------------------------------------------------
class FakeLoki:
    """Records every push / log_and_push / flush call.

    Duck-types the LokiClient interface used by probes and orchestration:
    - push(level, message, extra=None)
    - log_and_push(level, message, **fields)
    - flush() -> int
    """

    def __init__(self, flush_return: int = 0):
        self.pushes: list[tuple[str, str, dict]] = []  # (level, message, extra)
        self.log_and_pushes: list[tuple[str, str, dict]] = []
        self.flush_calls = 0
        self._flush_return = flush_return

    def push(self, level, message, extra=None):
        self.pushes.append((level, message, dict(extra or {})))

    def log_and_push(self, level, message, **fields):
        self.log_and_pushes.append((level, message, dict(fields)))

    def flush(self) -> int:
        self.flush_calls += 1
        return self._flush_return

    # ------------------------------------------------------------------
    # Convenience accessors for assertions
    # ------------------------------------------------------------------
    def events(self) -> list[str]:
        """Return the `event=` value from every push/log_and_push, in order."""
        out = []
        for _, _, extra in self.pushes:
            if "event" in extra:
                out.append(extra["event"])
        for _, _, fields in self.log_and_pushes:
            if "event" in fields:
                out.append(fields["event"])
        return out

    def called_with_event(self, event_name: str) -> bool:
        return event_name in self.events()


# ---------------------------------------------------------------------------
# Events (module-duck-typed)
# ---------------------------------------------------------------------------
class FakeEvents:
    """Records every call to any `events.*` function.

    Duck-types the events module: any attribute access returns a recording
    callable. Inspect `.calls` as a list of `(event_name, args, kwargs)` tuples.
    """

    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name):
        def _record(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return _record

    def names(self) -> list[str]:
        """Convenience: list of event names in call order."""
        return [c[0] for c in self.calls]

    def called(self, name: str) -> bool:
        return any(c[0] == name for c in self.calls)


# ---------------------------------------------------------------------------
# Grafana client
# ---------------------------------------------------------------------------
class FakeGrafana:
    """Records push_metrics and push_annotation calls."""

    def __init__(self, push_ok: bool = True, annotation_ok: bool = True):
        self._push_ok = push_ok
        self._annotation_ok = annotation_ok
        self.push_calls: list[list[str]] = []
        self.annotation_calls: list[dict] = []

    def push_metrics(self, lines) -> bool:
        self.push_calls.append(list(lines))
        return self._push_ok

    def push_annotation(self, time_ms, time_end_ms, text, *, reason=None, version=None):
        self.annotation_calls.append(
            {
                "time_ms": time_ms,
                "time_end_ms": time_end_ms,
                "text": text,
                "reason": reason,
                "version": version,
            }
        )
        return self._annotation_ok


# ---------------------------------------------------------------------------
# Signal (for lifecycle tests)
# ---------------------------------------------------------------------------
class FakeSignal:
    """Stand-in for the `signal` module. Records handler registrations."""

    def __init__(self):
        self.handlers: dict[int, Any] = {}
        # Mirror the real module's constants so code referencing them works.
        import signal as _real_signal

        self.SIGTERM = _real_signal.SIGTERM
        self.SIGINT = _real_signal.SIGINT

    def signal(self, signum, handler):
        self.handlers[signum] = handler
        return None
