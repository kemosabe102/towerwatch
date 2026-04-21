"""Characterization tests for _build_daily_throughput_schedule — 5 tests."""
import time
from unittest.mock import patch

import pytest


def _build_schedule(now=None, n=4):
    import towerwatch, config
    with patch.object(config, "HTTP_THROUGHPUT_TESTS_PER_DAY", n):
        with patch("towerwatch.time.time", return_value=now or time.time()):
            with patch("towerwatch.time.localtime", side_effect=time.localtime):
                with patch("towerwatch.time.mktime", side_effect=time.mktime):
                    return towerwatch._build_daily_throughput_schedule()


def test_schedule_length_at_most_n():
    """Schedule has at most HTTP_THROUGHPUT_TESTS_PER_DAY entries."""
    sched = _build_schedule(n=4)
    assert len(sched) <= 4

def test_schedule_all_in_future():
    """All scheduled times must be in the future relative to now."""
    import time as time_mod
    now = time_mod.time()
    sched = _build_schedule(now=now, n=4)
    for t in sched:
        assert t > now

def test_schedule_is_sorted():
    sched = _build_schedule(n=4)
    assert sched == sorted(sched)

def test_schedule_skips_past_due_slots():
    """If called at 23:59:59, all 4 slots (each 6h wide, last ending at midnight) are past."""
    import time as time_mod
    local = time_mod.localtime()
    midnight = time_mod.mktime(time_mod.struct_time((
        local.tm_year, local.tm_mon, local.tm_mday,
        0, 0, 0, 0, 0, local.tm_isdst,
    )))
    # One second before midnight — every possible random slot time is in the past
    just_before_midnight = midnight + 86400 - 1
    sched = _build_schedule(now=just_before_midnight, n=4)
    assert len(sched) == 0

def test_schedule_slot_count_config_respected():
    """HTTP_THROUGHPUT_TESTS_PER_DAY=2 yields at most 2 slots."""
    sched = _build_schedule(n=2)
    assert len(sched) <= 2

def test_heartbeat_cadence_config(monkeypatch):
    """_maybe_heartbeat emits only when interval has elapsed."""
    import towerwatch, config

    state = towerwatch.RuntimeState()
    state.last_heartbeat_ts = 0.0

    posted = []
    with patch("towerwatch.push_log", side_effect=lambda *a, **kw: posted.append(a)):
        with patch("towerwatch.time.time", return_value=1.0):
            towerwatch._maybe_heartbeat(state)
    # 1s elapsed is less than HEARTBEAT_INTERVAL_S (3600) — no heartbeat
    assert len(posted) == 0

    # Now advance past interval
    with patch("towerwatch.push_log", side_effect=lambda *a, **kw: posted.append(a)):
        with patch("towerwatch.time.time", return_value=float(config.HEARTBEAT_INTERVAL_S + 1)):
            with patch("towerwatch.time.monotonic", return_value=0.0):
                towerwatch._maybe_heartbeat(state)
    assert len(posted) == 1
