"""Tests for PingProbe and ping parsers — no patch, fakes injected directly."""
import subprocess
import sys
from pathlib import Path

_PI = Path(__file__).resolve().parents[1]
if str(_PI) not in sys.path:
    sys.path.insert(0, str(_PI))

from tests.fakes import FakeCompletedProcess, FakeLoki, FakeSubprocess

_FIXTURES = Path(__file__).parent / "fixtures"


def _read(name):
    return (_FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Pure parser — no probe instance needed
# ---------------------------------------------------------------------------
def _parse(txt, is_windows=False):
    import probes.ping as ping_mod
    return ping_mod._parse_ping_output(txt, is_windows=is_windows)


# Linux
def test_linux_ok_rtt_avg():
    r = _parse(_read("ping_linux_ok.txt"), is_windows=False)
    assert r["rtt_avg"] == 12

def test_linux_ok_rtt_min_max():
    r = _parse(_read("ping_linux_ok.txt"), is_windows=False)
    assert r["rtt_min"] == 12
    assert r["rtt_max"] == 13

def test_linux_ok_pkt_loss_zero():
    r = _parse(_read("ping_linux_ok.txt"), is_windows=False)
    assert r["pkt_loss"] == 0
    assert r["connected"] is True

def test_linux_50pct_loss():
    r = _parse(_read("ping_linux_loss.txt"), is_windows=False)
    assert r["pkt_loss"] == 50
    assert r["connected"] is True

def test_linux_100pct_loss_not_connected():
    r = _parse("10 packets transmitted, 0 received, 100% packet loss", is_windows=False)
    assert r["pkt_loss"] == 100
    assert r["connected"] is False


# Windows
def test_windows_subms_rtt_nonzero():
    r = _parse(_read("ping_windows_subms.txt"), is_windows=True)
    assert r["rtt_avg"] > 0

def test_windows_subms_mdev_nonzero():
    r = _parse(_read("ping_windows_subms.txt"), is_windows=True)
    assert r["rtt_min"] > 0

def test_windows_ok_rtt():
    r = _parse(_read("ping_windows_ok.txt"), is_windows=True)
    assert r["rtt_avg"] == 12
    assert r["pkt_loss"] == 0
    assert r["connected"] is True


def test_windows_partial_loss():
    txt = (
        "Pinging 8.8.8.8 with 32 bytes of data:\n"
        "Reply from 8.8.8.8: bytes=32 time=12ms TTL=117\n"
        "Reply from 8.8.8.8: bytes=32 time=11ms TTL=117\n"
        "Reply from 8.8.8.8: bytes=32 time=13ms TTL=117\n"
        "Request timed out.\n"
        "\n"
        "Ping statistics for 8.8.8.8:\n"
        "    Packets: Sent = 10, Received = 7, Lost = 3 (30% loss),\n"
        "Approximate round trip times in milli-seconds:\n"
        "    Minimum = 11ms, Maximum = 13ms, Average = 12ms\n"
    )
    r = _parse(txt, is_windows=True)
    assert r["pkt_loss"] == 30
    assert r["connected"] is True


def test_malformed_rtt_summary_returns_zeros_and_100pct_loss():
    r = _parse("no useful data here", is_windows=False)
    assert r["rtt_avg"] == 0
    assert r["pkt_loss"] == 100
    assert r["connected"] is False


# ---------------------------------------------------------------------------
# _calc_jitter edge cases (pure)
# ---------------------------------------------------------------------------
def test_calc_jitter_zero_rtts_falls_back_to_mdev():
    import probes.ping as ping_mod
    assert ping_mod._calc_jitter([], mdev=5.5) == 6

def test_calc_jitter_one_rtt_falls_back_to_mdev():
    import probes.ping as ping_mod
    assert ping_mod._calc_jitter([12.0], mdev=3.0) == 3

def test_calc_jitter_two_rtts_uses_diff():
    import probes.ping as ping_mod
    assert ping_mod._calc_jitter([10.0, 15.0], mdev=99.0) == 5


# ---------------------------------------------------------------------------
# Probe (with injected subprocess)
# ---------------------------------------------------------------------------
def _make_probe(outcomes, is_windows=False, loki=None):
    from probes.ping import PingProbe
    return PingProbe(
        "8.8.8.8", "google",
        subprocess_run=FakeSubprocess(*outcomes),
        loki=loki or FakeLoki(),
        count=10,
        timeout_s=1,
        is_windows=is_windows,
    )


def test_probe_timeout_returns_zeros():
    probe = _make_probe([subprocess.TimeoutExpired("ping", 15)])
    result = probe.run_ping()
    assert result["connected"] is False
    assert result["pkt_loss"] == 100
    assert result["rtt_avg"] == 0


def test_probe_oserror_returns_zeros():
    probe = _make_probe([OSError("No such file or directory")])
    result = probe.run_ping()
    assert result["connected"] is False
    assert result["rtt_avg"] == 0


def test_probe_timeout_emits_ping_failed_event():
    loki = FakeLoki()
    probe = _make_probe([subprocess.TimeoutExpired("ping", 15)], loki=loki)
    probe.run_ping()
    assert any(lp[2].get("event") == "ping_failed" for lp in loki.log_and_pushes)


def test_probe_happy_run_returns_parsed():
    stdout = _read("ping_linux_ok.txt")
    probe = _make_probe([FakeCompletedProcess(stdout=stdout, returncode=0)])
    result = probe.run_ping()
    assert result["rtt_avg"] == 12
    assert result["connected"] is True


def test_probe_run_emits_labeled_fields():
    stdout = _read("ping_linux_ok.txt")
    probe = _make_probe([FakeCompletedProcess(stdout=stdout, returncode=0)])
    r = probe.run()
    assert "rtt_avg_google" in r.fields
    assert "connected_google" in r.fields
    assert r.ok is True
