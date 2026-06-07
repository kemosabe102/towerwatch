# Towerwatch — complete metrics inventory (for external review)

**Purpose:** a self-contained catalog of everything towerwatch collects, to hand
to a collaborator for feedback on coverage and gaps. Generated from the live
Prometheus instance + source of truth, 2026-06-06.

## Context in one paragraph

Towerwatch is a Raspberry Pi network-quality probe. It runs a **60-second main
loop**, ships metrics to Grafana Cloud (Prometheus, Influx line protocol) and
structured logs to Loki. Two live sites: **`standstill`** (Verizon 5G/LTE via a
Netgear M6 hotspot, the troubleshooting target) and **`home`** (Comcast cable,
baseline). The goal is a long-running evidence dataset for diagnosing a flaky
cellular link. Hard constraint: **30 GB/month data budget** per site, so probe
cadence is deliberately tuned (the M6 can't be force-locked to a band — it's
carrier-locked — so band comparison is *passive*).

**Every metric carries these labels:** `host` (site), `carrier`, `connection_type`.
Metric naming convention: per-target metrics bake the target into the name
(`rtt_avg_google`, not a `target` label) — except a few sanctioned label
dimensions noted below. Units are `_ms` throughout (not seconds).

Live count: **74 metrics** on standstill. Legend: 🆕 = added in the last few days.

---

## 1. Reachability & latency (ICMP ping) — every 60 s

10-probe ICMP burst to each of 3 targets: **google (8.8.8.8)**,
**cloudflare (1.1.1.1)**, **gateway (carrier gateway, auto-discovered)**.

| Metric (per target) | Unit | Meaning |
|---|---|---|
| `rtt_avg_{target}` | ms | mean round-trip time |
| `rtt_min_{target}` | ms | best RTT in the burst |
| `rtt_max_{target}` | ms | worst RTT in the burst |
| `jitter_{target}` | ms | RTT std-dev (RFC 3550) |
| `pkt_loss_{target}` | % | packet loss |
| `connected_{target}` | 0/1 | reachable this tick |
| `connected` | 0/1 | aggregate (any target up) — the uptime signal |

🆕 RTTs are now clamped at the source (a suspended process / clock jump used to
emit absurd values like 510000 ms; those are dropped from jitter).

## 2. DNS resolution — every 60 s

`dnspython` against explicit nameservers (bypasses systemd-resolved).

| Metric | Unit | Meaning |
|---|---|---|
| `dns_resolve_ms_8_8_8_8` | ms | resolution time via Google DNS |
| `dns_resolve_ms_1_1_1_1` | ms | via Cloudflare DNS |
| 🆕 `dns_resolve_ms_198_224_166_135` | ms | via Verizon DNS (carrier resolver) |

## 3. TCP connect — every 60 s

| Metric | Unit | Meaning |
|---|---|---|
| `tcp_connect_ms` | ms | socket handshake time to `8.8.8.8:443` |

## 4. Gateway health — every 60 s

| Metric | Unit | Meaning |
|---|---|---|
| `gateway_tcp_ms` | ms | TCP connect to the local gateway |
| `gateway_http_ms` | ms | HTTP response time from the gateway |
| `gateway_clients` | count | connected-device count (Orbi only; absent on M6) |

## 5. HTTP latency & throughput

| Metric | Unit | Cadence | Meaning |
|---|---|---|---|
| `http_latency_ms` | ms | every 5 min | timed 10 KB fetch from a CDN |
| `http_throughput_mbps` | Mbps | ~4×/day random | timed 1 MB download |
| `http_throughput_ms` | ms | ~4×/day | download duration |
| `http_throughput_bytes` | bytes | ~4×/day | bytes used (budget tracking) |
| 🆕 `http_upload_mbps` | Mbps | ~4×/day | timed upload |
| 🆕 `http_upload_ms` | ms | ~4×/day | upload duration |
| 🆕 `http_upload_bytes` | bytes | ~4×/day | upload bytes used |

## 6. 🆕 Bufferbloat (latency under load) — runs with each throughput test

Measures how much RTT inflates while the link is saturated. *Not yet visible on
standstill until the next windowed throughput run.*

| Metric | Unit | Meaning |
|---|---|---|
| `bufferbloat_rtt_idle_ms` | ms | baseline RTT, no load |
| `bufferbloat_rtt_download_ms` | ms | RTT during download |
| `bufferbloat_rtt_upload_ms` | ms | RTT during upload |
| `bufferbloat_download_delta_ms` | ms | **inflation under download load** |
| `bufferbloat_upload_delta_ms` | ms | **inflation under upload load** |

## 7. Cloudflare speedtest — ~2×/day + manual SSH-triggered

Multi-stream adaptive test against speed.cloudflare.com.

| Metric | Unit | Meaning |
|---|---|---|
| `speedtest_download_mbps` | Mbps | adaptive multi-stream download |
| `speedtest_upload_mbps` | Mbps | adaptive multi-stream upload |
| `speedtest_download_bytes` / `speedtest_upload_bytes` | bytes | budget tracking |

Carries a `triggered_by` **label** (operator name) on manual runs.

## 8. Cellular radio (Netgear M6) — every 60 s, standstill only

Polls the M6's `/api/model.json`. This is the richest section and the
troubleshooting focus.

