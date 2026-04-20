"""TCP connection time probe."""

import logging
import socket
import time

import config

log = logging.getLogger("towerwatch")


def measure_tcp_connect() -> float:
    """Measure TCP handshake time in ms. Returns 0 on failure."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(config.TCP_TIMEOUT_S)
        start = time.perf_counter()
        sock.connect((config.TCP_TARGET_HOST, config.TCP_TARGET_PORT))
        elapsed = (time.perf_counter() - start) * 1000
        sock.close()
        return round(elapsed)
    except (OSError, socket.timeout) as e:
        log.warning("TCP connect failed: %s", e)
        return 0
