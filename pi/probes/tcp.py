"""TCP connection time probe."""

import logging
import socket
import time

import config
from probes.base import Probe, ProbeResult

log = logging.getLogger("towerwatch")


def measure_tcp_connect() -> float:
    """Measure TCP handshake time in ms. Returns 0 on failure."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(config.TCP_TIMEOUT_S)
    try:
        start = time.perf_counter()
        sock.connect((config.TCP_TARGET_HOST, config.TCP_TARGET_PORT))
        return round((time.perf_counter() - start) * 1000)
    except (OSError, socket.timeout) as e:
        log.warning("TCP connect failed: %s", e)
        return 0
    finally:
        sock.close()


class TCPProbe:
    name = "tcp"

    def run(self) -> ProbeResult:
        ms = measure_tcp_connect()
        return ProbeResult(fields={"tcp_connect_ms": ms}, ok=ms > 0)
