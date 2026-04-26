"""Netgear Nighthawk M6 cellular signal + serving-cell probe.

Reads /api/model.json which bundles wwan + wwanadv + wwan.ca + wwan.diagInfo
in a single response. Read access is anonymous on M6 firmware apiVersion 2.0+,
so no auth dance is required for metric collection. The admin password in
credentials.py is only used for write operations (band lock, etc.) which the
probe does not perform.

The probe self-disables when CONNECTION_TYPE in credentials is not cellular,
so deploying the same code to a non-cellular site (Comcast cable, fiber)
costs no HTTP attempts.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from towerwatch import config
from towerwatch.probes.base import ProbeResult

log = logging.getLogger("towerwatch")


# ---------------------------------------------------------------------------
# Schema map: each entry is (metric_name, json_path_tuple, converter).
# json_path is a tuple of dict keys to walk from the root model.json.
# Multiple json_paths can be tried per metric — first hit wins. This makes the
# probe firmware-tolerant: when Netgear renames a key the probe survives until
# the alternate is also gone.
#
# Source of truth: tests/fixtures/m6_model_standstill.json (live capture
# 2026-04-25 from a Verizon-served Nighthawk M6, firmware apiVersion 2.0).
# ---------------------------------------------------------------------------

_INVALID_INT = -32768  # M6 sentinel for "no measurement available"


def _safe_int(v: Any) -> int | None:
    """Convert to int, returning None on parse failure or M6's -32768 sentinel."""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return None if n == _INVALID_INT else n


def _safe_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _band_int(v: Any) -> int | None:
    """`wwanadv.curBand` is a string like 'LTE B66' or 'B13' — extract digits."""
    if v is None:
        return None
    digits = "".join(ch for ch in str(v) if ch.isdigit())
    return int(digits) if digits else 0


def _bool_int(v: Any) -> int | None:
    """JSON booleans → 0/1 integers for Prometheus (Influx field types)."""
    if v is None:
        return None
    return 1 if bool(v) else 0


# Each entry: (metric_field, [path_alternatives], converter).
# Paths are tuples of nested keys.
_FIELD_MAP: list[tuple[str, list[tuple[str | int, ...]], Any]] = [
    # --- Signal quality (LTE anchor) ---
    ("m6_rsrp", [("wwan", "signalStrength", "rsrp")], _safe_int),
    ("m6_rsrq", [("wwan", "signalStrength", "rsrq")], _safe_int),
    ("m6_sinr", [("wwan", "signalStrength", "sinr")], _safe_int),
    ("m6_rssi", [("wwan", "signalStrength", "rssi")], _safe_int),
    ("m6_bars", [("wwan", "signalStrength", "bars")], _safe_int),
    # --- Signal quality (5G NR — populated when on NSA/SA) ---
    ("m6_nr5g_rsrp", [("wwan", "signalStrength", "nr5gRsrp")], _safe_int),
    ("m6_nr5g_rsrq", [("wwan", "signalStrength", "nr5gRsrq")], _safe_int),
    ("m6_nr5g_sinr", [("wwan", "signalStrength", "nr5gSinr")], _safe_int),
    # --- Serving cell identity ---
    ("m6_cell_id", [("wwanadv", "cellId")], _safe_int),
    ("m6_earfcn", [("wwanadv", "chanId")], _safe_int),
    ("m6_earfcn_ul", [("wwanadv", "chanIdUl")], _safe_int),
    ("m6_band", [("wwanadv", "curBand")], _band_int),
    ("m6_lac", [("wwanadv", "LAC")], _safe_int),
    ("m6_radio_quality", [("wwanadv", "radioQuality")], _safe_int),
    ("m6_tx_level", [("wwanadv", "txLevel")], _safe_int),
    ("m6_rx_level", [("wwanadv", "rxLevel")], _safe_int),
    # --- Network identity ---
    # MCC/MNC are strings in the JSON; emit as ints for Prometheus.
    ("m6_mcc", [("wwanadv", "MCC")], _safe_int),
    ("m6_mnc", [("wwanadv", "MNC")], _safe_int),
    # --- Attachment state ---
    ("m6_lte_attached", [("wwan", "diagInfo", 0, "lteAttached")], _bool_int),
    ("m6_nr5g_attached", [("wwan", "diagInfo", 0, "nr5gAttached")], _bool_int),
    ("m6_endc_enabled", [("wwan", "diagInfo", 0, "endcEnabledConfig")], _bool_int),
]


def _walk(d: Any, path: tuple) -> Any:
    """Walk a nested dict/list path; return None on any miss."""
    cur = d
    for key in path:
        try:
            cur = cur[key]
        except (KeyError, IndexError, TypeError):
            return None
    return cur


def _extract_fields(model: dict) -> dict:
    """Pull every metric in _FIELD_MAP from the parsed model.json tree."""
    out: dict = {}
    for metric, paths, convert in _FIELD_MAP:
        for path in paths:
            raw = _walk(model, path)
            if raw is None:
                continue
            converted = convert(raw)
            if converted is not None:
                out[metric] = converted
                break
    return out


def _extract_ca_fields(model: dict) -> dict:
    """Extract carrier-aggregation secondary-cell counters.

    `wwan.ca.SCClist` is a list where the last entry is `{}` (sentinel). Real
    SCCs have non-empty `cellid`. Emits a count and per-SCC RSRP if present.
    Returns {} (not a count of 0) when the CA section is absent so a probe
    that scraped a payload missing `wwan.ca` doesn't fabricate a fake zero.
    """
    out: dict = {}
    ca = _walk(model, ("wwan", "ca"))
    if not isinstance(ca, dict):
        return out
    scc_list = ca.get("SCClist", []) or []
    real_sccs = [s for s in scc_list if isinstance(s, dict) and s.get("cellid")]
    out["m6_ca_scc_count"] = len(real_sccs)
    declared = ca.get("SCCcount")
    if declared is not None:
        n = _safe_int(declared)
        if n is not None:
            out["m6_ca_scc_declared"] = n
    return out


