"""Tests for M6Probe — exercise the live fixture, the connection-type gate,
and the partial-payload / non-JSON failure modes.

No patching: all collaborators (session, loki, is_cellular gate) are injected.
"""

import json
from pathlib import Path

from tests.fakes import FakeLoki, FakeResponse, FakeSession

_FIXTURES = Path(__file__).parent / "fixtures"


def _build_probe(responses=None, loki=None, is_cellular=lambda: True):
    """Build an M6Probe with a session_factory returning a FakeSession queued
    with the given responses."""
    from towerwatch.probes.m6 import M6Probe

    session = FakeSession(get_responses=responses or [])
    probe = M6Probe(
        session_factory=lambda: session,
        loki=loki or FakeLoki(),
        url="http://fake-m6/api/model.json",
        timeout_s=5,
        is_cellular=is_cellular,
    )
    return probe, session


def _ok_resp(data, status=200):
    return FakeResponse(status_code=status, _json=data)


# ---------------------------------------------------------------------------
# Live-fixture happy path — validate against the standstill capture
# ---------------------------------------------------------------------------
def test_m6_parses_live_standstill_fixture():
    """Pulls every Tier 1 + Tier 2 field from the live capture."""
    model = json.loads((_FIXTURES / "m6_model_standstill.json").read_text())
    probe, _ = _build_probe(responses=[_ok_resp(model)])
    result = probe.poll()

    # Signal quality (LTE anchor)
    assert result["m6_rsrp"] == -104
    assert result["m6_rsrq"] == -13
    assert result["m6_sinr"] == 0
    assert result["m6_rssi"] == -71
    assert result["m6_bars"] == 4

    # 5G NR placeholder values (-32768 sentinel) are filtered out — the modem
    # is on NSA so the LTE anchor is the source of truth, NR fields aren't
    # populated by this firmware until SA mode.
    assert "m6_nr5g_rsrp" not in result
    assert "m6_nr5g_rsrq" not in result
    assert "m6_nr5g_sinr" not in result

    # Serving cell identity
    assert result["m6_cell_id"] == 359184
    assert result["m6_earfcn"] == 67086
    assert result["m6_earfcn_ul"] == 132622
    assert result["m6_band"] == 66  # parsed from "LTE B66"
    assert result["m6_lac"] == 262
    assert result["m6_radio_quality"] == 49
    assert result["m6_tx_level"] == -50
    assert result["m6_rx_level"] == -104

    # Network identity (Verizon US)
    assert result["m6_mcc"] == 311
    assert result["m6_mnc"] == 480

    # Attachment state
    assert result["m6_lte_attached"] == 1
    assert result["m6_nr5g_attached"] == 1
    assert result["m6_endc_enabled"] == 1

    # Service type enum
    assert result["m6_service_type"] == 4  # Nr5gService

    # Carrier aggregation — fixture has 3 SCCs (last list entry is sentinel {})
    assert result["m6_ca_scc_count"] == 3
    assert result["m6_ca_scc_declared"] == 3

    # Derived tower fields (cellId 359184 = eNB 1403, sector 16)
    assert result["m6_enb_id"] == 1403
    assert result["m6_sector_id"] == 16

    # Thermal state enum ("Normal" → 0)
    assert result["m6_thermal_state"] == 0

    # Per-carrier band info (lteBandInfo: 4 real carriers 10+20+10+10 MHz,
    # last list entry is the {} sentinel and is ignored)
    assert result["m6_carrier_count"] == 4
    assert result["m6_agg_dl_bandwidth_mhz"] == 50
    assert result["m6_pcc_band"] == 66
    assert result["m6_pcc_bandwidth_mhz"] == 10
    assert result["m6_pcc_pci"] == 81  # phyCid "81"


# ---------------------------------------------------------------------------
# Connection-type gate
# ---------------------------------------------------------------------------
def test_m6_self_disables_when_not_cellular():
    """Probe must not make HTTP calls when CONNECTION_TYPE is not cellular."""
    probe, session = _build_probe(responses=[], is_cellular=lambda: False)
    assert probe.poll() == {}
    assert session.get_calls == [], "probe must not have made any HTTP calls"


def test_m6_run_returns_ok_true_when_disabled():
    """A probe that correctly skipped itself is not a failure."""
    probe, _ = _build_probe(is_cellular=lambda: False)
    result = probe.run()
    assert result.ok is True
    assert result.fields == {}


# ---------------------------------------------------------------------------
# Schema tolerance
# ---------------------------------------------------------------------------
def test_m6_missing_top_level_keys_returns_empty():
    probe, _ = _build_probe(responses=[_ok_resp({})])
    assert probe.poll() == {}


