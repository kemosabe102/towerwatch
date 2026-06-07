# Response to the metrics review

Thanks — this was a genuinely useful review. We acted on it. Summary of what we
verified, what we shipped, and one correction to the framing that changes the
antenna picture.

## TL;DR

- **Experiment tag: shipped.** Every metric now carries an `experiment` label.
  `avg by (experiment) (...)` is now a one-query on/off comparison across signal,
  throughput, bufferbloat, loss, and tx power. This is the big one — thank you.
- **MIMO rank / neighbor cells / CQI / MCS / BLER: not available on this
  hardware.** We grepped a live dump (as you advised) — none are in the
  firmware's `model.json`. Details + evidence below.
- **Correction: this link is effectively LTE, not 5G.** The data doesn't support
  the "5G SINR ~8 / co-channel interference" premise. That reshapes the antenna
  rationale — see below.
- **tx_level reframe + percentiles: adopted as dashboard analysis** (no new
  metrics, exactly as you said).

## 1. Experiment tag — done

Added `EXPERIMENT_LABEL` to per-site credentials → an `experiment` Influx tag on
every line (mirrors our existing `carrier` / `triggered_by` pattern). Default
`"none"`. To run an A/B: set the label, deploy (the restart marks the boundary),
run, then reset to `"none"` and redeploy. Now:

```promql
avg by (experiment) (towerwatch_m6_sig_sinr)
avg by (experiment) (towerwatch_bufferbloat_download_delta_ms)
avg by (experiment) (towerwatch_m6_tx_level)
```

Cardinality is guarded by resetting to `"none"` between runs (free-tier ~10k
series cap).

## 2. The new radio metrics — verified absent in firmware

You flagged these as firmware-dependent and said to grep first. We did, against
**both** the stored fixture (2026-04-25) **and a fresh live pull** from the
device this session. Results — case-insensitive search of the full `model.json`:

| Metric you suggested | grep terms | In firmware? |
|---|---|---|
| MIMO rank / layers | `rank` `mimo` `layer` `txmode` `transmission` | **No** |
| Neighbor cells | `neighbor` `cellmeas` `intrafreq` `interfreq` `measresult` | **No** |
| CQI | `cqi` | **No** |
| MCS / modulation | `mcs` `modulation` `qam` | **No** |
| BLER | `bler` `errorrate` `retrans` `crc` | **No** |
| PRB utilization | `prb` `resourceblock` `utilization` | **No** (and you noted it's tower-side anyway) |
| Tx power headroom | `phr` `headroom` `pusch` `pucch` | **No** |

The `insight` section is a feature-flag stub (`{"supported": true, ... "active":
false}`) with no data; `eventlog` is empty. The device is a **Netgear MR6500 on
the latest (though a few years old) firmware, apiVersion 2.0**. These are
Qualcomm-modem diagnostics the consumer firmware doesn't surface over the HTTP
API. The only path to them would be telnet/AT commands — which we established in
prior research is locked on this Verizon-carrier-locked unit
(`docs/m6-band-research.md`).

So MIMO rank and neighbor cells — your two highest-value adds — **aren't
buildable here.** Not gating the antenna test on them, per your own advice.

## 3. Correction: it's an LTE link, and we have no usable 5G signal

This is the one that matters for the antenna rationale. Your framing was "your 5G
SINR (~8) is poor because of co-channel interference, and a directional antenna
helps if that interference is directional." But our data says:

- **Live right now:** `currentNWserviceType = LteService`, `nr5gAttached =
  false`. The device is on **LTE B66** (SINR 20, RSRP −96, tx 15). Healthy LTE.
- **Even when it *was* NR-attached** (April fixture: `nr5gAttached: true`,
  `Nr5gService`, `endc: true`), **both** 5G signal sources —
  `signalStrength.nr5gRsrp/Sinr` *and* `diagInfo.nr5gsigRsrp/Snr` — read the
  `-32768` "no measurement" sentinel. This firmware **never reports usable 5G NR
  signal numbers.**
- Over the last day it shows **only band 66, only LTE** — it isn't roaming bands
  or sitting on NR in practice.

So we don't have a 5G SINR of ~8 to improve — we have an LTE SINR of ~20, and no
visibility into 5G signal at all. The implication for the antenna: the testable
hypothesis isn't "reject a directional 5G interferer" (we can't see 5G), it's
"**does a better antenna improve the LTE B66 link** — higher RSRP/SINR, higher
throughput, lower bufferbloat under load, and lower `tx_level` on the uplink."
That's fully measurable with what we have + the experiment tag.

Were you reading the ~8 off a different source (the device UI? a one-off NR
moment?) — or should we treat this as an LTE-only evaluation? Your call on
whether the antenna is still worth it given it's an already-decent LTE link.

## 4. tx_level + percentiles — adopted as analysis

Both land as dashboard panels, no new data, exactly as you framed:

- **`m6_tx_level` (lower = better):** surfaced with that note. Live value moves
  (−50 in April, 15 now), so it's a usable uplink-effort proxy. A drop after an
  antenna install = better uplink path.
- **Percentiles on existing data:** confirmed
  `quantile_over_time(0.95, rtt_avg_google[6h])` → 185 ms and
  `max_over_time(rtt_max_google[6h])` → 354 ms live. Tail panels added per
  experiment window; no extra ping cost.

## What we'd still value your take on

Given it's an LTE B66 link with decent SINR (~20) and no 5G visibility:

1. Is a directional antenna still worth testing, or is the gain ceiling low on an
   already-healthy LTE anchor?
2. With rank/neighbors off the table, is the {RSRP, SINR, throughput, bufferbloat
   delta, tx_level} × experiment-tag set enough to call an antenna result
   convincingly — or is there a confound we're missing on LTE specifically?
3. Any LTE-specific angle we're under-weighting now that the 5G story is out
   (e.g. the carrier-aggregation count `m6_carrier_count` as a throughput-ceiling
   signal — it's at 1 SCC right now)?
