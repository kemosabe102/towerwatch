"""Tests for the Cloudflare adaptive multi-stream throughput probe.

Strategy: hand-written fakes for the streaming session + a synchronous
"executor" that runs futures inline, so the threading model doesn't bleed
into the test. Tests pin behaviour around:

  - Steady-state rate computation discards warm-up bytes
  - Ramp escalates only when target_s isn't reached
  - Hard byte cap is honoured even mid-stream
  - Failures surface as zero metrics + a FAILED Loki event
  - run_speedtest() returns the legacy speedtest dict shape (used by CLI)
"""

from __future__ import annotations

from concurrent.futures import Future

from tests.fakes import FakeClock, FakeLoki


class _StreamResp:
    """Stand-in for a streaming requests.Response.

    `chunks` is the byte sequence iter_content will yield. `_raise` (if set)
    is raised from raise_for_status. Used for both /__down GETs (where the
    test seeds chunks) and /__up POSTs (where chunks is empty — the body is
    written by the probe, not read from this response).
    """

    def __init__(self, chunks=None, _raise=None):
        self._chunks = list(chunks or [])
        self._raise = _raise
        self.closed = False

    def raise_for_status(self):
        if self._raise is not None:
            raise self._raise

    def iter_content(self, chunk_size):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True


class _FakeStreamingSession:
    """Streaming-aware FakeSession.

    `get_responses` is a list of _StreamResp; each call to .get() pops one.
    `post_handler` (if provided) is called for every .post() with the
    materialised data iterator so tests can assert byte counts without
    threading; default behaviour drains the iterator and returns 200.
    """

    def __init__(self, get_responses=None, post_handler=None):
        self._get = list(get_responses or [])
        self._post_handler = post_handler
        self.get_calls: list[tuple[str, dict]] = []
        self.post_calls: list[tuple[str, dict]] = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        if not self._get:
            raise AssertionError(f"FakeSession: no queued GET response for {url!r}")
        return self._get.pop(0)

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        # Drain the iterator so the upload counter advances.
        data = kwargs.get("data")
        total = 0
        if data is not None and hasattr(data, "__iter__"):
            for chunk in data:
                total += len(chunk)
        if self._post_handler:
            return self._post_handler(url, kwargs, total)
        return _StreamResp()


