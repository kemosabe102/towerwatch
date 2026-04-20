"""DNS resolution time probe."""

import logging
import time

import dns.resolver

import config
from loki import log_and_push

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
