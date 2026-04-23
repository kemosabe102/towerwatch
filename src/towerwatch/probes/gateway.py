"""Vendor-agnostic gateway health probe.

Baseline (always): TCP connect + HTTP response time to GATEWAY_IP.
M6 (GATEWAY_VENDOR="m6"): delegates to probes.m6.M6Probe for radio metrics.
Orbi (GATEWAY_VENDOR="orbi"): unauthenticated /api/DEV_INFO for connected client count.
"""

import logging
import socket
import xml.etree.ElementTree as ET

import requests

from towerwatch import config
from towerwatch.clock import Clock, SystemClock
from towerwatch.probes.base import ProbeResult

log = logging.getLogger("towerwatch")


def _default_socket_factory():
    return socket.socket(socket.AF_INET, socket.SOCK_STREAM)


class GatewayProbe:
    """Vendor-agnostic gateway probe."""

    name = "gateway"

    def __init__(
        self,
        vendor: str | None = None,
        ip: str | None = None,
        tcp_port: int | None = None,
        timeout_s: float | None = None,
        requests_get=None,
        socket_factory=_default_socket_factory,
        clock: Clock | None = None,
        m6_poll=None,
    ):
        self._vendor = vendor if vendor is not None else getattr(config, "GATEWAY_VENDOR", "")
        self._ip = ip if ip is not None else config.GATEWAY_IP
        self._tcp_port = tcp_port if tcp_port is not None else config.GATEWAY_TCP_PORT
        self._timeout_s = timeout_s if timeout_s is not None else config.GATEWAY_TIMEOUT_S
        self._requests_get = requests_get if requests_get is not None else requests.get
        self._socket_factory = socket_factory
        self._clock = clock if clock is not None else SystemClock()
        self._m6_poll = m6_poll  # if None, resolve lazily

    def _probe_baseline(self) -> dict:
        fields: dict = {}
        sock = self._socket_factory()
        sock.settimeout(self._timeout_s)
        try:
            t0 = self._clock.perf_counter()
            sock.connect((self._ip, self._tcp_port))
            fields["gateway_tcp_ms"] = round(
                (self._clock.perf_counter() - t0) * 1000,
                1,
            )
        except OSError:
            fields["gateway_tcp_ms"] = 0
        finally:
            sock.close()
        try:
            t0 = self._clock.perf_counter()
            self._requests_get(f"http://{self._ip}/", timeout=self._timeout_s)
            fields["gateway_http_ms"] = round(
                (self._clock.perf_counter() - t0) * 1000,
                1,
            )
        except Exception:
            fields["gateway_http_ms"] = 0
        return fields

    def _probe_orbi(self) -> dict:
        try:
            resp = self._requests_get(
                f"http://{self._ip}/api/DEV_INFO",
                timeout=self._timeout_s,
            )
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
            el = root.find(".//ConnectedDeviceCount")
            if el is not None and el.text is not None:
                return {"gateway_clients": int(el.text)}
        except Exception as e:
            log.debug("Orbi DEV_INFO failed: %s", e)
        return {}

    def poll(self) -> dict:
        fields = self._probe_baseline()
        if self._vendor == "m6":
            if self._m6_poll is None:
                from towerwatch.probes.m6 import poll_m6_signal

                self._m6_poll = poll_m6_signal
            fields.update(self._m6_poll())
        elif self._vendor == "orbi":
            fields.update(self._probe_orbi())
        return fields

    def run(self) -> ProbeResult:
        f = self.poll()
        return ProbeResult(fields=f, ok=f.get("gateway_tcp_ms", 0) > 0)


# ---------------------------------------------------------------------------
# Back-compat module-level function
# ---------------------------------------------------------------------------
def poll_gateway() -> dict:
    """Legacy API. Prefer `GatewayProbe().poll()`."""
    return GatewayProbe().poll()
