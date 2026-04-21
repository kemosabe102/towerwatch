"""DNS resolution time probe."""

import logging
import time

import dns.resolver

import config
from loki import log_and_push
from probes.base import Probe, ProbeResult

log = logging.getLogger("towerwatch")


def measure_dns(nameserver: str) -> float:
    """Measure DNS resolution time in ms using explicit nameserver."""
    resolver = dns.resolver.Resolver()
    resolver.nameservers = [nameserver]
    resolver.lifetime = config.DNS_TIMEOUT_S
    try:
        start = time.perf_counter()
        resolver.resolve(config.DNS_QUERY_DOMAIN, "A")
        return round((time.perf_counter() - start) * 1000)
    except Exception as e:
        log_and_push("WARN", f"DNS {nameserver} failed",
                     event=config.LOG_EVENT_DNS_FAILED, nameserver=nameserver, error=str(e))
        return 0


class DNSProbe:
    def __init__(self, nameserver: str):
        self.nameserver = nameserver
        ns_label = nameserver.replace(".", "_")
        self.name = f"dns_{ns_label}"
        self._field = f"dns_resolve_ms_{ns_label}"

    def run(self) -> ProbeResult:
        ms = measure_dns(self.nameserver)
        return ProbeResult(fields={self._field: ms}, ok=ms > 0)