def _extract_service_type(model: dict) -> dict:
    """`wwan.currentNWserviceType` is a string enum. Map to a small int code
    so Prometheus state-timeline panels can colour by it.

    0=none, 1=2G/GSM, 2=WCDMA/3G, 3=LTE/4G, 4=NR5G NSA, 5=NR5G SA.
    """
    code_map = {
        "": 0,
        "None": 0,
        "GsmService": 1,
        "WcdmaService": 2,
        "LteService": 3,
        "Nr5gService": 4,  # firmware does not distinguish NSA/SA in this string
        "Nr5gSaService": 5,
    }
    raw = _walk(model, ("wwan", "currentNWserviceType"))
    if raw is None:
        return {}
    return {"m6_service_type": code_map.get(str(raw), 0)}


def _derive_tower_fields(fields: dict) -> dict:
    """Derive eNB ID + sector from cellId.

    LTE Cell ID is a 28-bit integer: high 20 bits = eNB ID (the physical
    tower), low 8 bits = sector within the tower. Splitting these out lets
    a dashboard panel count handoffs per tower vs per sector — the former
    means moving between towers, the latter a reselect within one tower.
    """
    out: dict = {}
    cid = fields.get("m6_cell_id")
    if isinstance(cid, int) and cid > 0:
        out["m6_enb_id"] = cid >> 8
        out["m6_sector_id"] = cid & 0xFF
    return out


def _is_cellular() -> bool:
    """True iff this Pi's CONNECTION_TYPE is a cellular variant.

    Cached at module import via config — credentials don't change at runtime.
    """
    return config.GATEWAY_VENDOR == "m6"


# ---------------------------------------------------------------------------
# Probe class
# ---------------------------------------------------------------------------


class _ModuleLokiSink:
    def log_and_push(self, level, message, **fields):
        from towerwatch.clients.loki import log_and_push

        log_and_push(level, message, **fields)


def _default_session_factory() -> requests.Session:
    """A plain session — read access is anonymous on M6 firmware 2.0+.

    The Basic-auth dance from older firmware is dropped because it never
    worked on the live device and the password is no longer required to
    read /api/model.json.
    """
    return requests.Session()


class M6Probe:
    """Poll an M6 hotspot's /api/model.json for cellular telemetry."""

    name = "m6"

    def __init__(
        self,
        session_factory=_default_session_factory,
        loki=None,
        url: str | None = None,
        timeout_s: int | None = None,
        is_cellular=None,
    ):
        self._session_factory = session_factory
        self._loki = loki if loki is not None else _ModuleLokiSink()
        self._url = url if url is not None else config.M6_ADMIN_URL
        self._timeout_s = timeout_s if timeout_s is not None else config.M6_TIMEOUT_S
        self._session: requests.Session | None = None
        # Indirected so tests can force-enable on a non-cellular config.
        self._is_cellular = is_cellular if is_cellular is not None else _is_cellular

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = self._session_factory()
        assert self._session is not None
        return self._session

    def _invalidate_session(self) -> None:
        self._session = None

    def poll(self) -> dict:
        """Poll the router and return metric fields, or {} on failure / not cellular.

        Returns an empty dict (not None) so callers can `.update()` unconditionally.
        """
        if not self._is_cellular():
            return {}
        try:
            # follow redirects: fresh M6 firmware returns 302 → /sess_cd_tmp on
            # cookie-less first hit, then serves the JSON.
            resp = self._get_session().get(
                self._url,
                timeout=self._timeout_s,
                allow_redirects=True,
            )
            if resp.status_code == 401:
                self._invalidate_session()
                self._loki.log_and_push(
                    "WARN",
                    "M6 auth expired",
                    event=config.LOG_EVENT_M6_AUTH_EXPIRED,
                )
                return {}
            resp.raise_for_status()
            model = resp.json()
        except requests.RequestException as e:
            log.debug("M6 poll transport failure: %s", e)
            return {}
        except ValueError as e:
            # JSON parse — typically means the gateway is not an M6 (returned
            # HTML, e.g. an Orbi or a 400 page). Log louder than a transport
            # failure because this signals a misconfigured site.
            log.warning("M6 poll: response was not JSON (gateway not an M6?): %s", e)
            return {}

        fields = _extract_fields(model)
        fields.update(_extract_ca_fields(model))
        fields.update(_extract_service_type(model))
        fields.update(_derive_tower_fields(fields))
        return fields

    def run(self) -> ProbeResult:
        f = self.poll()
        # ok=True when we got *any* data, or when the probe correctly self-
        # disabled (non-cellular site). The empty-with-disabled case must
        # not surface as an error.
        return ProbeResult(fields=f, ok=True)


# ---------------------------------------------------------------------------
# Back-compat module-level shim. Deprecated — prefer M6Probe directly.
# ---------------------------------------------------------------------------
_shared_probe: M6Probe | None = None


def poll_m6_signal() -> dict:
    """Back-compat module-level probe. Prefer `M6Probe().poll()`."""
    global _shared_probe
    if _shared_probe is None:
        _shared_probe = M6Probe()
    return _shared_probe.poll()
