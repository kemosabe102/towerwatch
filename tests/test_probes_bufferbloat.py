"""Tests for the bufferbloat (latency-under-load) probe.

The threaded sampling loop is not exercised directly (it would be timing-
flaky); instead the testable building blocks — single-ping parse, baseline
burst, loaded-median — are tested with injected fakes, and the coordinator is
tested with a hand-written FakeSampler and fake throughput functions.
"""

from tests.fakes import FakeCompletedProcess, FakeLoki, FakeSubprocess

_PING_1_OK = """\
PING 8.8.8.8 (8.8.8.8) 56(84) bytes of data.
64 bytes from 8.8.8.8: icmp_seq=1 ttl=117 time=14.2 ms

--- 8.8.8.8 ping statistics ---
1 packets transmitted, 1 received, 0% packet loss, time 0ms
rtt min/avg/max/mdev = 14.200/14.200/14.200/0.000 ms
"""

_PING_TIMEOUT = """\
PING 8.8.8.8 (8.8.8.8) 56(84) bytes of data.

--- 8.8.8.8 ping statistics ---
1 packets transmitted, 0 received, 100% packet loss, time 0ms
"""

_PING_BASELINE = """\
PING 8.8.8.8 (8.8.8.8) 56(84) bytes of data.
64 bytes from 8.8.8.8: icmp_seq=1 ttl=117 time=18.0 ms
64 bytes from 8.8.8.8: icmp_seq=2 ttl=117 time=22.0 ms

--- 8.8.8.8 ping statistics ---
2 packets transmitted, 2 received, 0% packet loss, time 1ms
rtt min/avg/max/mdev = 18.000/20.000/22.000/2.000 ms
"""


def _sampler(*outcomes, **kw):
    from towerwatch.probes.bufferbloat import LatencyUnderLoadSampler

    return LatencyUnderLoadSampler(
        target="8.8.8.8",
        subprocess_run=FakeSubprocess(*outcomes),
        loki=FakeLoki(),
        is_windows=False,
        **kw,
    )


# ---------------------------------------------------------------------------
# Single-ping building block
# ---------------------------------------------------------------------------
def test_single_ping_returns_rtt():
    s = _sampler(FakeCompletedProcess(stdout=_PING_1_OK))
    assert s._single_ping_ms() == 14


def test_single_ping_timeout_returns_none():
    s = _sampler(FakeCompletedProcess(stdout=_PING_TIMEOUT))
    assert s._single_ping_ms() is None


def test_single_ping_subprocess_failure_returns_none():
    import subprocess

    s = _sampler(subprocess.TimeoutExpired(cmd="ping", timeout=2))
    assert s._single_ping_ms() is None


# ---------------------------------------------------------------------------
# Baseline burst
# ---------------------------------------------------------------------------
def test_baseline_ms_returns_avg():
    s = _sampler(FakeCompletedProcess(stdout=_PING_BASELINE), baseline_count=2)
    assert s.baseline_ms() == 20


def test_baseline_ms_failure_returns_none():
    s = _sampler(FakeCompletedProcess(stdout=_PING_TIMEOUT), baseline_count=2)
    assert s.baseline_ms() is None


# ---------------------------------------------------------------------------
# Loaded median (samples recorded one at a time, as the thread would)
# ---------------------------------------------------------------------------
def test_loaded_median_over_recorded_samples():
    s = _sampler(
        FakeCompletedProcess(stdout=_PING_1_OK.replace("14.2", "30.0")),
        FakeCompletedProcess(stdout=_PING_1_OK.replace("14.2", "50.0")),
        FakeCompletedProcess(stdout=_PING_1_OK.replace("14.2", "40.0")),
    )
    s._record_loaded_sample()
    s._record_loaded_sample()
    s._record_loaded_sample()
    assert s.loaded_median_ms() == 40


def test_loaded_median_skips_failed_samples():
    s = _sampler(
        FakeCompletedProcess(stdout=_PING_TIMEOUT),
        FakeCompletedProcess(stdout=_PING_1_OK.replace("14.2", "25.0")),
    )
    s._record_loaded_sample()  # failed → not recorded
    s._record_loaded_sample()  # 25
    assert s.loaded_median_ms() == 25


def test_loaded_median_empty_returns_none():
    s = _sampler()
    assert s.loaded_median_ms() is None


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------
class _FakeSampler:
    def __init__(self, baseline, dl_loaded, ul_loaded):
        self._baseline = baseline
        self._loaded_queue = [dl_loaded, ul_loaded]
        self.events: list[str] = []

    def baseline_ms(self):
        self.events.append("baseline")
        return self._baseline

    def start(self):
        self.events.append("start")

    def stop(self):
        self.events.append("stop")

    def reset(self):
        self.events.append("reset")

    def loaded_median_ms(self):
        return self._loaded_queue.pop(0)


def test_coordinator_merges_throughput_and_computes_deltas():
    from towerwatch.probes.bufferbloat import measure_throughput_with_bufferbloat

    sampler = _FakeSampler(baseline=20, dl_loaded=80, ul_loaded=120)
    fields = measure_throughput_with_bufferbloat(
        sampler=sampler,
        download_fn=lambda: {"http_throughput_mbps": 100, "http_throughput_ms": 5000},
        upload_fn=lambda: {"http_upload_mbps": 20, "http_upload_ms": 5000},
    )

    # Throughput fields are passed through untouched.
    assert fields["http_throughput_mbps"] == 100
    assert fields["http_upload_mbps"] == 20

    # Bufferbloat fields.
    assert fields["bufferbloat_rtt_idle_ms"] == 20
    assert fields["bufferbloat_rtt_download_ms"] == 80
    assert fields["bufferbloat_rtt_upload_ms"] == 120
    assert fields["bufferbloat_download_delta_ms"] == 60
    assert fields["bufferbloat_upload_delta_ms"] == 100

    # Sampler started/stopped once per direction; baseline taken before load.
    assert sampler.events == [
        "baseline",
        "start",
        "stop",
        "reset",
        "start",
        "stop",
    ]


def test_coordinator_omits_deltas_when_baseline_fails():
    from towerwatch.probes.bufferbloat import measure_throughput_with_bufferbloat

    sampler = _FakeSampler(baseline=None, dl_loaded=80, ul_loaded=None)
    fields = measure_throughput_with_bufferbloat(
        sampler=sampler,
        download_fn=lambda: {"http_throughput_mbps": 100},
        upload_fn=lambda: {"http_upload_mbps": 20},
    )
    assert "bufferbloat_rtt_idle_ms" not in fields
    assert "bufferbloat_download_delta_ms" not in fields
    assert "bufferbloat_upload_delta_ms" not in fields
    # Loaded download RTT still reported even without a baseline to diff against.
    assert fields["bufferbloat_rtt_download_ms"] == 80
    assert "bufferbloat_rtt_upload_ms" not in fields