### Signal quality
| Metric | Unit | Meaning |
|---|---|---|
| `m6_rsrp` | dBm | LTE reference signal received power |
| `m6_rsrq` | dB | LTE reference signal received quality |
| `m6_sinr` | dB | LTE signal-to-interference-plus-noise |
| `m6_rssi` | dBm | received signal strength |
| `m6_bars` | 0–5 | UI signal bars |
| `m6_nr5g_rsrp` / `m6_nr5g_rsrq` / `m6_nr5g_sinr` | dBm/dB | 5G NR equivalents (when on NR) |
| `m6_radio_quality` | — | device composite quality score |
| `m6_rx_level` / `m6_tx_level` | — | receive/transmit power levels |

### Serving-cell identity
| Metric | Meaning |
|---|---|
| `m6_cell_id` | full LTE cell ID (28-bit) |
| `m6_enb_id` | tower ID (high 20 bits of cell_id) — handover between towers |
| `m6_sector_id` | sector (low 8 bits) — reselect within a tower |
| `m6_band` | current band number |
| `m6_earfcn` / `m6_earfcn_ul` | downlink/uplink channel number |
| `m6_lac` | location area code |
| `m6_mcc` / `m6_mnc` | mobile country/network code |

### 🆕 Carrier aggregation & primary carrier
| Metric | Unit | Meaning |
|---|---|---|
| `m6_carrier_count` | count | aggregated carriers (throughput-cliff signal) |
| `m6_agg_dl_bandwidth_mhz` | MHz | total aggregated downlink width |
| `m6_pcc_band` | — | primary-carrier band |
| `m6_pcc_bandwidth_mhz` | MHz | primary-carrier channel width |
| `m6_pcc_pci` | — | primary physical cell ID (handover signal) |
| `m6_ca_scc_count` / `m6_ca_scc_declared` | count | secondary-carrier counts |

### 🆕 Device health
| Metric | Unit | Meaning |
|---|---|---|
| `m6_dev_temperature` | °C | chassis temp — **prime suspect for afternoon thermal throttling** |
| `m6_dev_temp_critical` | 0/1 | device thermal-critical flag |
| `m6_thermal_state` | code | 0=normal 1=warm 2=hot 3=critical |
| `m6_eth_speed_mbps` | Mbps | Ethernet negotiated speed (catches drop to 100M) |
| `m6_uptime_s` | s | modem uptime (drops correlate with outages) |

### Attachment state
| Metric | Meaning |
|---|---|
| `m6_lte_attached` / `m6_nr5g_attached` | 0/1 attached to LTE / 5G NR |
| `m6_endc_enabled` | 0/1 EN-DC (LTE+NR dual connectivity) |
| `m6_service_type` | code: 0 none, 3 LTE, 4 NR5G NSA, 5 NR5G SA |

### 🆕 Per-band signal ranking (band/pci as **labels**)
| Metric | Unit | Meaning |
|---|---|---|
| `m6_sig_rsrp` | dBm | RSRP **tagged with `band` + `pci`** |
| `m6_sig_sinr` | dB | SINR tagged with band + pci |
| `m6_sig_nr5g_rsrp` / `m6_sig_nr5g_sinr` | dBm/dB | 5G equivalents (when on NR) |

These exist so `avg by (band) (m6_sig_sinr)` ranks which band performs best, from
the bands the M6 naturally roams. (Active band-locking is impossible on this
carrier-locked unit — see `docs/m6-band-research.md`.)

## 9. Service / meta

| Metric | Meaning |
|---|---|
| `build_info` | running version (labels: version, build_date, link_max_download/upload_mbps) |
| `service_restart` | restart marker |
| `metric_interval_s` | configured loop interval |
| `collection_duration_ms` | how long the tick's probes took |

## 10. Structured log events (Loki, not Prometheus)

Lifecycle/error events, queryable in Loki by `event=`:
`service_started`, `service_restarted`, `connection_down`, `connection_restored`,
`ping_failed`, `dns_failed`, `speedtest_complete/timeout/failed`,
`http_throughput_complete/failed`, `http_upload_complete/failed`,
`metrics_push_failed`, `log_buffer_flushed`, `partition_not_detected`,
`service_heartbeat`, `outage_recorded`, `annotation_push_failed`.

Outages ≥10 min also POST a sticky region annotation to Grafana.

---

## What we'd love your perspective on

1. **Coverage gaps for cellular troubleshooting.** Given the M6 telemetry above,
   what are we *not* collecting that would help diagnose intermittent
   slowdowns/drops on a fixed-location 5G/LTE link? (e.g. neighbor-cell
   measurements, MIMO layers / rank indicator, BLER, CQI, Tx power headroom,
   PRB utilization, latency percentiles vs averages?)

2. **Signal vs. outcome correlation.** We have signal (RSRP/SINR), cell identity
   (band/PCI/eNB), and outcomes (throughput, latency, loss). Are we capturing the
   right dimensions to actually *attribute* a slowdown to a cause (congestion vs.
   weak signal vs. thermal vs. handover thrashing)?

3. **Cadence & budget.** 60 s for signal, ~2×/day for speedtests, ~4×/day for
   http throughput — within a 30 GB/month cap. Is the temporal resolution right
   for catching transient events, or are we aliasing past short-lived problems?

4. **Aggregation/statistics.** We mostly store instantaneous/averaged values. Are
   there metrics where we should capture distributions/percentiles at the source
   instead (e.g. per-tick RTT p95, loss bursts)?

5. **Anything that's noise.** Metrics here that are low-value or redundant and
   could be dropped to save budget for something better.