def test_m6_partial_response_yields_subset():
    """Only the fields actually present in the response come out."""
    partial = {"wwan": {"signalStrength": {"rsrp": -90}}}
    probe, _ = _build_probe(responses=[_ok_resp(partial)])
    result = probe.poll()
    assert result == {"m6_rsrp": -90}


def test_m6_invalid_int_sentinel_filtered():
    """`-32768` is the firmware's 'no measurement' sentinel; must be dropped."""
    model = {"wwan": {"signalStrength": {"rsrp": -32768, "rsrq": -10}}}
    probe, _ = _build_probe(responses=[_ok_resp(model)])
    result = probe.poll()
    assert "m6_rsrp" not in result
    assert result["m6_rsrq"] == -10


def test_m6_band_string_parsed_from_LTE_prefix():
    model = {"wwanadv": {"curBand": "LTE B13"}}
    probe, _ = _build_probe(responses=[_ok_resp(model)])
    assert probe.poll()["m6_band"] == 13


def test_m6_band_no_digits_returns_zero():
    model = {"wwanadv": {"curBand": "n77"}}
    probe, _ = _build_probe(responses=[_ok_resp(model)])
    # n77 contains digits "77" → 77, not 0. This documents that NR band
    # strings extract the digits the same way LTE strings do.
    assert probe.poll()["m6_band"] == 77


def test_m6_band_empty_string_returns_zero():
    model = {"wwanadv": {"curBand": "no service"}}
    probe, _ = _build_probe(responses=[_ok_resp(model)])
    assert probe.poll()["m6_band"] == 0


# ---------------------------------------------------------------------------
# Derived tower fields
# ---------------------------------------------------------------------------
def test_m6_enb_id_and_sector_derived():
    model = {"wwanadv": {"cellId": 359184}}
    probe, _ = _build_probe(responses=[_ok_resp(model)])
    result = probe.poll()
    assert result["m6_enb_id"] == 1403
    assert result["m6_sector_id"] == 16


def test_m6_zero_cell_id_skips_derivation():
    """cellId 0 means 'unattached'; derived eNB/sector should not appear."""
    model = {"wwanadv": {"cellId": 0}}
    probe, _ = _build_probe(responses=[_ok_resp(model)])
    result = probe.poll()
    assert "m6_enb_id" not in result
    assert "m6_sector_id" not in result


# ---------------------------------------------------------------------------
# Carrier aggregation
# ---------------------------------------------------------------------------
def test_m6_ca_count_excludes_sentinel():
    """SCClist trailing `{}` is a sentinel, not a real secondary cell."""
    model = {
        "wwan": {
            "ca": {
                "SCCcount": 2,
                "SCClist": [
                    {"cellid": "81", "dlchan": "0", "sigrsrp": "0", "sigrsrq": "0"},
                    {"cellid": "81", "dlchan": "850", "sigrsrp": "0", "sigrsrq": "0"},
                    {},
                ],
            }
        }
    }
    probe, _ = _build_probe(responses=[_ok_resp(model)])
    result = probe.poll()
    assert result["m6_ca_scc_count"] == 2
    assert result["m6_ca_scc_declared"] == 2


# ---------------------------------------------------------------------------
# Device telemetry (general / power sections)
# ---------------------------------------------------------------------------
def test_m6_device_temperature_numeric():
    """general.devTemperature is the real numeric device temp (°C, metric)."""
    model = {"general": {"devTemperature": 61, "useMetricSystem": True}}
    probe, _ = _build_probe(responses=[_ok_resp(model)])
    assert probe.poll()["m6_dev_temperature"] == 61


def test_m6_device_temp_critical_flag():
    model = {"power": {"deviceTempCritical": True}}
    probe, _ = _build_probe(responses=[_ok_resp(model)])
    assert probe.poll()["m6_dev_temp_critical"] == 1


def test_m6_ethernet_speed_parsed_to_mbps():
    model = {"power": {"ethernetSpeed": "1000M"}}
    probe, _ = _build_probe(responses=[_ok_resp(model)])
    assert probe.poll()["m6_eth_speed_mbps"] == 1000


def test_m6_uptime_seconds():
    model = {"general": {"upTime": 6926342}}
    probe, _ = _build_probe(responses=[_ok_resp(model)])
    assert probe.poll()["m6_uptime_s"] == 6926342


def test_m6_device_fields_absent_omitted():
    model = {"wwan": {"signalStrength": {"rsrp": -90}}}
    probe, _ = _build_probe(responses=[_ok_resp(model)])
    result = probe.poll()
    for key in ("m6_dev_temperature", "m6_dev_temp_critical", "m6_eth_speed_mbps", "m6_uptime_s"):
        assert key not in result


# ---------------------------------------------------------------------------
# Thermal state
# ---------------------------------------------------------------------------
def test_m6_thermal_state_normal():
    model = {"wwan": {"thermalState": "Normal"}}
    probe, _ = _build_probe(responses=[_ok_resp(model)])
    assert probe.poll()["m6_thermal_state"] == 0


