# What's new — June 2026 work

Before/after summary of the towerwatch changes over 2026-06-04 → 2026-06-06.
Baseline = commit `2e4b7e8`; current = `03899af`. Use this for a show-and-tell.

**Headline:** +13 commits, +3,192 / −963 lines. One new probe (bufferbloat),
+10 new cellular metrics, 4 new band-tagged signal metrics, a reorganised
dashboard with 6 net-new panels, and a CI pipeline that auto-syncs dashboards.

---

## 1. New data we collect

### Bufferbloat — brand-new probe (`probes/bufferbloat.py`)
Measures **latency under load** — how much ping RTT inflates while the link is
saturated by a throughput test. This is the single best "is my connection
laggy when busy" signal and we collected *nothing* like it before.

| Metric | Meaning |
|---|---|
| `bufferbloat_rtt_idle_ms` | baseline RTT with no load |
| `bufferbloat_rtt_download_ms` | RTT during a download |
| `bufferbloat_rtt_upload_ms` | RTT during an upload |
| `bufferbloat_download_delta_ms` | inflation under download load (the bufferbloat number) |
| `bufferbloat_upload_delta_ms` | inflation under upload load |

### Cellular radio (M6) — +10 new metrics
We already polled RSRP/RSRQ/SINR/band. Added device-health and
spectrum-detail telemetry confirmed against a live `model.json`:

| New metric | Meaning |
|---|---|
| `m6_dev_temperature` | chassis temp °C — prime suspect for afternoon thermal throttling |
| `m6_dev_temp_critical` | device-reported thermal-critical flag |
| `m6_thermal_state` | thermal state code (normal/warm/hot/critical) |
| `m6_eth_speed_mbps` | Ethernet negotiated speed — catches a quiet renegotiation to 100M |
| `m6_uptime_s` | modem uptime — sudden drops correlate with outages |
| `m6_carrier_count` | number of aggregated carriers (throughput-cliff signal) |
| `m6_agg_dl_bandwidth_mhz` | total aggregated downlink bandwidth |
| `m6_pcc_band` | primary-carrier band |
| `m6_pcc_bandwidth_mhz` | primary-carrier channel width |
| `m6_pcc_pci` | primary physical cell ID — the handover/reselection signal |

### Per-band signal ranking — 4 new *labelled* metrics
`m6_sig_rsrp`, `m6_sig_sinr`, `m6_sig_nr5g_rsrp`, `m6_sig_nr5g_sinr`, each tagged
with `band` + `pci` as real Prometheus labels. This is what makes
`avg by (band) (m6_sig_sinr)` possible — ranking which band performs best from
the bands the M6 naturally roams (it can't be force-locked — see
`docs/m6-band-research.md`).

### DNS — third nameserver
Added the Verizon resolver `198.224.166.135` alongside Google/Cloudflare, so DNS
timing is sampled A/B/C across all three.

### Data-quality fix (ping)
`ping.py` now clamps impossible RTTs at the source. A suspended process or clock
jump could emit absurd values (e.g. 510000 ms); these are now clamped and dropped
from the jitter calc instead of polluting the dashboards.

---

## 2. Dashboard — before vs after

### Before (flat, 24 panels, no sections)
A single ungrouped list: HUD stats mixed in, per-target latency, DNS/TCP/HTTP,
one M6 signal panel, speedtest panels, Golden-Signal tiles at the bottom.

### After (organised into 4 labelled sections)

**Identity bar (top):** Current Status · Latency · Uptime · Packet Loss · Deployed Version

**▸ Latency, Loss & Reachability**
Google · Cloudflare · Gateway · Packet Loss · **Bufferbloat (NEW)** · DNS · TCP · HTTP Latency · Gateway Health

**▸ Throughput & Speedtests**
Saturation Down/Up · Speedtest Data (7d) · Avg Download/Upload gauges · Speedtest History · Daily throughput barcharts (14d)

**▸ Cellular Radio (M6)**
M6 Signal Quality · **M6 Carrier Aggregation (NEW)** · **M6 Handover History (NEW)** · **M6 Current Cell (NEW)** · **Band Performance Ranking (NEW)** · M6 Device Temperature

**▸ Logs & Events**
Towerwatch Event Log · Service Restarts

### Net-new panels (6)
| Panel | Type | What it shows |
|---|---|---|
| Bufferbloat (latency under load) | timeseries | RTT inflation during download/upload |
| M6 Carrier Aggregation | timeseries | how many carriers + aggregate bandwidth |
| M6 Handover History | state-timeline | PCI/band/eNB as colored bands — spot flapping |
| M6 Current Cell | stat | current PCI/band/eNB/RSRP/SINR at a glance |
| Band Performance Ranking | table | avg SINR/RSRP per band, sorted — which band is best |
| (Packet Loss HUD) | stat | fixed: was cramped/4-value, now one clean value per target |

Plus a focused visual pass: capped latency axes, reorganised sections, fixed
overlaps, M6 panel coloring corrected (categorical IDs no longer shown on a
good/bad gradient).

---

## 3. Infrastructure / process (not user-facing data)

- **Dashboard CI auto-sync** (`scripts/sync_dashboards.py` +
  `.github/workflows/sync-dashboards.yml`): `grafana/*.json` now pushes to
  Grafana Cloud automatically on merge — no more manual re-import. Docs:
  `docs/dashboard-sync.md`.
- **Tests:** 247 passing (was ~190); new suites for bufferbloat, M6, ping clamp,
  and band-tagged signal lines.
- **Research/plan docs:** `docs/m6-band-research.md` (why active band-locking is
  impossible on this hardware), `docs/plan-band-comparison.md` (the 3-piece plan).

---

## How to show "before" live

The Pis have been collecting the pre-existing metrics for months. To contrast:
- **Old data:** query e.g. `towerwatch_rtt_avg_google`, `towerwatch_m6_rsrp` —
  history goes back to first deploy.
- **New data:** `towerwatch_bufferbloat_download_delta_ms`,
  `towerwatch_m6_dev_temperature`, `towerwatch_m6_sig_sinr` — history starts
  2026-06-04/06 when each shipped. The gap in the time series *is* the "before."
