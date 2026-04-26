"""Network discovery helpers — pure stdlib, no subprocess.

`discover_default_gateway()` parses `/proc/net/route` to find the IPv4 of the
system's default gateway, so each Pi self-configures regardless of which subnet
its carrier router lives on. Falls back to a caller-supplied constant on
Windows / missing or unparseable file / no default route.
"""

from __future__ import annotations

import sys
from pathlib import Path

DEFAULT_PROC_ROUTE = "/proc/net/route"
RTF_GATEWAY = 0x2  # see <linux/route.h>


def _parse_proc_route(text: str) -> str | None:
    """Return the IPv4 of the default route in `/proc/net/route` text, or None.

    Format (tab-separated, first line is a header):
        Iface   Destination Gateway     Flags ...
        eth0    00000000    0101000A    0003  ...

    Destination "00000000" + RTF_GATEWAY flag = default route. The Gateway
    column is the IPv4 in little-endian hex (so "0101000A" is 10.0.1.1).
    """
    for line in text.splitlines()[1:]:
        cols = line.split()
        if len(cols) < 4:
            continue
        destination, gateway_hex, flags_hex = cols[1], cols[2], cols[3]
        if destination != "00000000":
            continue
        try:
            flags = int(flags_hex, 16)
        except ValueError:
            continue
        if not (flags & RTF_GATEWAY):
            continue
        try:
            raw = int(gateway_hex, 16)
        except ValueError:
            continue
        # Little-endian: low byte is first octet
        octets = (raw & 0xFF, (raw >> 8) & 0xFF, (raw >> 16) & 0xFF, (raw >> 24) & 0xFF)
        return ".".join(str(o) for o in octets)
    return None


def discover_default_gateway(
    fallback: str = "192.168.1.1",
    *,
    route_path: str | Path = DEFAULT_PROC_ROUTE,
    is_windows: bool | None = None,
) -> str:
    """Return the IPv4 of the default route, or `fallback` on any failure.

    Pure-stdlib so it can run at config import time without dragging in deps.
    `route_path` and `is_windows` are injectable for tests.
    """
    if is_windows is None:
        is_windows = sys.platform == "win32"
    if is_windows:
        return fallback
    try:
        text = Path(route_path).read_text(encoding="ascii", errors="replace")
    except OSError:
        return fallback
    parsed = _parse_proc_route(text)
    return parsed if parsed else fallback
