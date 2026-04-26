"""One-shot dashboard editor. Mechanical edits to grafana/dashboard.json:

  1. Delete panel id 1 (Connection Uptime — query was broken, replaced by
     Traffic Golden Signal tile).
  2. Shift every remaining panel with y >= 4 down by +4 to make room.
     Inner panels in collapsed rows shift too.
  3. Insert 4 Golden Signals stat tiles at y=4 (Latency, Traffic, Errors,
     Saturation), x=0/6/12/18, w=6, h=4.
  4. Add two query templating variables: $max_download_mbps, $max_upload_mbps.
  5. Update gauge `max` thresholds for panels 9, 29, 23, 30.

Run from the repo root: `python scripts/update_dashboard.py`.
This script is checked in for reproducibility — re-running on an already-edited
JSON will detect that and refuse, so it's safe.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DASH_PATH = Path(__file__).resolve().parent.parent / "grafana" / "dashboard.json"
SHIFT = 4
GOLDEN_ROW_Y = 4


def _shift_y(panels, threshold=4, by=SHIFT):
    """Shift every panel's gridPos.y by `by` if y >= threshold."""
    for p in panels:
        gp = p.get("gridPos")
        if gp and gp.get("y", 0) >= threshold:
            gp["y"] = gp["y"] + by


