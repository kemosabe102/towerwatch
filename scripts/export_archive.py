#!/usr/bin/env python3
"""Export Grafana Cloud Prometheus series to durable CSV before retention expiry.

Free-tier retention is ~14 days. This exports a fixed window at native resolution
to `data-archive/`, one CSV per (host, metric), matching the format documented in
`data-archive/standstill-jul2026/README.md`:

    timestamp_utc,iso_pdt,<labelset-1>,<labelset-2>,...

One column per distinct Prometheus series (label-set); an empty cell means no
sample at that timestamp for that series. Timestamps are step-aligned; empty rows
are real data gaps.

Reads GRAFANA_INSTANCE_ID + GRAFANA_READ_KEY from credentials.py (same auth path
as scripts/twq.sh — Basic auth against the /api/prom endpoint).

Usage:
    python scripts/export_archive.py --host standstill \
        --start 2026-07-01 --end 2026-07-16 \
        --out data-archive/standstill-jul2026/jul01_15_throttle

    # Only a subset of metrics:
    python scripts/export_archive.py --host standstill --start ... --end ... \
        --out ... --metrics m6_sinr m6_rsrp
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

PROM_HOST = "https://prometheus-prod-67-prod-us-west-0.grafana.net"
QUERY_RANGE = f"{PROM_HOST}/api/prom/api/v1/query_range"

# Prometheus caps points-per-series per query (11k). A 15-day window at 60s is
# ~21.6k, so the window is chunked and stitched. 5 days at 60s = 7.2k — safe.
CHUNK_DAYS = 5

# Metrics worth archiving, grouped by the claim each supports. Kept explicit
# rather than a wildcard so the archive is a deliberate evidence set.
DEFAULT_METRICS = [
    # Throughput — the observation itself
    "http_throughput_mbps",
    "http_upload_mbps",
    "http_throughput_bytes",
    "http_upload_bytes",
    "http_latency_ms",
    # Bufferbloat / latency-under-load
    "bufferbloat_rtt_idle_ms",
    "bufferbloat_rtt_download_ms",
    "bufferbloat_download_delta_ms",
    "bufferbloat_rtt_upload_ms",
    "bufferbloat_upload_delta_ms",
    # Radio quality — the exoneration
    "m6_sinr",
    "m6_rsrp",
    "m6_rsrq",
    "m6_rssi",
    "m6_bars",
    # Serving cell — the same-cell gate
    "m6_pcc_pci",
    "m6_pcc_band",
    "m6_cell_id",
    "m6_enb_id",
    "m6_sector_id",
    "m6_band",
    # Spectrum / carrier aggregation
    "m6_carrier_count",
    "m6_agg_dl_bandwidth_mhz",
    "m6_pcc_bandwidth_mhz",
    # Thermal — exclusion
    "m6_dev_temperature",
    "m6_thermal_state",
    # Radio access tech — the LTE-only fact
    "m6_nr5g_attached",
    "m6_service_type",
    "m6_lte_attached",
    "m6_tx_level",
    # Reachability / layer split
    "rtt_avg_google",
    "rtt_avg_cloudflare",
    "rtt_avg_gateway",
    "pkt_loss_google",
    "pkt_loss_gateway",
    "jitter_google",
    "connected",
    "tcp_connect_ms",
    "dns_resolve_ms_gateway",
    "dns_resolve_ms_8_8_8_8",
]


def load_creds() -> tuple[str, str]:
    from towerwatch import credentials as c  # noqa: PLC0415

    return str(c.GRAFANA_INSTANCE_ID), str(c.GRAFANA_READ_KEY)


def query_range(query: str, start: int, end: int, step: int, user: str, key: str) -> list[dict]:
    """One query_range call; returns the raw `result` list. Raises on API error."""
    params = urllib.parse.urlencode(
        {"query": query, "start": start, "end": end, "step": step}
    ).encode()
    req = urllib.request.Request(QUERY_RANGE, data=params)
    import base64

    token = base64.b64encode(f"{user}:{key}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
        import json

        payload = json.load(resp)
    if payload.get("status") != "success":
        raise RuntimeError(f"query failed: {payload.get('error', payload)}")
    return payload["data"]["result"]


def series_label(metric: dict) -> str:
    """Stable column name from a label-set, matching the existing archive format:
    `k=v;k=v` sorted, excluding __name__/host/job/instance plumbing."""
    skip = {"__name__", "host", "job", "instance", "__proxy_source__"}
    parts = [f"{k}={v}" for k, v in sorted(metric.items()) if k not in skip]
    return ";".join(parts) if parts else "default"


def fetch_metric(
    metric: str, host: str, start: int, end: int, step: int, user: str, key: str
) -> dict[str, dict[int, str]]:
    """Fetch one metric across the window, chunking to stay under the point cap.

    Returns {column_label: {timestamp: value}}.
    """
    query = f'towerwatch_{metric}{{host="{host}"}}'
    out: dict[str, dict[int, str]] = {}
    chunk = CHUNK_DAYS * 86400
    cur = start
    while cur < end:
        chunk_end = min(cur + chunk, end)
        for attempt in range(3):
            try:
                results = query_range(query, cur, chunk_end, step, user, key)
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    print(f"    ! {metric}: {e}", file=sys.stderr)
                    results = []
                    break
                time.sleep(2 * (attempt + 1))
        for r in results:
            col = series_label(r["metric"])
            bucket = out.setdefault(col, {})
            for ts, val in r["values"]:
                bucket[int(float(ts))] = val
        cur = chunk_end
    return out


def write_csv(path: Path, columns: dict[str, dict[int, str]], tz_name: str) -> int:
    """Write the wide CSV. Returns the row count."""
    all_ts = sorted({ts for col in columns.values() for ts in col})
    if not all_ts:
        return 0
    col_names = sorted(columns)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_utc", f"iso_{tz_name}", *col_names])
        for ts in all_ts:
            local = dt.datetime.fromtimestamp(ts).astimezone()
            w.writerow(
                [ts, local.isoformat(), *(columns[c].get(ts, "") for c in col_names)]
            )
    return len(all_ts)


def parse_day(s: str) -> int:
    """Local-midnight epoch for a YYYY-MM-DD date."""
    d = dt.datetime.strptime(s, "%Y-%m-%d")
    return int(d.timestamp())


def _tz_abbrev() -> str:
    """Short lowercase tz tag for the CSV header (`pdt`, not the Windows
    long form "Pacific Daylight Time") — matches the existing archive's
    `iso_pdt` column convention."""
    name = dt.datetime.now().astimezone().tzname() or "local"
    if " " in name:  # Windows returns the long name; initialise it.
        return "".join(w[0] for w in name.split()).lower()
    return name.lower()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", required=True, help='host tag, e.g. "standstill"')
    p.add_argument("--start", required=True, help="YYYY-MM-DD (local, inclusive)")
    p.add_argument("--end", required=True, help="YYYY-MM-DD (local, exclusive)")
    p.add_argument("--out", required=True, help="output directory")
    p.add_argument("--step", type=int, default=60, help="sample step in seconds (default 60)")
    p.add_argument("--metrics", nargs="*", default=None, help="metric subset (default: all)")
    args = p.parse_args()

    user, key = load_creds()
    start, end = parse_day(args.start), parse_day(args.end)
    metrics = args.metrics or DEFAULT_METRICS
    out_dir = REPO_ROOT / args.out
    tz_name = _tz_abbrev()

    print(f"Exporting host={args.host} {args.start}→{args.end} step={args.step}s")
    print(f"  → {out_dir}")
    written = empty = 0
    for m in metrics:
        cols = fetch_metric(m, args.host, start, end, args.step, user, key)
        path = out_dir / f"{args.host}__{m}.csv"
        rows = write_csv(path, cols, tz_name)
        if rows:
            written += 1
            print(f"  {m:32} {rows:6d} rows  {len(cols)} series")
        else:
            empty += 1
            print(f"  {m:32}      — no data")
    print(f"\nDone: {written} files written, {empty} empty (skipped).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
