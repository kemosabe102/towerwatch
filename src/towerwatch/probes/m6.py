"""Netgear M6 Nighthawk signal quality probe.

Class-based; constructor takes an injectable session_factory (for lazy,
re-creatable authenticated sessions) plus clock, loki, url, auth, timeout.
"""

import logging

import requests

from towerwatch import config
from towerwatch.probes.base import Probe, ProbeResult

log = logging.getLogger("towerwatch")


_M6_FIELD_MAP = [
    ('m6_rsrp', ('RSRP', 'rsrp'), int),
    ('m6_rsrq', ('RSRQ', 'rsrq'), int),
    ('m6_sinr', ('SINR', 'sinr'), int),
    ('m6_band', ('curBand', 'band'), lambda v: int(v) if str(v).isdigit() else 0),
]


def _extract_m6_fields(data: dict) -> dict:
    """Extract signal metrics from M6 JSON using field map."""
    result = {}
    for metric, keys, convert in _M6_FIELD_MAP:
        val = next((data[k] for k in keys if k in data), None)
        if val is not None:
            result[metric] = convert(val)
    return result


class _ModuleLokiSink:
    def log_and_push(self, level, message, **fields):
        from towerwatch.clients.loki import log_and_push
        log_and_push(level, message, **fields)


def _default_session_factory() -> requests.Session:
    s = requests.Session()
    from towerwatch import credentials
    s.auth = ('admin', credentials.M6_ADMIN_PASSWORD)
    return s


class M6Probe:
    """Poll Nighthawk M6 for signal metrics."""

    name = "m6"

    def __init__(
        self,
        session_factory=_default_session_factory,
        loki=None,
        url: str | None = None,
        timeout_s: int | None = None,
    ):
        self._session_factory = session_factory
        self._loki = loki if loki is not None else _ModuleLokiSink()
        self._url = url if url is not None else config.M6_WWAN_URL
        self._timeout_s = timeout_s if timeout_s is not None else config.M6_TIMEOUT_S
        self._session: requests.Session | None = None

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = self._session_factory()
        return self._session

    def _invalidate_session(self) -> None:
        self._session = None

    def poll(self) -> dict:
        """Poll the router and return signal metrics, or {} on failure."""
        try:
            resp = self._get_session().get(self._url, timeout=self._timeout_s)
            if resp.status_code == 401:
                self._invalidate_session()
                self._loki.log_and_push(
                    'WARN', 'M6 auth expired',
                    event=config.LOG_EVENT_M6_AUTH_EXPIRED,
                )
                return {}
            resp.raise_for_status()
            return _extract_m6_fields(resp.json())
        except Exception as e:
            log.debug('M6 poll failed: %s', e)
            return {}

    def run(self) -> ProbeResult:
        f = self.poll()
        return ProbeResult(fields=f, ok=bool(f))


# ---------------------------------------------------------------------------
# Back-compat module-level function + global session (deprecated — use M6Probe).
# ---------------------------------------------------------------------------
_shared_probe: M6Probe | None = None

# Legacy globals kept for back-compat with test_probe_contract.py which
# reaches into _m6_session and _ensure_m6_session.
_m6_session: requests.Session | None = None


def _ensure_m6_session() -> requests.Session:
    """Legacy accessor. New code should construct M6Probe with a session_factory."""
    global _m6_session
    if _m6_session is None:
        _m6_session = _default_session_factory()
    return _m6_session


def poll_m6_signal() -> dict:
    """Back-compat module-level probe. Prefer `M6Probe().poll()`."""
    global _shared_probe
    if _shared_probe is None:
        _shared_probe = M6Probe()
    return _shared_probe.poll()
