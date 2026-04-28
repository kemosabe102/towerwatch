"""Tests for scheduling.Scheduler — retargeted from towerwatch._build_daily_throughput_schedule."""

import time as time_mod

from towerwatch.scheduling import Scheduler


def _make_scheduler(n=4, now=None):
    from towerwatch import config

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
    midnight = time_mod.mktime(
        time_mod.struct_time(
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
    from towerwatch import config

    s = Scheduler.from_config(config)
    s._last_heartbeat_ts = 0.0
    assert s.should_heartbeat(1.0) is False


def test_heartbeat_due_after_interval():
    from towerwatch import config

    s = Scheduler.from_config(config)
    s._last_heartbeat_ts = 0.0
    assert s.should_heartbeat(float(config.HEARTBEAT_INTERVAL_S + 1)) is True


# ---------------------------------------------------------------------------
# Day rollover rebuilds schedule
# ---------------------------------------------------------------------------
def test_day_rollover_rebuilds_schedule():
    """should_run_throughput triggers a schedule rebuild when tm_yday changes."""
    from towerwatch import config

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

    class FixedRng:
        def uniform(self, a, b):
            return a + (b - a) / 2  # always midpoint

    now = time_mod.time()
    local = time_mod.localtime(now)
    midnight = time_mod.mktime(
        time_mod.struct_time(
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


# ---------------------------------------------------------------------------
# Named-window mode
# ---------------------------------------------------------------------------
def _midnight_today() -> float:
    local = time_mod.localtime()
    return time_mod.mktime(
        time_mod.struct_time(
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


def _make_windowed_scheduler(windows, now):
    s = Scheduler(
        http_latency_interval_s=300,
        http_throughput_tests_per_day=len(windows),
        heartbeat_interval_s=3600,
        throughput_windows=windows,
    )
    s._rebuild_schedule(now)
    s._last_schedule_day = time_mod.localtime(now).tm_yday
    return s


def test_window_mode_produces_one_slot_per_window():
    """3 windows → 3 scheduled slots, when called at the start of the day."""
    midnight = _midnight_today()
    s = _make_windowed_scheduler(windows=[(6, 10), (11, 14), (17, 21)], now=midnight + 1)
    assert len(s._throughput_schedule) == 3


def test_window_mode_each_slot_inside_its_window():
    """Each scheduled slot falls inside its named window."""
    midnight = _midnight_today()
    windows = [(6, 10), (11, 14), (17, 21)]
    s = _make_windowed_scheduler(windows=windows, now=midnight + 1)
    for (start_h, end_h), ts in zip(windows, s._throughput_schedule, strict=True):
        slot_start = midnight + start_h * 3600
        slot_end = midnight + end_h * 3600
        assert slot_start <= ts < slot_end


def test_window_mode_skips_already_elapsed_windows():
    """Service started at 18:00 — morning and midday windows are past, only evening fires."""
    midnight = _midnight_today()
    six_pm = midnight + 18 * 3600
    s = _make_windowed_scheduler(windows=[(6, 10), (11, 14), (17, 21)], now=six_pm)
    # Evening window is 17-21; we're at 18 - so 1 slot, in (18, 21).
    assert len(s._throughput_schedule) == 1
    ts = s._throughput_schedule[0]
    assert six_pm <= ts < midnight + 21 * 3600


def test_window_mode_partially_elapsed_window_starts_from_now():
    """Service started in the middle of a window — slot is between now and window end."""
    midnight = _midnight_today()
    twelve_thirty = midnight + 12 * 3600 + 1800
    s = _make_windowed_scheduler(windows=[(11, 14)], now=twelve_thirty)
    assert len(s._throughput_schedule) == 1
    ts = s._throughput_schedule[0]
    assert twelve_thirty <= ts < midnight + 14 * 3600


def test_window_mode_no_remaining_windows_yields_empty_schedule():
    """All windows past → empty schedule for today."""
    midnight = _midnight_today()
    eleven_pm = midnight + 23 * 3600
    s = _make_windowed_scheduler(windows=[(6, 10), (11, 14), (17, 21)], now=eleven_pm)
    assert s._throughput_schedule == []


def test_default_mode_preserved_when_windows_unset():
    """When throughput_windows is None, equal-slot logic still works."""
    now = time_mod.time()
    s = Scheduler(
        http_latency_interval_s=300,
        http_throughput_tests_per_day=4,
        heartbeat_interval_s=3600,
        throughput_windows=None,
    )
    s._rebuild_schedule(now)
    # 4 equal slots of 6h, only future ones kept; expect ≤4.
    assert len(s._throughput_schedule) <= 4
    assert all(t > now for t in s._throughput_schedule)


def test_window_mode_uses_rng_for_each_window():
    """RNG is called once per non-elapsed window."""
    rng_calls = []

    class TrackingRng:
        def uniform(self, a, b):
            rng_calls.append((a, b))
            return (a + b) / 2

    midnight = _midnight_today()
    s = Scheduler(
        http_latency_interval_s=300,
        http_throughput_tests_per_day=3,
        heartbeat_interval_s=3600,
        throughput_windows=[(6, 10), (11, 14), (17, 21)],
        rng=TrackingRng(),
    )
    s._rebuild_schedule(midnight + 1)
    assert len(rng_calls) == 3


def test_window_mode_log_label_pairs_with_correct_window():
    """Regression: when windows are pruned (some already elapsed), the log line
    must still label each scheduled slot with the window it actually belongs to,
    not the first window in the configured list."""
    midnight = _midnight_today()
    six_pm = midnight + 18 * 3600

    class FixedRng:
        def uniform(self, a, b):
            # Always return mid-window so the assertion about which window the
            # slot belongs to is unambiguous.
            return (a + b) / 2

    s = Scheduler(
        http_latency_interval_s=300,
        http_throughput_tests_per_day=3,
        heartbeat_interval_s=3600,
        throughput_windows=[(6, 10), (11, 14), (17, 21)],
        rng=FixedRng(),
    )
    paired = s._build_window_schedule(midnight, six_pm)
    # Only the evening window survives; pair must reflect (17, 21), not (6, 10).
    assert len(paired) == 1
    (start_h, end_h), ts = paired[0]
    assert (start_h, end_h) == (17, 21)
    assert midnight + 17 * 3600 <= ts <= midnight + 21 * 3600
    # Format string must use the evening label, not morning.
    formatted = s._format_windowed(paired)
    assert formatted.startswith("evening=")
    assert "morning" not in formatted
    assert "midday" not in formatted
