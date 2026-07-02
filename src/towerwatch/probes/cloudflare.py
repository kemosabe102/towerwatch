"""Cloudflare adaptive multi-stream throughput probe.

Replaces the previous single-stream HTTPThroughputProbe + HTTPUploadProbe and
the Ookla CLI manual speedtest. Implements a faithful subset of the protocol
that powers speed.cloudflare.com:

  - N parallel TCP streams against /__down (download) or /__up (upload)
  - Adaptive ramp: try 25 MB, escalate to 100 MB if total elapsed < target
  - Discard the first WARMUP_DISCARD_S of bytes from the rate calculation so
    TCP slow-start doesn't drag the number down
  - Hard cap on total bytes per direction (data-budget guardrail)

Two modes:
  - "scheduled": emits towerwatch_http_throughput_mbps / towerwatch_http_upload_mbps
                 (existing dashboard gauges keep working)
  - "manual":    emits towerwatch_speedtest_download_mbps / *_upload_mbps
                 (Manual Speedtest History panel keeps working)
"""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

from towerwatch import config
from towerwatch.clock import Clock, SystemClock

log = logging.getLogger("towerwatch")

_CHUNK_BYTES = 64 * 1024  # read granularity for stream-counting


class _ModuleLokiSink:
    """Lazy façade so the probe doesn't need a LokiClient threaded in."""

    def log_and_push(self, level, message, **fields):
        from towerwatch.clients.loki import log_and_push

        log_and_push(level, message, **fields)


class _ByteCounter:
    """Thread-safe stream of (timestamp, bytes_so_far) samples.

    Each download/upload thread calls `add(n)` after each chunk; the main
    thread reads `samples` after all workers join to compute steady-state
    throughput from the post-warmup window.
    """

    def __init__(self, clock: Clock):
        self._clock = clock
        self._lock = threading.Lock()
        self._total = 0
        self.samples: list[tuple[float, int]] = []  # (perf_counter, total_bytes)

    def add(self, n: int) -> None:
        with self._lock:
            self._total += n
            self.samples.append((self._clock.perf_counter(), self._total))

    @property
    def total(self) -> int:
        with self._lock:
            return self._total


def _steady_state_mbps(
    samples: list[tuple[float, int]],
    start_t: float,
    warmup_discard_s: float,
) -> float:
    """Throughput in Mbps over the post-warmup window.

    Takes the (timestamp, cumulative-bytes) sample stream, finds the first
    sample at start_t + warmup_discard_s, and computes (bytes_in_window) /
    (elapsed_in_window) * 8 / 1e6.

    Returns 0.0 if the window has fewer than 2 samples — happens on very
    short transfers where the warmup eats the whole test (we'll log a warning
    upstream and accept the test as low-confidence).
    """
    if len(samples) < 2:
        return 0.0
    cutoff = start_t + warmup_discard_s
    post = [s for s in samples if s[0] >= cutoff]
    if len(post) < 2:
        return 0.0
    t0, b0 = post[0]
    t1, b1 = post[-1]
    elapsed = t1 - t0
    if elapsed <= 0:
        return 0.0
    return round((b1 - b0) * 8 / elapsed / 1_000_000, 2)


def _download_stream(
    session: requests.Session,
    url: str,
    bytes_per_stream: int,
    counter: _ByteCounter,
    deadline_s: float,
    clock: Clock,
    timeout_s: int,
) -> int:
    """Download `bytes_per_stream` from Cloudflare. Returns bytes read.

    Stops early if `clock.perf_counter() > deadline_s` — that's how we
    enforce the global byte-cap across all streams cooperatively without
    needing to cancel in-flight requests.
    """
    full_url = f"{url}?bytes={bytes_per_stream}"
    bytes_read = 0
    try:
        with session.get(full_url, stream=True, timeout=timeout_s) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_content(chunk_size=_CHUNK_BYTES):
                if not chunk:
                    continue
                bytes_read += len(chunk)
                counter.add(len(chunk))
                if clock.perf_counter() > deadline_s:
                    break
    except requests.RequestException as e:
        log.warning("Cloudflare download stream failed: %s", e)
    return bytes_read


