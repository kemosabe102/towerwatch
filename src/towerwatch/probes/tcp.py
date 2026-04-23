"""TCP connection time probe."""

import logging
import socket

from towerwatch import config
from towerwatch.clock import Clock, SystemClock
from towerwatch.probes.base import Probe, ProbeResult

log = logging.getLogger("towerwatch")


def _default_socket_factory() -> socket.socket:
    return socket.socket(socket.AF_INET, socket.SOCK_STREAM)


class TCPProbe:
    """Measure TCP handshake time in ms."""

    name = "tcp"

    def __init__(
        self,
        socket_factory=_default_socket_factory,
        clock: Clock | None = None,
        host: str | None = None,
        port: int | None = None,
        timeout_s: float | None = None,
    ):
        self._socket_factory = socket_factory
        self._clock = clock if clock is not None else SystemClock()
        self._host = host if host is not None else config.TCP_TARGET_HOST
        self._port = port if port is not None else config.TCP_TARGET_PORT
        self._timeout_s = timeout_s if timeout_s is not None else config.TCP_TIMEOUT_S

    def measure(self) -> int:
        sock = self._socket_factory()
        sock.settimeout(self._timeout_s)
        try:
            start = self._clock.perf_counter()
            sock.connect((self._host, self._port))
            return round((self._clock.perf_counter() - start) * 1000)
        except (OSError, socket.timeout) as e:
            log.warning("TCP connect failed: %s", e)
            return 0
        finally:
            sock.close()

    def run(self) -> ProbeResult:
        ms = self.measure()
        return ProbeResult(fields={"tcp_connect_ms": ms}, ok=ms > 0)


# ---------------------------------------------------------------------------
# Back-compat module-level function
# ---------------------------------------------------------------------------
def measure_tcp_connect() -> int:
    """Legacy API. Prefer `TCPProbe().measure()`."""
    return TCPProbe().measure()
