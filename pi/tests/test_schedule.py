"""Tests for scheduling.Scheduler — retargeted from towerwatch._build_daily_throughput_schedule."""
import sys
import time as time_mod
from pathlib import Path
from unittest.mock import patch

import pytest

_PI = Path(__file__).resolve().parents[1]
if str(_PI) not in sys.path:
    sys.path.insert(0, str(_PI))

from scheduling import Scheduler


def _make_scheduler(n=4, now=None):
    import config
    s = Scheduler(
        http_latency_interval_s=config.HTTP_LATENCY_INTERVAL_S,
        http_throughput_tests_per_day=n,
        heartbeat_interval_s=config.HEARTBEAT_INTERVAL_S,
    )
    if now is not None:
        s._rebuild_schedule(now)
        s._last_schedule_day = time_mod.localtime(now).tm_yday
    return s


# ---------------------------------------------------------------------------
# Throughput schedule basics
# ---------------------------------------------------------------------------
def test_schedule_length_at_most_n():
    now = time_mod.time()
    s = _make_scheduler(n=4, now=now)
    assert len(s._throughput_schedule) <= 4


def test_schedule_all_in_future():
    now = time_mod.time()
    s = _make_scheduler(n=4, now=now)
    for t in s._throughput_schedule:
        assert t > now


def test_schedule_is_sorted():
    now = time_mod.time()
    s = _make_scheduler(n=4, now=now)
    assert s._throughput_schedule == sorted(s._throughput_schedule)


def test_schedule_skips_past_due_slots():
    """Called at 23:59:59 — all 4 slots (each 6h wide) are past."""
    local = time_mod.localtime()
    midnight = time_mod.mktime(time_mod.struct_time((
        local.tm_year, local.tm_mon, local.tm_mday,
        0, 0, 0, 0, 0, local.tm_isdst,
    )))
    just_before_midnight = midnight + 86400 - 1
    s = _make_scheduler(n=4, now=just_before_midnight)
    assert len(s._throughput_schedule) == 0


def test_schedule_slot_count_config_respected():
    now = time_mod.time()
    s = _make_scheduler(n=2, now=now)
    assert len(s._throughput_schedule) <= 2


# ---------------------------------------------------------------------------
# Heartbeat cadence
# ---------------------------------------------------------------------------
def test_heartbeat_not_yet_due():
    import config
    s = Scheduler.from_config(config)
    s._last_heartbeat_ts = 0.0
    assert s.should_heartbeat(1.0) is False


def test_heartbeat_due_after_interval():
    import config
    s = Scheduler.from_config(config)
    s._last_heartbeat_ts = 0.0
    assert s.should_heartbeat(float(config.HEARTBEAT_INTERVAL_S + 1)) is True


# ---------------------------------------------------------------------------
# Day rollover rebuilds schedule
# ---------------------------------------------------------------------------
def test_day_rollover_rebuilds_schedule():
    """should_run_throughput triggers a schedule rebuild when tm_yday changes."""
    import config
    rng_calls = []

    class DeterministicRng:
        def uniform(self, a, b):
            rng_calls.append((a, b))
            return b - 1  # always near end of slot, always in future if now < b-1

    now = time_mod.time()
    s = Scheduler(
        http_latency_interval_s=config.HTTP_LATENCY_INTERVAL_S,
        http_throughput_tests_per_day=4,
        heartbeat_interval_s=config.HEARTBEAT_INTERVAL_S,
        rng=DeterministicRng(),
    )
    s._last_schedule_day = -1  # force rebuild on first call
    s.should_run_throughput(now)
    assert len(rng_calls) == 4  # one call per slot


# ---------------------------------------------------------------------------
# RNG injection — deterministic schedule
# ---------------------------------------------------------------------------
def test_rng_injection_controls_schedule():
    """With a fixed RNG, the schedule is deterministic."""
    import config

    class FixedRng:
        def uniform(self, a, b):
            return a + (b - a) / 2  # always midpoint

    now = time_mod.time()
    local = time_mod.localtime(now)
    midnight = time_mod.mktime(time_mod.struct_time((
        local.tm_year, local.tm_mon, local.tm_mday,
        0, 0, 0, 0, 0, local.tm_isdst,
    )))
    slot_size = 86400 / 4
    expected = sorted(
        midnight + i * slot_size + slot_size / 2
        for i in range(4)
        if midnight + i * slot_size + slot_size / 2 > now
    )

    s = Scheduler(
        http_latency_interval_s=300,
        http_throughput_tests_per_day=4,
        heartbeat_interval_s=3600,
        rng=FixedRng(),
    )
    s._rebuild_schedule(now)
    assert s._throughput_schedule == expected
