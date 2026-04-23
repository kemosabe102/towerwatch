"""DNS resolution time probe."""

import logging

import dns.resolver

import config
from clock import Clock, SystemClock
from probes.base import Probe, ProbeResult

log = logging.getLogger("towerwatch")


class _ModuleLokiSink:
    def log_and_push(self, level, message, **fields):
        from loki import log_and_push
        log_and_push(level, message, **fields)


def _default_resolver_factory() -> dns.resolver.Resolver:
    return dns.resolver.Resolver()


class DNSProbe:
    """Measure DNS resolution time in ms using an explicit nameserver."""

    def __init__(
        self,
        nameserver: str,
        resolver_factory=_default_resolver_factory,
        clock: Clock | None = None,
        loki=None,
        domain: str | None = None,
        lifetime_s: int | None = None,
    ):
        self.nameserver = nameserver
        ns_label = nameserver.replace(".", "_")
        self.name = f"dns_{ns_label}"
        self._field = f"dns_resolve_ms_{ns_label}"
        self._resolver_factory = resolver_factory
        self._clock = clock if clock is not None else SystemClock()
        self._loki = loki if loki is not None else _ModuleLokiSink()
        self._domain = domain if domain is not None else config.DNS_QUERY_DOMAIN
        self._lifetime_s = lifetime_s if lifetime_s is not None else config.DNS_TIMEOUT_S

    def measure(self) -> int:
        resolver = self._resolver_factory()
        resolver.nameservers = [self.nameserver]
        resolver.lifetime = self._lifetime_s
        try:
            start = self._clock.perf_counter()
            resolver.resolve(self._domain, "A")
            return round((self._clock.perf_counter() - start) * 1000)
        except Exception as e:
            self._loki.log_and_push(
                "WARN", f"DNS {self.nameserver} failed",
                event=config.LOG_EVENT_DNS_FAILED,
                nameserver=self.nameserver, error=str(e),
            )
            return 0

    def run(self) -> ProbeResult:
        ms = self.measure()
        return ProbeResult(fields={self._field: ms}, ok=ms > 0)


# ---------------------------------------------------------------------------
# Back-compat module-level function
# ---------------------------------------------------------------------------
def measure_dns(nameserver: str) -> int:
    """Legacy API. Prefer `DNSProbe(nameserver).measure()`."""
    return DNSProbe(nameserver).measure()
