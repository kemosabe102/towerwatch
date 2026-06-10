# Phone RF logger

Capture the **phone's radio layer** — serving LTE/NR signal, neighbor cells, and
carrier aggregation — and land it in the same Grafana as the M6 hotspot, so the
**Phone RF (standstill-phone)** dashboard row sits next to the M6's cellular panels.
The question this answers: *why does the Pixel feel faster than the M6 on the same
tower?*

This is the **RF complement** to [`phone-compare.md`](phone-compare.md). That tool
measures the phone's **throughput and latency** over ADB; this adds the **signal
layer** the M6 firmware can't expose. Together they make the phone-vs-hotspot
comparison diagnostic:

- **Matched RF / band / CA but the phone still pulls ~2× throughput** → MIMO or
  antenna order (hardware). See the MIMO caveat below — this is *inferred*, not measured.
- **Phone camps on 5G NR while the M6 sits on LTE** → the phone has capacity access
  the hotspot doesn't.
- **Everything similar, phone only pulls ahead under load** → deprioritization.

The data comes from a **separate Android app, `towerwatch-rflogger`** (package
`net.towerwatch.rflogger`), built and run on the Pixel itself — *not* from this repo.
It's a session-based foreground logger: you start a session on the phone, it samples
the Telephony API every ~30 s and pushes to the towerwatch Grafana stack as
`host=standstill-phone`. Build/install/run instructions live in that app's README;
this doc just orients a dashboard reader and pins the **push contract** the app must
follow.

> **Why a separate app?** Neighbor cells, NR signal, and CA carrier counts come from
> Android's `TelephonyManager`/`PhysicalChannelConfig` — there's no way to get them
> over ADB or from the M6. The app is the only path to that data.

---

## What the dashboard row shows

All panels are fixed to `host=standstill-phone` (they do **not** follow the
`$location` picker — the phone is a distinct host):

| Panel | Metric(s) | Reading it |
|---|---|---|
| Phone Serving LTE Signal | `phone_rsrp`, `phone_rsrq`, `phone_sinr` | The phone's serving-cell LTE quality. |
| Phone NR Signal (5G) | `phone_nr_rsrp`, `phone_nr_rsrq`, `phone_nr_sinr` | **The M6's blind spot.** Present only when the phone is on 5G NR. |
| Phone Neighbor Cells | `phone_neighbor_rsrp_max`, `phone_neighbor_rsrp_min`, `phone_neighbor_count` | Aggregated neighbor signal. Needs location services on the phone. |
| Phone CA Carriers | `phone_ca_count` | Component-carrier count held by the phone. |
| Phone Band Ranking | `phone_sig_rsrp`, `phone_sig_sinr` by `band` | Avg signal per band the phone roamed, with dwell-time samples. |

For throughput/latency, open **`dashboard-compare.json`** and set
`location_a=standstill`, `location_b=standstill-phone`. The phone reuses the M6's
`speedtest_*` and `rtt_*` metric names, so that overlay works with no dashboard
edits.

## Same-cell sanity check

Before drawing any conclusion, confirm both radios are on the **same serving cell** in
the window you're comparing: check the phone's `phone_pci` / `phone_band` against the
M6's `m6_pcc_pci` / `m6_band`. If they're on different cells or bands, a throughput
gap is expected and tells you nothing about MIMO or deprioritization.

## MIMO caveat

The 4×4-vs-2×2 antenna-order hypothesis is **not measurable**. No public Android API
exposes MIMO rank. It can only be *inferred* — if RF, band, and CA all match but the
phone still doubles the M6's throughput, MIMO/antenna is the remaining explanation by
elimination. Don't present it as a measured value.

---

## Push contract (authoritative — the app must match this)

The app replicates towerwatch's Grafana push exactly. Source of truth in this repo:
`src/towerwatch/clients/grafana.py` (transport), `src/towerwatch/tick.py`
(`format_influx_line` / `format_speedtest_line` / `format_band_sig_line` / `_common_tags`),
`src/towerwatch/config.py` (URL + measurement), `src/towerwatch/probes/m6.py`
(`_safe_int` sentinel), and `scripts/phone_compare.py` (the `host=standstill-phone` tag set).

**Transport**
- `POST` to the push URL, default
  `https://prometheus-prod-67-prod-us-west-0.grafana.net/api/v1/push/influx/write?precision=s`.
  Make this a Settings field — Grafana Cloud stacks live on different `prometheus-prod-NN` hosts.
- Headers: `Authorization: Basic base64(INSTANCE_ID:API_KEY)`, `Content-Type: text/plain`,
  and `Content-Encoding: gzip` **only when** the body is gzipped.
- Body = Influx line protocol, `\n`-joined. Success = HTTP status `< 300`.

**Line shape**
- Measurement = `towerwatch`. **Timestamp in whole seconds** (matches `precision=s`).
- Common tags on every line:
  `host=standstill-phone,carrier=verizon,connection_type=5g_cellular,experiment=none`
  (all Settings-configurable). Add `triggered_by=rf-logger` on reused throughput lines —
  the ADB tool uses `triggered_by=phone-compare`, so this keeps the sources distinct in raw queries.
- **Field types:** signal ints are **plain** (`phone_rsrp=-95`); byte counts use the `i`
  suffix (`speedtest_download_bytes=123i`); Mbps are plain floats. **Omit any field whose
  value is unavailable** — mirror `_safe_int` in `probes/m6.py`: a parse failure or sentinel
  (`Integer.MAX_VALUE` = `2147483647` on Android; `-32768` on the M6) → drop the field from
  the line entirely, never push `0` or `MAX_VALUE`.
- **Required:** every push includes a plain **`connected=1`** field (mirrors
  `scripts/phone_compare.py` line 240). Without it `host=standstill-phone` never appears in
  the compare dashboard's `$location_a/$location_b` dropdowns — they're populated by
  `label_values(towerwatch_connected, host)`.

**Metric names**
- Reuse `speedtest_download_mbps` / `speedtest_upload_mbps` / `speedtest_download_bytes` /
  `speedtest_upload_bytes` and `rtt_avg_google` (etc.) for throughput/latency so the compare
  dashboard overlays with zero edits.
- New `phone_*` names for RF (own panels):
  `phone_rsrp, phone_rsrq, phone_sinr, phone_nr_rsrp, phone_nr_rsrq, phone_nr_sinr,
  phone_cell_id, phone_pci, phone_band, phone_tac, phone_neighbor_count,
  phone_neighbor_rsrp_max, phone_neighbor_rsrp_min, phone_ca_count`.
- Per-band line mirrors `format_band_sig_line` — emit **only** when a band value is present
  AND at least one signal field rode along (otherwise emit nothing):
  ```
  towerwatch,<common_tags>,band=<n>,pci=<n> phone_sig_rsrp=<n>,phone_sig_sinr=<n> <ts>
  ```
  This enables `avg by (band) (...)` ranking against the M6's identically-shaped line.

**Cardinality guard**
- **Do not** emit one series per neighbor PCI — neighbors churn and would blow the free-tier
  ~10k series cap. Emit aggregates only: `phone_neighbor_count`,
  `phone_neighbor_rsrp_max`, `phone_neighbor_rsrp_min`.