def main() -> int:
    with open(DASH_PATH, encoding="utf-8") as f:
        dash = json.load(f)

    panels = dash["panels"]

    # Idempotency check: if our new templating var already exists, this script
    # already ran. Bail rather than double-shift.
    var_names = {v.get("name") for v in dash.get("templating", {}).get("list", [])}
    if "max_download_mbps" in var_names:
        print("Dashboard already updated (max_download_mbps variable exists). Aborting.")
        return 1

    # Pre-define datasource ref used by all newly-built panels.
    DS = {"type": "prometheus", "uid": "grafanacloud-towerwatch-prom"}

    # 1. Delete panel id 1 (Connection Uptime). Resize the two remaining HUD
    #    tiles to fill the row width so it reads as a balanced identity bar.
    panels = [p for p in panels if p.get("id") != 1]
    dash["panels"] = panels
    for p in panels:
        if p.get("id") == 2:  # Current Status
            p["gridPos"].update({"x": 0, "w": 12})
        elif p.get("id") == 24:  # Deployed Version
            p["gridPos"].update({"x": 12, "w": 12})

    # 1b. Replace the "Most Recent Sample" stat (id 30) with an Avg HTTP Upload
    #     gauge symmetric to id 23 (Avg HTTP Download). The stat was confusing
    #     to users and the upload gauge is more useful for the Saturation analysis.
    #     Also resize id 23 from w=8 to w=6 to make room.
    for p in panels:
        if p.get("id") == 23:
            p["title"] = "Avg HTTP Download"
            p["description"] = (
                "Mean download throughput from passive HTTP samples (6×/day, "
                "random schedule). Gauge over the dashboard time range. Sample "
                "size is per-site (HTTP_THROUGHPUT_BYTES_OVERRIDE in credentials.py)."
            )
            p["gridPos"].update({"w": 6})
        elif p.get("id") == 30:
            # Rebuild as upload gauge
            p["type"] = "gauge"
            p["title"] = "Avg HTTP Upload"
            p["description"] = (
                "Mean upload throughput from passive HTTP samples (6×/day, "
                "random schedule, paired with the download probe). Cellular "
                "uplinks are typically capacity-capped — expect a flat ceiling."
            )
            p["gridPos"].update({"x": 18, "w": 6})
            p["targets"] = [
                {
                    "expr": 'avg(avg_over_time(towerwatch_http_upload_mbps{host="$location"}[$__range]))',
                    "instant": True,
                    "refId": "A",
                    "datasource": DS,
                }
            ]
            p["fieldConfig"] = {
                "defaults": {
                    "unit": "Mbps",
                    "min": 0,
                    "decimals": 1,
                    "color": {"mode": "fixed", "fixedColor": "#F2CC0C"},
                },
                "overrides": [],
            }
            p["options"] = {
                "orientation": "auto",
                "showThresholdLabels": False,
                "showThresholdMarkers": True,
                "reduceOptions": {
                    "calcs": ["lastNotNull"],
                    "fields": "",
                    "values": False,
                },
            }

    # 2. Shift all top-level panels with y >= 4 by +4. Also shift inner panels
    #    in any collapsed row whose own y >= 4.
    _shift_y(panels)
    for p in panels:
        if p.get("type") == "row" and p.get("panels"):
            _shift_y(p["panels"])

    # 3. Insert 4 Golden Signals stat tiles at y=4.

    def stat(pid, title, desc, x, expr, unit, thresholds, color="green"):
        return {
            "id": pid,
            "title": title,
            "description": desc,
            "type": "stat",
            "datasource": DS,
            "gridPos": {"h": 4, "w": 6, "x": x, "y": GOLDEN_ROW_Y},
            "targets": [
                {"expr": expr, "instant": True, "refId": "A", "datasource": DS}
            ],
            "fieldConfig": {
                "defaults": {
                    "unit": unit,
                    "decimals": 1,
                    "color": {"mode": "thresholds"},
                    "thresholds": {"mode": "absolute", "steps": thresholds},
                    "noValue": "—",
                },
                "overrides": [],
            },
            "options": {
                "colorMode": "value",
                "graphMode": "area",
                "textMode": "auto",
                "justifyMode": "center",
                "orientation": "auto",
                "reduceOptions": {
                    "calcs": ["lastNotNull"],
                    "fields": "",
                    "values": False,
                },
            },
        }

    # Latency: avg RTT to Google over last 1h. Used as the SRE Latency signal.
    latency = stat(
        pid=40,
        title="Latency (1h avg)",
        desc="Average ping RTT to 8.8.8.8 over the last hour. The Latency Golden Signal — high values mean the link is slow regardless of throughput.",
        x=0,
        expr='avg(avg_over_time(towerwatch_rtt_avg_google{host="$location"}[1h]))',
        unit="ms",
        thresholds=[
            {"color": "green", "value": None},
            {"color": "yellow", "value": 50},
            {"color": "red", "value": 150},
        ],
    )

    # Traffic: % of selected range where towerwatch_connected==1. Replaces
    # the broken Connection Uptime panel — uses the corrected query.
    traffic = stat(
        pid=41,
        title="Uptime (range %)",
        desc="Percentage of time the link reported connected=1 over the dashboard time range. The Traffic Golden Signal. Replaces the broken Connection Uptime panel — uses avg_over_time directly so sub-5-min outages aren't masked.",
        x=6,
        expr='avg_over_time(towerwatch_connected{host="$location"}[$__range]) * 100',
        unit="percent",
        thresholds=[
            {"color": "red", "value": None},
            {"color": "yellow", "value": 95},
            {"color": "green", "value": 99},
        ],
    )

    # Errors: max packet loss across off-link targets (excluding gateway,
    # which is often legitimately lossy on cellular). The Errors signal.
    errors = stat(
        pid=42,
        title="Packet Loss (1h max)",
        desc="Highest packet-loss percentage to Google or Cloudflare over the last hour. Excludes gateway loss (often legitimately high on cellular). The Errors Golden Signal.",
        x=12,
        expr='max(max_over_time(towerwatch_packet_loss_google{host="$location"}[1h]), max_over_time(towerwatch_packet_loss_cloudflare{host="$location"}[1h]))',
        unit="percent",
        thresholds=[
            {"color": "green", "value": None},
            {"color": "yellow", "value": 1},
            {"color": "red", "value": 5},
        ],
    )

    # Saturation: 6h-avg HTTP throughput as % of plan capacity. Saturation signal.
    saturation = stat(
        pid=43,
        title="Saturation (6h)",
        desc="6-hour average HTTP throughput as a percentage of the plan capacity ($max_download_mbps). 30–80% is healthy active use; >95% means the link is saturated; near 0% means light usage. The Saturation Golden Signal.",
        x=18,
        expr='avg_over_time(towerwatch_http_throughput_mbps{host="$location"}[6h]) / $max_download_mbps * 100',
        unit="percent",
        thresholds=[
            {"color": "green", "value": None},
            {"color": "yellow", "value": 80},
            {"color": "red", "value": 95},
        ],
    )

    panels.extend([latency, traffic, errors, saturation])

    # 4. Add 2 templating variables.
    template_list = dash.setdefault("templating", {}).setdefault("list", [])

    template_list.append(
        {
            "name": "max_download_mbps",
            "label": "Link Max Down (Mbps)",
            "type": "query",
            "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
            "query": 'label_values(towerwatch_build_info{host="$location"}, link_max_download_mbps)',
            "refresh": 1,
            "sort": 0,
            "includeAll": False,
            "multi": False,
            "hide": 2,
        }
    )
    template_list.append(
        {
            "name": "max_upload_mbps",
            "label": "Link Max Up (Mbps)",
            "type": "query",
            "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
            "query": 'label_values(towerwatch_build_info{host="$location"}, link_max_upload_mbps)',
            "refresh": 1,
            "sort": 0,
            "includeAll": False,
            "multi": False,
            "hide": 2,
        }
    )

    # 5. Set generous gauge maxes that fit the fastest expected link (1 Gbps
    #    home / 50-100 Mbps upload). Grafana does NOT support templating in
    #    fieldConfig.defaults.max, so per-Pi dynamic max isn't workable today.
    #    Trade-off: slow links show the needle low on the gauge, but the
    #    actual Mbps number is always readable. Thresholds use percentage
    #    mode so they scale with the max.
    DOWNLOAD_GAUGE_MAX = 1500  # comfortably above 1 Gbps home, plenty of headroom
    UPLOAD_GAUGE_MAX = 100  # above typical 50 Mbps cable upstream
    download_gauges = {9, 23}
    upload_gauges = {29, 30}

    proportional_thresholds = [
        {"color": "red", "value": None},
        {"color": "orange", "value": 25},
        {"color": "yellow", "value": 50},
        {"color": "green", "value": 75},
    ]

    for p in panels:
        pid = p.get("id")
        if pid in download_gauges:
            defaults = p.setdefault("fieldConfig", {}).setdefault("defaults", {})
            defaults["max"] = DOWNLOAD_GAUGE_MAX
            defaults["thresholds"] = {
                "mode": "percentage",
                "steps": proportional_thresholds,
            }
        elif pid in upload_gauges:
            defaults = p.setdefault("fieldConfig", {}).setdefault("defaults", {})
            defaults["max"] = UPLOAD_GAUGE_MAX
            defaults["thresholds"] = {
                "mode": "percentage",
                "steps": proportional_thresholds,
            }

    # Save
    with open(DASH_PATH, "w", encoding="utf-8") as f:
        json.dump(dash, f, indent=2)
        f.write("\n")

    print(f"Updated {DASH_PATH}")
    print(f"Panel count: {len(panels)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