def _upload_stream(
    session: requests.Session,
    url: str,
    bytes_per_stream: int,
    counter: _ByteCounter,
    deadline_s: float,
    clock: Clock,
    timeout_s: int,
    rand_bytes,
) -> int:
    """POST `bytes_per_stream` to Cloudflare /__up. Returns bytes sent.

    Generates payload as a chunked iterator so very large uploads don't
    materialise the whole buffer in memory at once and so we can break out
    when the global deadline trips.
    """
    bytes_sent = 0

    def gen():
        nonlocal bytes_sent
        remaining = bytes_per_stream
        while remaining > 0:
            if clock.perf_counter() > deadline_s:
                return
            n = min(_CHUNK_BYTES, remaining)
            chunk = rand_bytes(n)
            counter.add(n)
            bytes_sent += n
            remaining -= n
            yield chunk

    try:
        resp = session.post(
            url,
            data=gen(),
            headers={"Content-Type": "application/octet-stream"},
            timeout=timeout_s,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning("Cloudflare upload stream failed: %s", e)
    return bytes_sent


class CloudflareThroughputProbe:
    """Adaptive multi-stream throughput probe.

    Single class with `measure_download()` and `measure_upload()` methods.
    The `_run_streams()` helper is shared between the two directions; only
    the per-stream worker function differs.
    """

    name = "cloudflare_throughput"

    def __init__(
        self,
        session=None,
        clock: Clock | None = None,
        loki=None,
        rand_bytes=os.urandom,
        executor_factory: Any = ThreadPoolExecutor,
        dl_max_total_bytes: int | None = None,
        ul_max_total_bytes: int | None = None,
    ):
        self._session = session if session is not None else requests.Session()
        self._clock = clock if clock is not None else SystemClock()
        self._loki = loki if loki is not None else _ModuleLokiSink()
        self._rand_bytes = rand_bytes
        self._executor_factory = executor_factory
        # Per-run byte-cap overrides (manual field runs shrink these for tight
        # ABAB alternation without a redeploy). None -> use the config defaults.
        self._dl_max_total_bytes = (
            dl_max_total_bytes
            if dl_max_total_bytes is not None
            else config.CLOUDFLARE_THROUGHPUT_MAX_TOTAL_BYTES
        )
        self._ul_max_total_bytes = (
            ul_max_total_bytes
            if ul_max_total_bytes is not None
            else config.CLOUDFLARE_UPLOAD_MAX_TOTAL_BYTES
        )

    # ------------------------------------------------------------------
    # Shared ramp + parallel-stream driver
    # ------------------------------------------------------------------
    def _run_streams(
        self,
        worker,
        ramp_bytes: tuple[int, ...],
        streams: int,
        max_total_bytes: int,
        target_s: float,
        warmup_discard_s: float,
        timeout_s: int,
    ) -> tuple[float, int, float]:
        """Run the adaptive ramp. Returns (mbps, total_bytes, elapsed_s).

        Each entry in `ramp_bytes` is a per-stream byte target. We try the
        first entry; if total elapsed < target_s and we haven't hit the
        cap, escalate to the next entry. Bytes from earlier passes count
        toward the cap so we can't blow the budget.
        """
        counter = _ByteCounter(self._clock)
        start_t = self._clock.perf_counter()
        total_bytes = 0

        for per_stream in ramp_bytes:
            remaining_cap = max_total_bytes - total_bytes
            if remaining_cap <= 0:
                break
            # Per-stream target shrinks if the cap would be exceeded.
            per_stream_capped = min(per_stream, max(1, remaining_cap // streams))
            deadline_s = start_t + max(target_s * 2, timeout_s)
            with self._executor_factory(max_workers=streams) as ex:
                futures = [
                    ex.submit(worker, per_stream_capped, counter, deadline_s, timeout_s)
                    for _ in range(streams)
                ]
                for f in as_completed(futures):
                    total_bytes += f.result()
            elapsed_now = self._clock.perf_counter() - start_t
            if elapsed_now >= target_s:
                break

        elapsed_s = self._clock.perf_counter() - start_t
        mbps = _steady_state_mbps(counter.samples, start_t, warmup_discard_s)
        return mbps, total_bytes, elapsed_s

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------
    def measure_download(self) -> dict:
        def worker(per_stream, counter, deadline_s, timeout_s):
            return _download_stream(
                self._session,
                config.CLOUDFLARE_THROUGHPUT_DL_URL,
                per_stream,
                counter,
                deadline_s,
                self._clock,
                timeout_s,
            )

        try:
            mbps, total_bytes, elapsed_s = self._run_streams(
                worker,
                config.CLOUDFLARE_THROUGHPUT_RAMP_BYTES,
                config.CLOUDFLARE_THROUGHPUT_STREAMS,
                self._dl_max_total_bytes,
                config.CLOUDFLARE_THROUGHPUT_TARGET_S,
                config.CLOUDFLARE_THROUGHPUT_WARMUP_DISCARD_S,
                config.CLOUDFLARE_THROUGHPUT_TIMEOUT_S,
            )
            elapsed_ms = round(elapsed_s * 1000)
            if mbps <= 0 or total_bytes == 0:
                raise ValueError(f"invalid sample: {total_bytes}B in {elapsed_s:.2f}s")
            self._loki.log_and_push(
                "INFO",
                f"Throughput: {mbps} Mbps ({elapsed_ms}ms, {total_bytes}B, "
                f"{config.CLOUDFLARE_THROUGHPUT_STREAMS} streams)",
                event=config.LOG_EVENT_HTTP_THROUGHPUT_OK,
                throughput_mbps=mbps,
                elapsed_ms=elapsed_ms,
                bytes_used=total_bytes,
                streams=config.CLOUDFLARE_THROUGHPUT_STREAMS,
            )
            return {
                "http_throughput_ms": elapsed_ms,
                "http_throughput_mbps": mbps,
                "http_throughput_bytes": total_bytes,
            }
        except Exception as e:
            self._loki.log_and_push(
                "WARN",
                f"HTTP throughput test failed: {e}",
                event=config.LOG_EVENT_HTTP_THROUGHPUT_FAILED,
                error=str(e),
            )
            return {
                "http_throughput_ms": 0,
                "http_throughput_mbps": 0,
                "http_throughput_bytes": 0,
            }

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------
    def measure_upload(self) -> dict:
        def worker(per_stream, counter, deadline_s, timeout_s):
            return _upload_stream(
                self._session,
                config.CLOUDFLARE_THROUGHPUT_UL_URL,
                per_stream,
                counter,
                deadline_s,
                self._clock,
                timeout_s,
                self._rand_bytes,
            )

        try:
            mbps, total_bytes, elapsed_s = self._run_streams(
                worker,
                config.CLOUDFLARE_UPLOAD_RAMP_BYTES,
                config.CLOUDFLARE_UPLOAD_STREAMS,
                self._ul_max_total_bytes,
                config.CLOUDFLARE_THROUGHPUT_TARGET_S,
                config.CLOUDFLARE_THROUGHPUT_WARMUP_DISCARD_S,
                config.CLOUDFLARE_THROUGHPUT_TIMEOUT_S,
            )
            elapsed_ms = round(elapsed_s * 1000)
            if mbps <= 0 or total_bytes == 0:
                raise ValueError(f"invalid sample: {total_bytes}B in {elapsed_s:.2f}s")
            self._loki.log_and_push(
                "INFO",
                f"Upload: {mbps} Mbps ({elapsed_ms}ms, {total_bytes}B, "
                f"{config.CLOUDFLARE_UPLOAD_STREAMS} streams)",
                event=config.LOG_EVENT_HTTP_UPLOAD_OK,
                upload_mbps=mbps,
                elapsed_ms=elapsed_ms,
                bytes_used=total_bytes,
                streams=config.CLOUDFLARE_UPLOAD_STREAMS,
            )
            return {
                "http_upload_ms": elapsed_ms,
                "http_upload_mbps": mbps,
                "http_upload_bytes": total_bytes,
            }
        except Exception as e:
            self._loki.log_and_push(
                "WARN",
                f"HTTP upload test failed: {e}",
                event=config.LOG_EVENT_HTTP_UPLOAD_FAILED,
                error=str(e),
            )
            return {
                "http_upload_ms": 0,
                "http_upload_mbps": 0,
                "http_upload_bytes": 0,
            }


# ---------------------------------------------------------------------------
# Module-level wrappers — drop-in replacements for the old http.py functions.
# tick.py imports these by name; keeping the names lets us swap implementations
# without touching the call sites.
# ---------------------------------------------------------------------------
_shared_probe: CloudflareThroughputProbe | None = None


def _shared() -> CloudflareThroughputProbe:
    global _shared_probe
    if _shared_probe is None:
        _shared_probe = CloudflareThroughputProbe()
    return _shared_probe


def measure_http_throughput() -> dict:
    return _shared().measure_download()


def measure_http_upload() -> dict:
    return _shared().measure_upload()


def run_speedtest(
    *,
    triggered_by: str | None = None,
    loki=None,
    max_total_bytes: int | None = None,
) -> dict:
    """Manual-speedtest entrypoint. Returns {download_mbps, upload_mbps, success}.

    Used by `speedtest_cli.py` — the SSH-triggered manual flow. The metric
    field names match the old Ookla function so format_speedtest_line and
    the dashboard's Manual Speedtest History panel keep working.

    `max_total_bytes` caps BOTH the download and upload byte budgets for this
    one run (overriding the config defaults). Use it to shrink a field run for
    tight back-to-back alternation on a metered link; None keeps the defaults.
    A dedicated probe is constructed whenever an override or a loki sink is
    given — the shared singleton is only used for the plain default path.
    """
    if loki is not None or max_total_bytes is not None:
        probe = CloudflareThroughputProbe(
            loki=loki,
            dl_max_total_bytes=max_total_bytes,
            ul_max_total_bytes=max_total_bytes,
        )
    else:
        probe = _shared()
    dl = probe.measure_download()
    ul = probe.measure_upload()
    success = 1 if (dl["http_throughput_mbps"] > 0 and ul["http_upload_mbps"] > 0) else 0
    if success and triggered_by:
        # Augment the throughput log line with operator identity for the
        # Manual Speedtest History panel's `triggered_by` label.
        log.info(
            "Speedtest by %s: %s↓ / %s↑ Mbps",
            triggered_by,
            dl["http_throughput_mbps"],
            ul["http_upload_mbps"],
        )
    return {
        "download_mbps": dl["http_throughput_mbps"],
        "upload_mbps": ul["http_upload_mbps"],
        "download_bytes": dl.get("http_throughput_bytes", 0),
        "upload_bytes": ul.get("http_upload_bytes", 0),
        "success": success,
    }