def test_m6_thermal_state_unknown_falls_back_to_zero():
    model = {"wwan": {"thermalState": "SomethingNew"}}
    probe, _ = _build_probe(responses=[_ok_resp(model)])
    assert probe.poll()["m6_thermal_state"] == 0


def test_m6_thermal_state_absent_omitted():
    model = {"wwan": {"signalStrength": {"rsrp": -90}}}
    probe, _ = _build_probe(responses=[_ok_resp(model)])
    assert "m6_thermal_state" not in probe.poll()


# ---------------------------------------------------------------------------
# Per-carrier band info (lteBandInfo)
# ---------------------------------------------------------------------------
def test_m6_band_info_aggregates_bandwidth_and_counts_carriers():
    model = {
        "wwan": {
            "lteBandInfo": [
                {"band": 66, "dlBandwidth": "10MHz", "phyCid": "81", "sccId": 0, "isPcc": True},
                {"band": 66, "dlBandwidth": "20MHz", "phyCid": "81", "sccId": 1, "isPcc": False},
                {"band": 2, "dlBandwidth": "10MHz", "phyCid": "81", "sccId": 2, "isPcc": False},
                {},  # sentinel
            ]
        }
    }
    probe, _ = _build_probe(responses=[_ok_resp(model)])
    result = probe.poll()
    assert result["m6_carrier_count"] == 3
    assert result["m6_agg_dl_bandwidth_mhz"] == 40
    assert result["m6_pcc_band"] == 66
    assert result["m6_pcc_bandwidth_mhz"] == 10
    assert result["m6_pcc_pci"] == 81


def test_m6_band_info_absent_omits_keys():
    model = {"wwan": {"signalStrength": {"rsrp": -90}}}
    probe, _ = _build_probe(responses=[_ok_resp(model)])
    result = probe.poll()
    for key in (
        "m6_carrier_count",
        "m6_agg_dl_bandwidth_mhz",
        "m6_pcc_band",
        "m6_pcc_bandwidth_mhz",
        "m6_pcc_pci",
    ):
        assert key not in result


def test_m6_band_info_no_pcc_flag_falls_back_to_first_entry():
    """If no entry is flagged isPcc, the first real carrier is treated as PCC."""
    model = {
        "wwan": {
            "lteBandInfo": [
                {"band": 13, "dlBandwidth": "5MHz", "phyCid": "42", "sccId": 0},
                {},
            ]
        }
    }
    probe, _ = _build_probe(responses=[_ok_resp(model)])
    result = probe.poll()
    assert result["m6_carrier_count"] == 1
    assert result["m6_agg_dl_bandwidth_mhz"] == 5
    assert result["m6_pcc_band"] == 13
    assert result["m6_pcc_pci"] == 42


# ---------------------------------------------------------------------------
# Service type enum
# ---------------------------------------------------------------------------
def test_m6_service_type_lte_only():
    model = {"wwan": {"currentNWserviceType": "LteService"}}
    probe, _ = _build_probe(responses=[_ok_resp(model)])
    assert probe.poll()["m6_service_type"] == 3


def test_m6_service_type_unknown_string_falls_back_to_zero():
    model = {"wwan": {"currentNWserviceType": "MarsianRadio"}}
    probe, _ = _build_probe(responses=[_ok_resp(model)])
    assert probe.poll()["m6_service_type"] == 0


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------
def test_m6_401_clears_session_and_emits_event():
    loki = FakeLoki()
    probe, _ = _build_probe(responses=[_ok_resp({}, status=401)], loki=loki)
    probe._get_session()  # force a session to exist
    assert probe._session is not None
    assert probe.poll() == {}
    assert probe._session is None
    assert any(lp[2].get("event") == "m6_auth_expired" for lp in loki.log_and_pushes)


def test_m6_non_json_response_returns_empty():
    """Pointing at an Orbi (or any non-JSON gateway) must fail clean."""
    probe, _ = _build_probe(responses=[FakeResponse(status_code=200, _json=ValueError("not JSON"))])
    assert probe.poll() == {}


def test_m6_403_does_not_clear_session():
    """A 403 (or other transport failure) must not clobber the cached session.

    raise_for_status raises requests.HTTPError, a RequestException subclass —
    the probe catches it as a normal transport failure, not auth-expired.
    """
    import requests

    loki = FakeLoki()
    bad = FakeResponse(status_code=403, _raise=requests.HTTPError("403 Forbidden"))
    probe, _ = _build_probe(responses=[bad], loki=loki)
    probe._get_session()
    assert probe.poll() == {}
    assert probe._session is not None
    assert not any(lp[2].get("event") == "m6_auth_expired" for lp in loki.log_and_pushes)
