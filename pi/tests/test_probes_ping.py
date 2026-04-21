"""Characterization tests for probes/ping.py — 8 tests."""
import sys
from pathlib import Path
from unittest.mock import patch
import subprocess

import pytest

_FIXTURES = Path(__file__).parent / "fixtures"


def _read(name):
    return (_FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers to call the internal parser directly (no subprocess)
# ---------------------------------------------------------------------------
def _parse(txt, is_windows=False):
    import probes.ping as ping_mod
    orig = ping_mod.IS_WINDOWS
    ping_mod.IS_WINDOWS = is_windows
    try:
        return ping_mod._parse_ping_output(txt)
    finally:
        ping_mod.IS_WINDOWS = orig


# ---------------------------------------------------------------------------
# Linux — happy path
# ---------------------------------------------------------------------------
def test_linux_ok_rtt_avg():
    r = _parse(_read("ping_linux_ok.txt"), is_windows=False)
    assert r["rtt_avg"] == 12

def test_linux_ok_rtt_min_max():
    r = _parse(_read("ping_linux_ok.txt"), is_windows=False)
    assert r["rtt_min"] == 12   # round(11.500)
    assert r["rtt_max"] == 13

def test_linux_ok_pkt_loss_zero():
    r = _parse(_read("ping_linux_ok.txt"), is_windows=False)
    assert r["pkt_loss"] == 0
    assert r["connected"] is True

def test_linux_50pct_loss():
    r = _parse(_read("ping_linux_loss.txt"), is_windows=False)
    assert r["pkt_loss"] == 50
    assert r["connected"] is True   # some packets got through

def test_linux_100pct_loss_not_connected():
    txt = "10 packets transmitted, 0 received, 100% packet loss"
    r = _parse(txt, is_windows=False)
    assert r["pkt_loss"] == 100
    assert r["connected"] is False

# ---------------------------------------------------------------------------
# Windows — known bugs marked xfail (fixed in Pass 6)
# ---------------------------------------------------------------------------
def test_windows_subms_rtt_nonzero():
    """time<1ms replies should produce rtt_avg > 0, not 0."""
    r = _parse(_read("ping_windows_subms.txt"), is_windows=True)
    assert r["rtt_avg"] > 0

def test_windows_subms_mdev_nonzero():
    """When all RTTs are sub-ms, rtt_min/max should be 1 (floor of <1ms)."""
    r = _parse(_read("ping_windows_subms.txt"), is_windows=True)
    assert r["rtt_min"] > 0

def test_windows_ok_rtt():
    r = _parse(_read("ping_windows_ok.txt"), is_windows=True)
    assert r["rtt_avg"] == 12
    assert r["pkt_loss"] == 0
    assert r["connected"] is True
