"""HTTP latency, throughput (download), and upload probes.

All three probes are class-based and take their collaborators (session, clock,
loki sink) via the constructor. The module-level functions
`measure_http_latency()` / `measure_http_throughput()` / `measure_http_upload()`
are thin back-compat wrappers that instantiate a default probe per call.
"""

import logging
import os

import requests

from towerwatch import config
from towerwatch.clock import Clock, SystemClock
from towerwatch.probes.base import ProbeResult

log = logging.getLogger("towerwatch")


class _ModuleLokiSink:
    """Lazy façade that calls loki.log_and_push via the module-level shim.

    Used as the production default so probes don't require a LokiClient to
    be threaded through construction.
    """

    def log_and_push(self, level, message, **fields):
        from towerwatch.clients.loki import log_and_push

        log_and_push(level, message, **fields)


class HTTPLatencyProbe:
    """Timed ~10 KB CDN fetch — returns elapsed ms (0 on failure)."""

    name = "http_latency"

    def __init__(
        self,
        session=None,
        clock: Clock | None = None,
        loki=None,
        url: str | None = None,
        timeout_s: int | None = None,
    ):
        self._session = session if session is not None else requests.Session()
        self._clock = clock if clock is not None else SystemClock()
        self._loki = loki if loki is not None else _ModuleLokiSink()
        self._url = url if url is not None else config.HTTP_LATENCY_URL
        self._timeout_s = timeout_s if timeout_s is not None else config.HTTP_LATENCY_TIMEOUT_S

    def measure(self) -> int:
        try:
            start = self._clock.perf_counter()
            resp = self._session.get(self._url, timeout=self._timeout_s)
            resp.raise_for_status()
            _ = resp.content
            return round((self._clock.perf_counter() - start) * 1000)
        except Exception as e:
            log.warning("HTTP latency probe failed: %s", e)
            return 0

    def run(self) -> ProbeResult:
        ms = self.measure()
        return ProbeResult(fields={"http_latency_ms": ms}, ok=ms > 0)


class HTTPThroughputProbe:
    """Timed ~1 MB CDN fetch — returns {http_throughput_ms, http_throughput_mbps}."""

    name = "http_throughput"

    def __init__(
        self,
        session=None,
        clock: Clock | None = None,
        loki=None,
        url: str | None = None,
        timeout_s: int | None = None,
    ):
        self._session = session if session is not None else requests.Session()
        self._clock = clock if clock is not None else SystemClock()
        self._loki = loki if loki is not None else _ModuleLokiSink()
        self._url = url if url is not None else config.HTTP_THROUGHPUT_URL
        self._timeout_s = timeout_s if timeout_s is not None else config.HTTP_THROUGHPUT_TIMEOUT_S

    def measure(self) -> dict:
        try:
            start = self._clock.perf_counter()
            resp = self._session.get(self._url, timeout=self._timeout_s)
            resp.raise_for_status()
            size_bytes = len(resp.content)
            elapsed_s = self._clock.perf_counter() - start
            if elapsed_s <= 0 or size_bytes == 0:
                raise ValueError(f"invalid sample: {size_bytes}B in {elapsed_s:.6f}s")
            throughput_mbps = round((size_bytes * 8) / elapsed_s / 1_000_000, 2)
            elapsed_ms = round(elapsed_s * 1000)
            self._loki.log_and_push(
                "INFO",
                f"Throughput: {throughput_mbps} Mbps ({elapsed_ms}ms, {size_bytes}B)",
                event=config.LOG_EVENT_HTTP_THROUGHPUT_OK,
                throughput_mbps=throughput_mbps,
                elapsed_ms=elapsed_ms,
                bytes_used=size_bytes,
            )
            return {
                "http_throughput_ms": elapsed_ms,
                "http_throughput_mbps": throughput_mbps,
                "http_throughput_bytes": size_bytes,
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

    def run(self) -> ProbeResult:
        f = self.measure()
        return ProbeResult(fields=f, ok=f["http_throughput_ms"] > 0)


class HTTPUploadProbe:
    """Timed POST of `bytes_to_upload` random bytes to a Cloudflare endpoint.

    Returns {http_upload_ms, http_upload_mbps, http_upload_bytes}. Pairs with
    HTTPThroughputProbe to give symmetric download/upload measurements at
    matching cadence, fitting in the project's data-budget envelope.
    """

    name = "http_upload"

    def __init__(
        self,
        session=None,
        clock: Clock | None = None,
        loki=None,
        url: str | None = None,
        timeout_s: int | None = None,
        bytes_to_upload: int | None = None,
        rand_bytes=os.urandom,
    ):
        self._session = session if session is not None else requests.Session()
        self._clock = clock if clock is not None else SystemClock()
        self._loki = loki if loki is not None else _ModuleLokiSink()
        self._url = url if url is not None else config.HTTP_UPLOAD_URL
        self._timeout_s = timeout_s if timeout_s is not None else config.HTTP_UPLOAD_TIMEOUT_S
        self._bytes_to_upload = (
            bytes_to_upload if bytes_to_upload is not None else config.HTTP_UPLOAD_BYTES
        )
        self._rand_bytes = rand_bytes

    def measure(self) -> dict:
        try:
            payload = self._rand_bytes(self._bytes_to_upload)
            size_bytes = len(payload)
            start = self._clock.perf_counter()
            resp = self._session.post(
                self._url,
                data=payload,
                headers={"Content-Type": "application/octet-stream"},
                timeout=self._timeout_s,
            )
            resp.raise_for_status()
            elapsed_s = self._clock.perf_counter() - start
            if elapsed_s <= 0 or size_bytes == 0:
                raise ValueError(f"invalid sample: {size_bytes}B in {elapsed_s:.6f}s")
            throughput_mbps = round((size_bytes * 8) / elapsed_s / 1_000_000, 2)
            elapsed_ms = round(elapsed_s * 1000)
            self._loki.log_and_push(
                "INFO",
                f"Upload: {throughput_mbps} Mbps ({elapsed_ms}ms, {size_bytes}B)",
                event=config.LOG_EVENT_HTTP_UPLOAD_OK,
                upload_mbps=throughput_mbps,
                elapsed_ms=elapsed_ms,
                bytes_used=size_bytes,
            )
            return {
                "http_upload_ms": elapsed_ms,
                "http_upload_mbps": throughput_mbps,
                "http_upload_bytes": size_bytes,
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

    def run(self) -> ProbeResult:
        f = self.measure()
        return ProbeResult(fields=f, ok=f["http_upload_ms"] > 0)


# ---------------------------------------------------------------------------
# Back-compat module-level wrappers. These each instantiate a default probe
# lazily so module import remains side-effect-free.
# ---------------------------------------------------------------------------
_shared_latency_probe: HTTPLatencyProbe | None = None
_shared_throughput_probe: HTTPThroughputProbe | None = None
_shared_upload_probe: HTTPUploadProbe | None = None


def measure_http_latency() -> int:
    global _shared_latency_probe
    if _shared_latency_probe is None:
        _shared_latency_probe = HTTPLatencyProbe()
    return _shared_latency_probe.measure()


def measure_http_throughput() -> dict:
    global _shared_throughput_probe
    if _shared_throughput_probe is None:
        _shared_throughput_probe = HTTPThroughputProbe()
    return _shared_throughput_probe.measure()


def measure_http_upload() -> dict:
    global _shared_upload_probe
    if _shared_upload_probe is None:
        _shared_upload_probe = HTTPUploadProbe()
    return _shared_upload_probe.measure()
