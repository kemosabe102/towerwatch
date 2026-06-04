"""Bufferbloat / latency-under-load probe.

Measures how much latency inflates while the link is saturated — the canonical
"is it us or the carrier" test. Rather than running its own (expensive) traffic
saturation, this piggybacks the scheduled Cloudflare throughput run: a background
thread pings a fixed target while the download (and then the upload) is in flight,
and the loaded RTT is compared against an idle baseline taken just before.

Sampling reuses the platform ping-flag + RTT-parse helpers from `ping.py`, so a
single-ping sample is parsed the same way as the main ping probe.

Emitted fields (merged with the throughput fields by the coordinator):
  - bufferbloat_rtt_idle_ms       — idle baseline RTT
  - bufferbloat_rtt_download_ms   — median RTT while downloading
  - bufferbloat_rtt_upload_ms     — median RTT while uploading
  - bufferbloat_download_delta_ms — loaded(download) minus idle
  - bufferbloat_upload_delta_ms   — loaded(upload) minus idle
"""

from __future__ import annotations

import logging
import statistics
import subprocess
import threading
from typing import Protocol

from towerwatch import config
from towerwatch.probes.ping import IS_WINDOWS, _build_ping_cmd, _parse_ping_output

log = logging.getLogger("towerwatch")


class _Sampler(Protocol):
    """The latency-sampler interface the coordinator depends on. Lets tests pass
    a hand-written fake without subclassing LatencyUnderLoadSampler."""

    def baseline_ms(self) -> int | None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def reset(self) -> None: ...
    def loaded_median_ms(self) -> int | None: ...


class _ModuleLokiSink:
    def log_and_push(self, level, message, **fields):
        from towerwatch.clients.loki import log_and_push

        log_and_push(level, message, **fields)


class LatencyUnderLoadSampler:
    """Ping a target repeatedly to measure idle vs. under-load latency.

    `baseline_ms()` takes an idle burst. `start()`/`stop()` run a background
    thread that records a sample every `ping_interval_s` until stopped;
    `loaded_median_ms()` reduces those samples. `reset()` clears the sample
    buffer so the same sampler can be reused for a second (upload) phase.
    """

    def __init__(
        self,
        target: str | None = None,
        subprocess_run=subprocess.run,
        loki=None,
        ping_interval_s: float | None = None,
        baseline_count: int | None = None,
        timeout_s: int | None = None,
        is_windows: bool | None = None,
    ):
        self._target = target if target is not None else config.BUFFERBLOAT_TARGET
        self._subprocess_run = subprocess_run
        self._loki = loki if loki is not None else _ModuleLokiSink()
        self._interval_s = (
            ping_interval_s if ping_interval_s is not None else config.BUFFERBLOAT_PING_INTERVAL_S
        )
        self._baseline_count = (
            baseline_count if baseline_count is not None else config.BUFFERBLOAT_BASELINE_COUNT
        )
        self._timeout_s = timeout_s if timeout_s is not None else config.BUFFERBLOAT_PING_TIMEOUT_S
        self._is_windows = is_windows if is_windows is not None else IS_WINDOWS
        self._samples: list[int] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _ping(self, count: int) -> dict:
        """Run one ping burst; return parsed fields, or a disconnected stub."""
        try:
            result = self._subprocess_run(
                _build_ping_cmd(self._target, count, self._timeout_s, self._is_windows),
                capture_output=True,
                text=True,
                timeout=self._timeout_s * count + 5,
            )
        except (subprocess.TimeoutExpired, OSError):
            return {"rtt_avg": 0, "connected": False}
        return _parse_ping_output(result.stdout, is_windows=self._is_windows)

    def _single_ping_ms(self) -> int | None:
        """One ping. Returns RTT in ms, or None if it failed/timed out."""
        fields = self._ping(1)
        if not fields.get("connected") or not fields.get("rtt_avg"):
            return None
        return int(fields["rtt_avg"])

    def baseline_ms(self) -> int | None:
        """Idle baseline RTT from a short burst. None if unreachable."""
        fields = self._ping(self._baseline_count)
        if not fields.get("connected") or not fields.get("rtt_avg"):
            return None
        return int(fields["rtt_avg"])

    def _record_loaded_sample(self) -> None:
        rtt = self._single_ping_ms()
        if rtt is not None:
            self._samples.append(rtt)

    def loaded_median_ms(self) -> int | None:
        """Median of samples collected during load. None if none collected."""
        if not self._samples:
            return None
        return round(statistics.median(self._samples))

    def reset(self) -> None:
        self._samples = []

    # ------------------------------------------------------------------
    # Background sampling thread
    # ------------------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            self._record_loaded_sample()
            # event.wait doubles as an interruptible sleep so stop() is prompt.
            self._stop.wait(self._interval_s)

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="bufferbloat", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._timeout_s + 1)
            self._thread = None


def measure_throughput_with_bufferbloat(
    *,
    sampler: _Sampler | None = None,
    download_fn=None,
    upload_fn=None,
) -> dict:
    """Run the scheduled throughput test with concurrent latency sampling.

    Returns the throughput fields merged with the bufferbloat fields. Collaborators
    are injectable for testing; defaults wire the real sampler + Cloudflare probe.
    """
    from towerwatch.probes.cloudflare import measure_http_throughput, measure_http_upload

    sampler = sampler if sampler is not None else LatencyUnderLoadSampler()
    download_fn = download_fn or measure_http_throughput
    upload_fn = upload_fn or measure_http_upload

    idle = sampler.baseline_ms()

    sampler.start()
    dl = download_fn()
    sampler.stop()
    dl_loaded = sampler.loaded_median_ms()
    sampler.reset()

    sampler.start()
    ul = upload_fn()
    sampler.stop()
    ul_loaded = sampler.loaded_median_ms()

    fields: dict = {}
    fields.update(dl)
    fields.update(ul)
    if idle is not None:
        fields["bufferbloat_rtt_idle_ms"] = idle
    if dl_loaded is not None:
        fields["bufferbloat_rtt_download_ms"] = dl_loaded
        if idle is not None:
            fields["bufferbloat_download_delta_ms"] = dl_loaded - idle
    if ul_loaded is not None:
        fields["bufferbloat_rtt_upload_ms"] = ul_loaded
        if idle is not None:
            fields["bufferbloat_upload_delta_ms"] = ul_loaded - idle

    log.info(
        "Bufferbloat: idle=%s dl=%s ul=%s ms",
        idle,
        dl_loaded,
        ul_loaded,
    )
    return fields
