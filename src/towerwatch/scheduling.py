"""
Scheduling helpers: daily throughput slots, HTTP latency interval, heartbeat cadence.
"""

import logging
import random as _random
import time as _time
from typing import Any

from towerwatch import config

log = logging.getLogger("towerwatch")


def _window_label(start: int, end: int) -> str:
    """Human label for a (start_hour, end_hour) window, used in startup logs."""
    if start <= 5:
        return "early"
    if start <= 10:
        return "morning"
    if start <= 14:
        return "midday"
    if start <= 17:
        return "afternoon"
    if start <= 21:
        return "evening"
    return "night"


class Scheduler:
    def __init__(
        self,
        *,
        http_latency_interval_s: int,
        http_throughput_tests_per_day: int,
        heartbeat_interval_s: int,
        throughput_windows: list[tuple[int, int]] | None = None,
        clock: Any = _time,
        rng: Any = _random,
    ):
        self._latency_interval = http_latency_interval_s
        self._throughput_n = http_throughput_tests_per_day
        self._heartbeat_interval = heartbeat_interval_s
        self._windows = throughput_windows
        self._clock = clock
        self._rng = rng

        self._last_latency_ts: float = 0.0
        self._last_heartbeat_ts: float = 0.0
        self._throughput_schedule: list[float] = []
        self._last_schedule_day: int = -1

    @classmethod
    def from_config(cls, cfg=None) -> "Scheduler":
        cfg = cfg or config
        return cls(
            http_latency_interval_s=cfg.HTTP_LATENCY_INTERVAL_S,
            http_throughput_tests_per_day=cfg.CLOUDFLARE_THROUGHPUT_TESTS_PER_DAY,
            heartbeat_interval_s=cfg.HEARTBEAT_INTERVAL_S,
            throughput_windows=cfg.CLOUDFLARE_THROUGHPUT_WINDOWS,
        )

    # ------------------------------------------------------------------
    # HTTP latency gate
    # ------------------------------------------------------------------
    def should_run_http_latency(self, now: float) -> bool:
        if now - self._last_latency_ts >= self._latency_interval:
            self._last_latency_ts = now
            return True
        return False

    # ------------------------------------------------------------------
    # Throughput gate (daily schedule — equal slots OR named windows)
    # ------------------------------------------------------------------
    def _midnight(self, now: float) -> float:
        local = self._clock.localtime(now)
        return self._clock.mktime(
            self._clock.struct_time(
                (
                    local.tm_year,
                    local.tm_mon,
                    local.tm_mday,
                    0,
                    0,
                    0,
                    0,
                    0,
                    local.tm_isdst,
                )
            )
        )

    def _rebuild_schedule(self, now: float) -> None:
        midnight = self._midnight(now)
        if self._windows:
            schedule = self._build_window_schedule(midnight, now)
            label = "windowed"
        else:
            schedule = self._build_equal_slot_schedule(midnight, now)
            label = "equal-slot"
        schedule.sort()
        self._throughput_schedule = schedule
        log.info(
            "Throughput schedule (%s): %s",
            label,
            self._format_schedule(schedule),
        )

    def _build_equal_slot_schedule(self, midnight: float, now: float) -> list[float]:
        n = self._throughput_n
        slot_size = 86400 / n
        schedule = []
        for i in range(n):
            slot_start = midnight + i * slot_size
            slot_end = slot_start + slot_size
            t = self._rng.uniform(slot_start, slot_end)
            if t > now:
                schedule.append(t)
        return schedule

    def _build_window_schedule(self, midnight: float, now: float) -> list[float]:
        schedule = []
        for start_h, end_h in self._windows or []:
            slot_start = midnight + start_h * 3600
            slot_end = midnight + end_h * 3600
            if slot_end <= now:
                # Window already past for today — skip.
                continue
            # If we're inside the window already, only schedule from `now` onward.
            effective_start = max(slot_start, now)
            t = self._rng.uniform(effective_start, slot_end)
            schedule.append(t)
        return schedule

    def _format_schedule(self, schedule: list[float]) -> str:
        if not schedule:
            return "(empty — all slots elapsed for today)"
        parts = []
        if self._windows:
            for (start_h, end_h), ts in zip(self._windows, schedule, strict=False):
                label = _window_label(start_h, end_h)
                parts.append(f"{label}={self._clock.strftime('%H:%M', self._clock.localtime(ts))}")
        else:
            parts = [self._clock.strftime("%H:%M", self._clock.localtime(t)) for t in schedule]
        return " ".join(parts) if self._windows else str(parts)

    def should_run_throughput(self, now: float) -> bool:
        today = self._clock.localtime(now).tm_yday
        if today != self._last_schedule_day:
            self._rebuild_schedule(now)
            self._last_schedule_day = today
        if self._throughput_schedule and now >= self._throughput_schedule[0]:
            self._throughput_schedule.pop(0)
            return True
        return False

    # ------------------------------------------------------------------
    # Heartbeat gate
    # ------------------------------------------------------------------
    def should_heartbeat(self, now: float) -> bool:
        if now - self._last_heartbeat_ts >= self._heartbeat_interval:
            self._last_heartbeat_ts = now
            return True
        return False
