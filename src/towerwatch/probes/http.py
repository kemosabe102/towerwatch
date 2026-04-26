"""HTTP latency probe (~10 KB CDN fetch, 5-min cadence).

Throughput + upload moved to towerwatch.probes.cloudflare — the multi-stream
adaptive probe that replaced the single-stream version.
"""

import logging

import requests

from towerwatch import config
from towerwatch.clock import Clock, SystemClock
from towerwatch.probes.base import ProbeResult

log = logging.getLogger("towerwatch")


class HTTPLatencyProbe:
    """Timed ~10 KB CDN fetch — returns elapsed ms (0 on failure)."""

    name = "http_latency"

    def __init__(
        self,
        session=None,
        clock: Clock | None = None,
        url: str | None = None,
        timeout_s: int | None = None,
    ):
        self._session = session if session is not None else requests.Session()
        self._clock = clock if clock is not None else SystemClock()
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


_shared_latency_probe: HTTPLatencyProbe | None = None


def measure_http_latency() -> int:
    global _shared_latency_probe
    if _shared_latency_probe is None:
        _shared_latency_probe = HTTPLatencyProbe()
    return _shared_latency_probe.measure()
