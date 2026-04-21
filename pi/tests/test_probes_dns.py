"""Characterization tests for probes/dns.py — 5 tests."""
import sys
from unittest.mock import patch, MagicMock
import time

import pytest


def _measure_dns_patched(ns, side_effect=None, resolve_return=None):
    """Call measure_dns with a mocked resolver."""
    import probes.dns as dns_mod

    mock_resolver = MagicMock()
    if side_effect:
        mock_resolver.resolve.side_effect = side_effect
    else:
        mock_resolver.resolve.return_value = resolve_return or MagicMock()

    with patch("probes.dns.dns.resolver.Resolver", return_value=mock_resolver):
        with patch("probes.dns.time.perf_counter", side_effect=[0.0, 0.025]):
            return dns_mod.measure_dns(ns)


def test_dns_success_returns_ms():
    result = _measure_dns_patched("8.8.8.8")
    assert result == 25   # round(0.025 * 1000)

def test_dns_nxdomain_returns_zero():
    import dns.resolver
    result = _measure_dns_patched("8.8.8.8", side_effect=dns.resolver.NXDOMAIN())
    assert result == 0

def test_dns_servfail_returns_zero():
    import dns.resolver
    result = _measure_dns_patched("8.8.8.8", side_effect=dns.resolver.NoAnswer())
    assert result == 0

def test_dns_timeout_returns_zero():
    import dns.resolver
    result = _measure_dns_patched("8.8.8.8", side_effect=dns.resolver.Timeout())
    assert result == 0

def test_dns_sentinel_is_zero():
    """Lock the current sentinel value (0.0 / 0). Pass 6 may change to None."""
    import dns.resolver
    result = _measure_dns_patched("1.1.1.1", side_effect=Exception("generic"))
    assert result == 0
