"""Vendor-agnostic gateway health probe.

Baseline (always): TCP connect + HTTP response time to GATEWAY_IP.
M6 (GATEWAY_VENDOR="m6"): delegates to probes.m6.poll_m6_signal() for radio metrics.
Orbi (GATEWAY_VENDOR="orbi"): unauthenticated /api/DEV_INFO for connected client count.
"""
import logging
import socket
import time
import xml.etree.ElementTree as ET

import requests

import config
from probes.base import Probe, ProbeResult

log = logging.getLogger("towerwatch")


def _probe_baseline(ip: str, port: int, timeout: float) -> dict:
    fields = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        t0 = time.perf_counter()
        sock.connect((ip, port))
        fields["gateway_tcp_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    except OSError:
        fields["gateway_tcp_ms"] = 0
    finally:
        sock.close()
    try:
        t0 = time.perf_counter()
        requests.get(f"http://{ip}/", timeout=timeout)
        fields["gateway_http_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    except Exception:
        fields["gateway_http_ms"] = 0
    return fields


def _probe_orbi(ip: str, timeout: float) -> dict:
    try:
        resp = requests.get(f"http://{ip}/api/DEV_INFO", timeout=timeout)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        el = root.find(".//ConnectedDeviceCount")
        if el is not None:
            return {"gateway_clients": int(el.text)}
    except Exception as e:
        log.debug("Orbi DEV_INFO failed: %s", e)
    return {}


def poll_gateway() -> dict:
    fields = _probe_baseline(config.GATEWAY_IP, config.GATEWAY_TCP_PORT, config.GATEWAY_TIMEOUT_S)
    vendor = getattr(config, "GATEWAY_VENDOR", "")
    if vendor == "m6":
        from probes.m6 import poll_m6_signal
        fields.update(poll_m6_signal())
    elif vendor == "orbi":
        fields.update(_probe_orbi(config.GATEWAY_IP, config.GATEWAY_TIMEOUT_S))
    return fields


class GatewayProbe:
    name = "gateway"

    def run(self) -> ProbeResult:
        f = poll_gateway()
        return ProbeResult(fields=f, ok=f.get("gateway_tcp_ms", 0) > 0)
