"""Tests for towerwatch.net.discover_default_gateway."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from towerwatch.net import _parse_proc_route, discover_default_gateway

if TYPE_CHECKING:
    from pathlib import Path

PROC_ROUTE_HEADER = (
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
)


def _route(*lines: str) -> str:
    return PROC_ROUTE_HEADER + "\n".join(lines) + ("\n" if lines else "")


# ---------------------------------------------------------------------------
# _parse_proc_route
# ---------------------------------------------------------------------------
def test_parse_default_route_little_endian():
    # 0101000A little-endian = 10.0.1.1
    text = _route("eth0\t00000000\t0101000A\t0003\t0\t0\t100\t00000000\t0\t0\t0")
    assert _parse_proc_route(text) == "10.0.1.1"


def test_parse_default_route_192_168_1_1():
    # 0101A8C0 little-endian = 192.168.1.1
    text = _route("eth0\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0")
    assert _parse_proc_route(text) == "192.168.1.1"


def test_parse_skips_non_default_routes():
    text = _route(
        "eth0\t0101000A\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0",  # subnet, no gw
        "eth0\t0000000A\t00000000\t0001\t0\t0\t100\t000000FF\t0\t0\t0",  # not default
    )
    assert _parse_proc_route(text) is None


def test_parse_skips_default_without_rtf_gateway_flag():
    # Flags=0001 means RTF_UP but not RTF_GATEWAY (0x2)
    text = _route("eth0\t00000000\t0101000A\t0001\t0\t0\t100\t00000000\t0\t0\t0")
    assert _parse_proc_route(text) is None


def test_parse_picks_first_default_when_multiple():
    text = _route(
        "eth0\t00000000\t0101000A\t0003\t0\t0\t100\t00000000\t0\t0\t0",
        "wlan0\t00000000\t0102000A\t0003\t0\t0\t600\t00000000\t0\t0\t0",
    )
    assert _parse_proc_route(text) == "10.0.1.1"


def test_parse_empty_returns_none():
    assert _parse_proc_route(PROC_ROUTE_HEADER) is None


def test_parse_malformed_hex_skipped():
    text = _route("eth0\t00000000\tNOTHEX!!\t0003\t0\t0\t100\t00000000\t0\t0\t0")
    assert _parse_proc_route(text) is None


# ---------------------------------------------------------------------------
# discover_default_gateway
# ---------------------------------------------------------------------------
def test_discover_returns_parsed_ip(tmp_path: Path):
    route_file = tmp_path / "route"
    route_file.write_text(
        _route("eth0\t00000000\t0101000A\t0003\t0\t0\t100\t00000000\t0\t0\t0"),
        encoding="ascii",
    )
    assert (
        discover_default_gateway(fallback="1.2.3.4", route_path=route_file, is_windows=False)
        == "10.0.1.1"
    )


def test_discover_returns_fallback_on_windows(tmp_path: Path):
    route_file = tmp_path / "route"
    route_file.write_text(
        _route("eth0\t00000000\t0101000A\t0003\t0\t0\t100\t00000000\t0\t0\t0"),
        encoding="ascii",
    )
    assert (
        discover_default_gateway(fallback="1.2.3.4", route_path=route_file, is_windows=True)
        == "1.2.3.4"
    )


def test_discover_returns_fallback_when_file_missing(tmp_path: Path):
    missing = tmp_path / "does-not-exist"
    assert (
        discover_default_gateway(fallback="9.9.9.9", route_path=missing, is_windows=False)
        == "9.9.9.9"
    )


def test_discover_returns_fallback_when_no_default_route(tmp_path: Path):
    route_file = tmp_path / "route"
    route_file.write_text(PROC_ROUTE_HEADER, encoding="ascii")
    assert (
        discover_default_gateway(fallback="9.9.9.9", route_path=route_file, is_windows=False)
        == "9.9.9.9"
    )


@pytest.mark.parametrize(
    "hex_be, expected",
    [
        ("0101000A", "10.0.1.1"),  # little-endian → 10.0.1.1
        ("0101A8C0", "192.168.1.1"),  # → 192.168.1.1
        ("FE01000A", "10.0.1.254"),  # → 10.0.1.254
    ],
)
def test_parse_endianness(hex_be: str, expected: str):
    text = _route(f"eth0\t00000000\t{hex_be}\t0003\t0\t0\t100\t00000000\t0\t0\t0")
    assert _parse_proc_route(text) == expected
