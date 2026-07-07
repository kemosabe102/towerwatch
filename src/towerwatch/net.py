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

# Re-resolve discovery at most this often (seconds). The gateway rarely changes,
# so a slow cadence is fine; the point is to heal a boot-race freeze or a DHCP
# renewal without a process restart, not to poll aggressively.
DEFAULT_RERESOLVE_INTERVAL_S = 300


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


class GatewayResolver:
    """Holds the current gateway IP and re-resolves it on a cadence.

    Retires the "frozen gateway IP" bug class: `config.GATEWAY_IP` was resolved
    once at import, so a boot race (config imported before DHCP installed the
    default route) froze it on the fallback with no way to heal short of a
    restart. This resolver re-resolves periodically and reports when the value
    changes, so a DHCP renewal / router reboot heals live and the change is
    observable (build_info label + Loki event).

    An `override` (from `credentials.GATEWAY_IP_OVERRIDE`) is authoritative and
    short-circuits discovery entirely — there is nothing to re-resolve.
    """

    def __init__(
        self,
        override: str | None,
        fallback: str = "192.168.1.1",
        *,
        route_path: str | Path = DEFAULT_PROC_ROUTE,
        is_windows: bool | None = None,
        reresolve_interval_s: float = DEFAULT_RERESOLVE_INTERVAL_S,
        clock=None,
    ):
        self._override = override
        self._fallback = fallback
        self._route_path = route_path
        self._is_windows = is_windows
        self._interval_s = reresolve_interval_s
        # monotonic clock, injectable for tests
        if clock is None:
            import time

            clock = time.monotonic
        self._clock = clock
        self._current: str | None = None
        self._last_resolve_ts: float | None = None

    def _resolve(self) -> str:
        if self._override:
            return self._override
        return discover_default_gateway(
            fallback=self._fallback,
            route_path=self._route_path,
            is_windows=self._is_windows,
        )

    def current(self) -> str:
        """Return the current gateway IP, resolving on first call."""
        if self._current is None:
            self._current = self._resolve()
            self._last_resolve_ts = self._clock()
        return self._current

    def resolve_with_retry(self, *, attempts: int = 5, delay_s: float = 2.0, sleeper=None) -> str:
        """Resolve at startup, retrying if discovery hits the fallback.

        The original bug was a boot race: config was imported before DHCP
        installed the default route, so discovery returned the fallback and
        froze there. Retrying a few times at startup gives DHCP time to finish,
        so the process starts on the *real* gateway instead of the fallback.

        With an override set there is nothing to retry — returns it immediately.
        If the route never appears, returns the fallback after `attempts` tries
        (never hangs). `sleeper` is injectable for tests.
        """
        if self._override:
            self._current = self._override
            self._last_resolve_ts = self._clock()
            return self._override
        if sleeper is None:
            import time

            sleeper = time.sleep
        ip = self._fallback
        for i in range(attempts):
            ip = discover_default_gateway(
                fallback=self._fallback,
                route_path=self._route_path,
                is_windows=self._is_windows,
            )
            if ip != self._fallback:
                break
            if i < attempts - 1:
                sleeper(delay_s)
        self._current = ip
        self._last_resolve_ts = self._clock()
        return ip

    def maybe_reresolve(self) -> str | None:
        """Re-resolve if the interval has elapsed; return the new IP iff it changed.

        Returns None when: an override is set (nothing to re-resolve), the
        interval hasn't elapsed yet, or the re-resolved IP matches the current
        one. A non-None return is the signal to update the build_info label and
        emit a change event.
        """
        if self._override:
            return None
        now = self._clock()
        # current() may not have been called yet; anchor the timer on first use.
        if self._current is None:
            self.current()
            return None
        assert self._last_resolve_ts is not None
        if now - self._last_resolve_ts < self._interval_s:
            return None
        self._last_resolve_ts = now
        new_ip = self._resolve()
        if new_ip == self._current:
            return None
        self._current = new_ip
        return new_ip
