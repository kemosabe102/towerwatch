# Standstill trip data archive — July 2026

Durable CSV export of the standstill investigation data, pulled from Grafana Cloud
before free-tier 14-day retention expires. This is the canonical archive; the live
Grafana data for these windows is gone after the dates below.

## Provenance

- **Exported:** 2026-07-06 (via Grafana Cloud read API, `query_range`, 60s step)
- **Source:** `prometheus-prod-67-prod-us-west-0.grafana.net`, stack `towerwatch.grafana.net`
- **Exporter:** ad-hoc script (`scratchpad/export_standstill.py`), one CSV per (window, host, metric)

## Retention deadlines (why this archive exists)

| Window | Local dates | Live data expires |
|---|---|---|
| `jun29_outage/` | 2026-06-29 → 06-30 | **~2026-07-13** (expires first) |
| `jul02_05_trip/` | 2026-07-02 → 07-06 | **~2026-07-18** |

## CSV format

- One file per metric: `<host>__<metric>.csv` (e.g. `standstill__m6_sinr.csv`).
- Columns: `timestamp_utc`, `iso_pdt` (America/Los_Angeles), then **one column per distinct
  Prometheus series** (label-set). Empty cell = no sample at that timestamp for that series.
- Timestamps are 60s-stepped; gaps (empty rows) are real data gaps.

## Known artifacts baked into the data (read before analysis)

The **root cause** of the artifacts below was a **stale deployed `credentials.py`** on the Pi
(missing `GATEWAY_IP_OVERRIDE = "10.0.1.1"`), which let gateway auto-discovery freeze on the
fallback `192.168.1.1` after a restart on ~July 3. It was **NOT** deploy `76ab9e2` (docs/tests
only). Fixed 2026-07-06 by redeploying correct creds.

1. **Duplicate series across the July 3 break.** Standstill probe metrics in `jul02_05_trip/`
   have **two columns**: `carrier=verizon;connection_type=lte_cellular;experiment=none` (the
   correct series, present once the right creds were running) and `default` (the bare-label
   series from the stale-creds process). June 29 has only the one series. Select the labeled
   column for analysis.
2. **`pkt_loss_gateway` = 100% is FALSE** during the frozen-IP period — the Pi was pinging the
   dead `192.168.1.1` fallback, not the real gateway `10.0.1.1`. Real WAN loss lives in the
   Google/Cloudflare loss series. Gateway RTT (`gateway_tcp_ms`) stayed healthy throughout.
3. **`m6_*` telemetry is dead after ~July 3 evening** — the M6 probe was pointed at the wrong IP
   (`192.168.1.1`) during the frozen period. July 4 peak has NO M6 RF context; the M6 columns
   in `jul02_05_trip/` cover only ~July 2 → 3 evening (~2256 samples vs ~5696 for probes).
4. **Phone overnight RF is idle-camping data** (band 13, worse SINR) — not comparable to the
   always-active M6. Split by band or sample near throughput-probe times for fair RF comparison.
5. **`phone_nr_pci` / NR-signal fields are sparse** (~15–40% sample coverage) — the F2 phone
   NR/CA capture gap; NR *signal* may be unobtainable via the public Android API on this Pixel.
