"""
Scheduling helpers: daily throughput slots, HTTP latency interval, heartbeat cadence.
"""

import logging
import random as _random
import time as _time

import config

log = logging.getLogger("towerwatch")


class Scheduler:
    def __init__(
        self,
        *,
        http_latency_interval_s: int,
        http_throughput_tests_per_day: int,
        heartbeat_interval_s: int,
        clock=_time,
        rng=_random,
    ):
        self._latency_interval = http_latency_interval_s
        self._throughput_n = http_throughput_tests_per_day
        self._heartbeat_interval = heartbeat_interval_s
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
            http_throughput_tests_per_day=cfg.HTTP_THROUGHPUT_TESTS_PER_DAY,
            heartbeat_interval_s=cfg.HEARTBEAT_INTERVAL_S,
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
    # Throughput gate (daily random schedule)
    # ------------------------------------------------------------------
    def _rebuild_schedule(self, now: float) -> None:
        n = self._throughput_n
        local = self._clock.localtime(now)
        midnight = self._clock.mktime(self._clock.struct_time((
            local.tm_year, local.tm_mon, local.tm_mday,
            0, 0, 0, 0, 0, local.tm_isdst,
        )))
        slot_size = 86400 / n
        schedule = []
        for i in range(n):
            slot_start = midnight + i * slot_size
            slot_end = slot_start + slot_size
            t = self._rng.uniform(slot_start, slot_end)
            if t > now:
                schedule.append(t)
        schedule.sort()
        self._throughput_schedule = schedule
        log.info("Throughput schedule: %s",
                 [self._clock.strftime("%H:%M", self._clock.localtime(t))
                  for t in schedule])

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
