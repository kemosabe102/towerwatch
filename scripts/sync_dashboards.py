#!/usr/bin/env python3
"""Push grafana/*.json dashboards to Grafana Cloud, overwriting in place by UID.

The dashboard JSON files are stored in Grafana *export* format: they carry an
``__inputs`` block and a ``${DS_PROMETHEUS}`` datasource placeholder that the
Grafana UI import wizard resolves interactively. The raw ``POST
/api/dashboards/db`` API does **not** process ``__inputs`` — it would store the
literal ``${DS_PROMETHEUS}`` string as a datasource UID and break those panels.

This script normalises each export into a deployable dashboard:
  1. Resolve ``${DS_PROMETHEUS}`` -> the real Prometheus datasource UID.
  2. Strip export-only keys (``__inputs``, ``__requires``, ``id``).
  3. POST wrapped as ``{"dashboard": ..., "overwrite": true}`` so the existing
     dashboard with the same UID is updated in place (no duplicates).

Run locally or from CI. Auth is a **stack service-account token** (NOT an
Access Policy token — those write telemetry, not dashboards):

    GRAFANA_URL=https://towerwatch.grafana.net \
    GRAFANA_SA_TOKEN=glsa_... \
    python scripts/sync_dashboards.py

Exits non-zero on the first failed push so CI fails loudly.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Real datasource UIDs in the Grafana Cloud stack. These are stable per-stack
# names (visible under Connections -> Data sources). The export placeholder
# ${DS_PROMETHEUS} maps to the Prometheus one; the Loki UID is already hardcoded
# in the JSON so it needs no remapping.
PROM_DS_UID = "grafanacloud-towerwatch-prom"
DS_PLACEHOLDER = "${DS_PROMETHEUS}"

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "grafana"
DASHBOARDS = ["dashboard.json", "dashboard-compare.json"]

# Export-only keys that must not be sent to /api/dashboards/db.
EXPORT_ONLY_KEYS = ("__inputs", "__requires", "id")


def normalise(dashboard: dict) -> dict:
    """Return a deployable copy: placeholder resolved, export keys stripped."""
    # Resolve the datasource placeholder everywhere it appears (nested deep in
    # panels/targets/templating), via a serialise-replace-parse round trip.
    resolved = json.loads(json.dumps(dashboard).replace(DS_PLACEHOLDER, PROM_DS_UID))
    for key in EXPORT_ONLY_KEYS:
        resolved.pop(key, None)
    if not resolved.get("uid"):
        raise SystemExit(
            "dashboard is missing a stable 'uid' — assign one before syncing "
            "(an empty uid creates a new dashboard on every push)"
        )
    return resolved


def push(url: str, token: str, dashboard: dict) -> None:
    payload = json.dumps({"dashboard": dashboard, "overwrite": True, "message": "CI sync"}).encode()
    req = urllib.request.Request(
        f"{url.rstrip('/')}/api/dashboards/db",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read())
    print(f"  -> {body.get('status', '?')} (uid={body.get('uid')}, version={body.get('version')})")


def main() -> int:
    url = os.environ.get("GRAFANA_URL")
    token = os.environ.get("GRAFANA_SA_TOKEN")
    if not url or not token:
        print(
            "ERROR: set GRAFANA_URL and GRAFANA_SA_TOKEN.\n"
            "  GRAFANA_URL=https://<stack>.grafana.net\n"
            "  GRAFANA_SA_TOKEN=<stack service-account token, glsa_...>",
            file=sys.stderr,
        )
        return 2

    failed = False
    for name in DASHBOARDS:
        path = DASHBOARD_DIR / name
        print(f"Syncing {name}...")
        dashboard = normalise(json.loads(path.read_text(encoding="utf-8")))
        try:
            push(url, token, dashboard)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            print(f"  FAILED {e.code}: {detail}", file=sys.stderr)
            failed = True
        except urllib.error.URLError as e:
            print(f"  FAILED (network): {e.reason}", file=sys.stderr)
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
