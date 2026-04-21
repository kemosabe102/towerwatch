"""HTTP latency and throughput probes."""

import logging
import time

import requests

import config
from loki import log_and_push
from probes.base import Probe, ProbeResult

log = logging.getLogger("towerwatch")

_http_session: requests.Session | None = None


def _get_session() -> requests.Session:
    global _http_session
    if _http_session is None:
        _http_session = requests.Session()
    return _http_session


def measure_http_latency() -> float:
    """Timed download of ~10KB CDN asset for latency proxy. Returns elapsed ms, 0 on failure."""
    try:
        start = time.perf_counter()
        resp = _get_session().get(
            config.HTTP_LATENCY_URL,
            timeout=config.HTTP_LATENCY_TIMEOUT_S,
        )
        resp.raise_for_status()
        _ = resp.content
        return round((time.perf_counter() - start) * 1000)
    except Exception as e:
        log.warning("HTTP latency probe failed: %s", e)
        return 0


def measure_http_throughput() -> dict:
    """Timed download of ~1MB CDN asset for throughput estimation.
    Returns {http_throughput_ms, http_throughput_mbps}, zeros on failure."""
    try:
        start = time.perf_counter()
        resp = _get_session().get(
            config.HTTP_THROUGHPUT_URL,
            timeout=config.HTTP_THROUGHPUT_TIMEOUT_S,
        )
        resp.raise_for_status()
        size_bytes = len(resp.content)
        elapsed_s = time.perf_counter() - start
        throughput_mbps = round((size_bytes * 8) / elapsed_s / 1_000_000, 2)
        elapsed_ms = round(elapsed_s * 1000)
        log_and_push("INFO", f"Throughput: {throughput_mbps} Mbps ({elapsed_ms}ms)",
                     event=config.LOG_EVENT_HTTP_THROUGHPUT_OK,
                     throughput_mbps=throughput_mbps, elapsed_ms=elapsed_ms)
        return {"http_throughput_ms": elapsed_ms, "http_throughput_mbps": throughput_mbps}
    except Exception as e:
        log_and_push("WARN", f"HTTP throughput test failed: {e}",
                     event=config.LOG_EVENT_HTTP_THROUGHPUT_FAILED, error=str(e))
        return {"http_throughput_ms": 0, "http_throughput_mbps": 0}


class HTTPLatencyProbe:
    name = "http_latency"

    def run(self) -> ProbeResult:
        ms = measure_http_latency()
        return ProbeResult(fields={"http_latency_ms": ms}, ok=ms > 0)


class HTTPThroughputProbe:
    name = "http_throughput"

    def run(self) -> ProbeResult:
        f = measure_http_throughput()
        return ProbeResult(fields=f, ok=f["http_throughput_ms"] > 0)