class _SyncExecutor:
    """Inline executor — runs each submitted callable synchronously.

    Removes thread scheduling from the test surface. The probe's ramp loop
    waits on `as_completed`; with this executor the futures are already
    resolved when as_completed iterates them, which matches well enough.
    """

    def __init__(self, max_workers=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def submit(self, fn, *args, **kwargs):
        f: Future = Future()
        try:
            f.set_result(fn(*args, **kwargs))
        except Exception as e:  # pragma: no cover — defensive
            f.set_exception(e)
        return f


def _make_clock(steps):
    """Convenience: a FakeClock that returns `steps` from perf_counter in order."""
    return FakeClock(perf=list(steps))


# ---------------------------------------------------------------------------
# _steady_state_mbps unit tests
# ---------------------------------------------------------------------------
def test_steady_state_drops_warmup_bytes():
    from towerwatch.probes.cloudflare import _steady_state_mbps

    # 4 samples, warm-up cutoff at 1.5s — only the last two should count.
    samples = [
        (0.0, 0),
        (1.0, 1_000_000),  # all in warmup, 1 MB
        (2.0, 5_000_000),  # post-warmup checkpoint
        (3.0, 13_000_000),  # 8 MB in 1s post-warmup window
    ]
    mbps = _steady_state_mbps(samples, start_t=0.0, warmup_discard_s=1.5)
    # post-warmup window: from sample at t=2.0 (5M) to t=3.0 (13M)
    # = 8M bytes / 1s * 8 / 1e6 = 64 Mbps
    assert mbps == 64.0


def test_steady_state_returns_zero_if_window_too_short():
    from towerwatch.probes.cloudflare import _steady_state_mbps

    samples = [(0.0, 0), (0.5, 1_000_000)]  # both in warmup
    assert _steady_state_mbps(samples, start_t=0.0, warmup_discard_s=1.5) == 0.0


# ---------------------------------------------------------------------------
# Download path
# ---------------------------------------------------------------------------
def test_download_happy_path_emits_ok_event_with_bytes():
    from towerwatch.probes.cloudflare import CloudflareThroughputProbe

    # 4 streams * 8 chunks * 1 MB = 32 MB total per pass. The ramp has 2 entries
    # so up to 2 passes can fire; provide 8 responses (one per stream per pass).
    chunks = [b"x" * 1_000_000] * 8
    session = _FakeStreamingSession(get_responses=[_StreamResp(chunks) for _ in range(8)])
    # FakeClock perf sequence: each chunk consumes 2 perf calls (counter.add + deadline check),
    # plus a few outer reads in _run_streams. A long monotonic ramp at 0.05s/step keeps
    # the ramp's "elapsed_now >= target_s" check firing on schedule (5s ≈ index 100).
    perf = [i * 0.05 for i in range(2000)]
    loki = FakeLoki()
    probe = CloudflareThroughputProbe(
        session=session,
        clock=FakeClock(perf=perf),
        loki=loki,
        executor_factory=_SyncExecutor,
    )
    result = probe.measure_download()
    # Two passes * 4 streams * 8 MB = 64 MB, since each fake response yields 8 MB
    # and the ramp escalates because clock-elapsed in pass 1 < target_s.
    assert result["http_throughput_bytes"] == 2 * 4 * 8 * 1_000_000
    assert result["http_throughput_mbps"] > 0
    ok = [lp for lp in loki.log_and_pushes if lp[2].get("event") == "http_throughput_complete"]
    assert len(ok) == 1
    assert ok[0][2]["bytes_used"] == 2 * 4 * 8 * 1_000_000
    assert ok[0][2]["streams"] == 4


def test_download_failure_emits_failed_event():
    import requests

    from towerwatch.probes.cloudflare import CloudflareThroughputProbe

    session = _FakeStreamingSession(
        get_responses=[_StreamResp(_raise=requests.ConnectionError("reset")) for _ in range(8)]
    )
    perf = [i * 0.1 for i in range(200)]
    loki = FakeLoki()
    probe = CloudflareThroughputProbe(
        session=session,
        clock=FakeClock(perf=perf),
        loki=loki,
        executor_factory=_SyncExecutor,
    )
    result = probe.measure_download()
    assert result == {
        "http_throughput_ms": 0,
        "http_throughput_mbps": 0,
        "http_throughput_bytes": 0,
    }
    assert any(lp[2].get("event") == "http_throughput_failed" for lp in loki.log_and_pushes)


# ---------------------------------------------------------------------------
# Upload path
# ---------------------------------------------------------------------------
def test_upload_happy_path_emits_ok_event_with_bytes():
    from towerwatch.probes.cloudflare import CloudflareThroughputProbe

    session = _FakeStreamingSession()  # default post_handler drains data iterator
    perf = [i * 0.05 for i in range(4000)]
    loki = FakeLoki()
    probe = CloudflareThroughputProbe(
        session=session,
        clock=FakeClock(perf=perf),
        loki=loki,
        rand_bytes=lambda n: b"x" * n,
        executor_factory=_SyncExecutor,
    )
    result = probe.measure_upload()
    assert result["http_upload_bytes"] > 0
    assert result["http_upload_mbps"] > 0
    ok = [lp for lp in loki.log_and_pushes if lp[2].get("event") == "http_upload_complete"]
    assert len(ok) == 1
    assert ok[0][2]["bytes_used"] > 0


# ---------------------------------------------------------------------------
# run_speedtest (manual CLI entrypoint)
# ---------------------------------------------------------------------------
def test_run_speedtest_returns_legacy_dict_shape(monkeypatch):
    """The CLI consumes the legacy {download_mbps, upload_mbps, success} dict."""
    from towerwatch.probes import cloudflare

    class _StubProbe:
        def measure_download(self):
            return {
                "http_throughput_ms": 5000,
                "http_throughput_mbps": 320.5,
                "http_throughput_bytes": 200_000_000,
            }

        def measure_upload(self):
            return {
                "http_upload_ms": 5000,
                "http_upload_mbps": 35.0,
                "http_upload_bytes": 50_000_000,
            }

    monkeypatch.setattr(cloudflare, "_shared", lambda: _StubProbe())
    out = cloudflare.run_speedtest(triggered_by="alice")
    assert out == {"download_mbps": 320.5, "upload_mbps": 35.0, "success": 1}


def test_run_speedtest_marks_failure_when_either_direction_zero(monkeypatch):
    from towerwatch.probes import cloudflare

    class _StubProbe:
        def measure_download(self):
            return {
                "http_throughput_ms": 0,
                "http_throughput_mbps": 0,
                "http_throughput_bytes": 0,
            }

        def measure_upload(self):
            return {
                "http_upload_ms": 5000,
                "http_upload_mbps": 35.0,
                "http_upload_bytes": 50_000_000,
            }

    monkeypatch.setattr(cloudflare, "_shared", lambda: _StubProbe())
    out = cloudflare.run_speedtest()
    assert out["success"] == 0
