"""Egress-IP / failover probe.

GETs Cloudflare's `/cdn-cgi/trace` (tiny `key=value` text) and reads the egress
IP + Cloudflare colo. The egress IP is the public address *as Cloudflare sees
it* — the post-NAT public IP. On a near-static cable line, a *change* in this IP
is the reliable LTE-failover signal (the change-event lives in tick.py); this
probe just reports the current reading.

`egress_cgnat` (via net.is_cgnat) is a near-free secondary field, NOT the failover
detector — see net.is_cgnat's docstring and docs/ullrich-gateway-ubc1340.md.

Returns {} on any failure or a body without an `ip=` line, so a transient miss
never fabricates a change.
"""

import logging

import requests

from towerwatch import config
from towerwatch.net import is_cgnat

log = logging.getLogger("towerwatch")


def _parse_trace(text: str) -> dict:
    """Parse Cloudflare /cdn-cgi/trace `key=value\\n` lines into a dict."""
    out: dict = {}
    for line in text.splitlines():
        key, sep, val = line.partition("=")
        if sep:
            out[key.strip()] = val.strip()
    return out


class EgressProbe:
    """Fetch the public egress IP + colo from Cloudflare's trace endpoint."""

    name = "egress"

    def __init__(
        self,
        session=None,
        url: str | None = None,
        timeout_s: int | None = None,
    ):
        self._session = session if session is not None else requests.Session()
        self._url = url if url is not None else config.EGRESS_IP_URL
        self._timeout_s = timeout_s if timeout_s is not None else config.EGRESS_IP_TIMEOUT_S

    def measure(self) -> dict:
        """Return {egress_ip, egress_colo, egress_cgnat} or {} on failure.

        {} (not a sentinel row) on any error or a body missing `ip=`, so the
        caller treats it as "no reading" rather than "IP changed".
        """
        try:
            resp = self._session.get(self._url, timeout=self._timeout_s)
            resp.raise_for_status()
            parsed = _parse_trace(resp.text)
        except Exception as e:
            log.warning("Egress probe failed: %s", e)
            return {}
        ip = parsed.get("ip")
        if not ip:
            log.warning("Egress probe: no ip= in trace body")
            return {}
        return {
            "egress_ip": ip,
            "egress_colo": parsed.get("colo", ""),
            "egress_cgnat": 1 if is_cgnat(ip) else 0,
        }


_shared_egress_probe: EgressProbe | None = None


def measure_egress() -> dict:
    """Back-compat module-level probe. Prefer `EgressProbe().measure()`."""
    global _shared_egress_probe
    if _shared_egress_probe is None:
        _shared_egress_probe = EgressProbe()
    return _shared_egress_probe.measure()
