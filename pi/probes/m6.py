"""M6 Nighthawk signal quality probe."""

import logging

import requests

import config
import credentials
from loki import log_and_push
from probes.base import Probe, ProbeResult

log = logging.getLogger("towerwatch")

_m6_session = None

_M6_FIELD_MAP = [
    ('m6_rsrp', ('RSRP', 'rsrp'), int),
    ('m6_rsrq', ('RSRQ', 'rsrq'), int),
    ('m6_sinr', ('SINR', 'sinr'), int),
    ('m6_band', ('curBand', 'band'), lambda v: int(v) if str(v).isdigit() else 0),
]


def _ensure_m6_session() -> requests.Session:
    """Lazy-create and return a cached M6 session."""
    global _m6_session
    if _m6_session is None:
        _m6_session = requests.Session()
        _m6_session.auth = ('admin', credentials.M6_ADMIN_PASSWORD)
    return _m6_session


def _extract_m6_fields(data: dict) -> dict:
    """Extract signal metrics from M6 JSON using field map."""
    result = {}
    for metric, keys, convert in _M6_FIELD_MAP:
        val = next((data[k] for k in keys if k in data), None)
        if val is not None:
            result[metric] = convert(val)
    return result


def poll_m6_signal() -> dict:
    """Poll Nighthawk M6 for signal metrics. Returns dict or empty on failure."""
    try:
        session = _ensure_m6_session()
        resp = session.get(config.M6_WWAN_URL, timeout=config.M6_TIMEOUT_S)
        if resp.status_code == 401:
            global _m6_session
            _m6_session = None
            log_and_push('WARN', 'M6 auth expired', event=config.LOG_EVENT_M6_AUTH_EXPIRED)
            return {}
        resp.raise_for_status()
        return _extract_m6_fields(resp.json())
    except Exception as e:
        log.debug('M6 poll failed: %s', e)
        return {}


class M6Probe:
    name = "m6"

    def run(self) -> ProbeResult:
        f = poll_m6_signal()
        return ProbeResult(fields=f, ok=bool(f))
