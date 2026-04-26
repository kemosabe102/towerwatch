"""GATEWAY_IP is the single source of truth for the gateway address.

These tests pin the wiring in config.py so a future "cleanup" can't reintroduce
hard-coded IPs in PROBE_TARGETS or M6_*_URL.
"""

from __future__ import annotations

from towerwatch import config


def test_probe_targets_gateway_entry_uses_gateway_ip():
    gateway_entries = [t for t in config.PROBE_TARGETS if t[1] == "gateway"]
    assert len(gateway_entries) == 1, "Expected exactly one ('<ip>', 'gateway') target"
    ip, label = gateway_entries[0]
    assert ip == config.GATEWAY_IP


def test_m6_admin_url_tracks_gateway_ip():
    assert f"http://{config.GATEWAY_IP}/api/model.json" == config.M6_ADMIN_URL


def test_m6_wwan_url_tracks_gateway_ip():
    assert f"http://{config.GATEWAY_IP}/api/wwanadv.json" == config.M6_WWAN_URL


def test_gateway_label_is_stable():
    """Label stays 'gateway' regardless of IP — dashboards/alerts depend on it."""
    labels = [t[1] for t in config.PROBE_TARGETS]
    assert "gateway" in labels
