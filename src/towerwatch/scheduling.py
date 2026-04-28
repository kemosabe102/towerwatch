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
    """Human label for a (start_hour, end_hour) window, used in startup logs.
    Matches the README's morning/midday/evening guidance for cellular sites."""
    if start < 6:
        return "early"
    if start < 11:
        return "morning"
    if start < 15:
        return "midday"
    if start < 17:
        return "afternoon"
    if start < 22:
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
            paired = self._build_window_schedule(midnight, now)
            paired.sort(key=lambda p: p[1])
            schedule = [ts for _, ts in paired]
            log_repr = self._format_windowed(paired)
            mode = "windowed"
        else:
            schedule = self._build_equal_slot_schedule(midnight, now)
            schedule.sort()
            log_repr = self._format_equal_slot(schedule)
            mode = "equal-slot"
        self._throughput_schedule = schedule
        log.info("Throughput schedule (%s): %s", mode, log_repr)

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

    def _build_window_schedule(
        self, midnight: float, now: float
    ) -> list[tuple[tuple[int, int], float]]:
        """Returns (window, scheduled_time) pairs for non-elapsed windows.
        The pairing preserves which window each slot belongs to so logs can
        label them correctly even after some windows have been pruned."""
        scheduled: list[tuple[tuple[int, int], float]] = []
        for start_h, end_h in self._windows or []:
            slot_start = midnight + start_h * 3600
            slot_end = midnight + end_h * 3600
            if slot_end <= now:
                continue
            effective_start = max(slot_start, now)
            t = self._rng.uniform(effective_start, slot_end)
            scheduled.append(((start_h, end_h), t))
        return scheduled

    def _format_windowed(self, paired: list[tuple[tuple[int, int], float]]) -> str:
        if not paired:
            return "(empty — all windows elapsed for today)"
        parts = []
        for (start_h, end_h), ts in paired:
            label = _window_label(start_h, end_h)
            parts.append(f"{label}={self._clock.strftime('%H:%M', self._clock.localtime(ts))}")
        return " ".join(parts)

    def _format_equal_slot(self, schedule: list[float]) -> str:
        if not schedule:
            return "(empty — all slots elapsed for today)"
        return str([self._clock.strftime("%H:%M", self._clock.localtime(t)) for t in schedule])

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
