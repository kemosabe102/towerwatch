# Standstill trip verdict — July 2026

One-page verdict from the July 2–5 standstill capture: what the data proves, what it
can't, and the decision tree pending the one open input (the M6 Verizon plan).

Source data: `data-archive/standstill-jul2026/` (committed CSV export, retention-safe).
All analysis re-derivable from those CSVs.

---

## Headline

The phone (Pixel 9 Pro, Xfinity/Verizon MVNO) delivered a usably better experience than
the M6 hotspot (Verizon direct) on the same tower, same band (66), same cell (PCI 81) —
**despite the M6 having the better radio**. The gap lives above the RF layer. The July 5
quiet window shows the M6's *latency/loss fully recover overnight*, which points at
**congestion-time contention** as the dominant driver — but a flat low-throughput floor
that persists into the next morning keeps a **plan throttle** alive as a partial
explanation. The tie-breaker is the M6's plan tier, still unknown.

---

## What the data proves

### 1. Radio quality is exonerated (SINR reversal)
Same-cell overlap window (Jul 2–3, before the M6 scrape died):
| | Phone | M6 |
|---|---|---|
| SINR mean | 10.2 dB | **14.7 dB** |
| PCI (mode) | 81 | 81 |
| Band (mode) | 66 | 66 |

The M6's radio is **better** (higher SINR, same cell/band) yet loses every outcome. Radio
quality does not explain the phone's win.

### 2. Same-cell gate PASSES
Jul 2–3: `m6_pcc_pci` = 81 (100% of samples); `phone_pci` mode = 81 (988/1260). Both on
band 66. The comparison is valid — same serving cell. (Phone also visited PCI 143/282 and
band 13 — coverage-band steering, §"caveats".)

### 3. July 4 outcome asymmetry (peak day)
| Metric (Jul 4) | Phone | M6-link (Pi probes) |
|---|---|---|
| Download | ~20.7 Mbps mean (p90 48, max 66) | **2.4 Mbps mean (1.2 median)** |
| RTT (google) | 452 ms mean | 386 ms mean |
| Afternoon RTT | — | 508 ms (12–18h) |
| Loss (google) | — | 16.9% afternoon peak |

Download gap is ~10×. Latency is comparable (both cellular-bad); the felt difference is
**throughput and loss**, not latency.

### 4. Quiet-window verdict → congestion signature (with a caveat)
M6-link outcomes, Jul 5 02:00–06:00 vs Jul 4 peak afternoon:
| Metric | Jul 4 peak aftn | Jul 5 QUIET | Recovered? |
|---|---|---|---|
| RTT google | 508 ms | **51 ms** | ✅ fully |
| HTTP latency | 1933 ms | **179 ms** | ✅ fully |
| Loss google | 16.9% | **0.3%** | ✅ fully |
| Throughput | 1.2 Mbps | *0.0 (1 probe)* | ⚠️ inconclusive |

Latency, loss, and RTT **fully recover** overnight — the classic congestion fingerprint
(load comes off, link is healthy). A hard plan-throttle would keep latency degraded; it
didn't. **This weakens hypothesis (f) plan-throttle for the latency/loss dimensions.**

**Caveat:** the single throughput probe in the quiet window read 0.0 (likely a probe
failure — one run, 5 repeats), and M6 throughput stayed low (0.6 Mbps) at 09:41 the next
morning, past the congestion peak. Throughput does **not** cleanly recover the way latency
does. So throughput-specifically remains consistent with *either* a capacity floor under
sustained load *or* a plan bandwidth cap. Cannot separate these without the M6 plan.

### 5. Phone NR collapses under congestion (not absent)
`phone_nr_connected` at standstill: whole-trip mean **0.47**, Jul 5 quiet **1.0**, Jul 4
peak afternoon **0.22**. The phone **did** hold NR at the site (NR hypothesis does not die),
but NR availability **collapses under load** — itself a congestion signal, and a secondary
reason the phone felt rough at peak despite winning.

### 6. Deprioritization penalty is real and measurable (home baseline)
The phone line was deprioritized the entire trip (125 GB June usage > 100 GB threshold,
mid-June→mid-July cycle). Pre-trip **home** data (Jun 22–Jul 1, stable cell 282148/282144,
RSRP ≥ −110) shows the diurnal deprio fingerprint:
- Quiet-hour (02–06) download: **~42 Mbps**
- Evening-peak (17–22) download: **~15–26 Mbps** → **~45–63% peak-hour penalty**

This is a *within-deprioritized* diurnal gap at town-congestion levels. Because the penalty
scales with congestion severity and **July 4 congestion ≫ town congestion**, this is a
**lower bound** on what deprioritization cost the phone at standstill. Undeprioritized, the
phone's July 4 numbers would have been *at least* this much better — so July 4 phone
figures **understate** a premium line's capability.

---

## What the data CANNOT prove

- **M6 plan tier / throttle.** The single decisive unknown. `1.2 Mbps flat` at peak is
  consistent with both post-allotment hard throttle (f) and severe congestion contention.
  Latency recovery argues congestion; throughput's failure to recover argues throttle.
  **Only the M6's Verizon plan (name, allotment, premium-data-used, cycle) resolves it.**
- **True deprioritization crossing diff-in-diff.** Usage was already past 100 GB by Jun 22
  (the retention floor); the pre-crossing (un-deprioritized) home data is **expired**. We
  have the deprioritized-diurnal gap, not a before/after. The clean version is the mid-July
  cycle reset — see `docs/standstill-cycle-reset-experiment.md`.
- **M6 NR context on Jul 4.** The M6 scrape died Jul 3 evening (stale-creds bug, now fixed).
  July 4 peak has no M6 RF telemetry. Same-cell claims use the Jul 2–3 overlap only.
- **Phone NR *signal* quality.** `phone_nr_rsrp/sinr` sparse/absent (F2 capture gap); NR
  signal may be unobtainable via public Android API on this Pixel.

---

## Decision tree (pending the M6 plan answer)

```
M6 plan screenshots arrive:
├── M6 is on a hotspot plan with a hard post-allotment throttle (e.g. drops to
│   600 Kbps–3 Mbps after N GB), and premium-data is used up
│   → hypothesis (f) CONFIRMED. July 4 "collapse" is largely plan throttle.
│     The 1.2 Mbps flat is the throttle floor; congestion is secondary.
│     Phone wins because its (deprioritized) floor is still ~20 Mbps.
│
└── M6 has premium data remaining / no hard throttle in effect on Jul 4
    → hypothesis (f) REJECTED. July 4 is congestion + priority.
      Both lines carry Verizon traffic; phone (QCI 8/9) vs M6 hotspot (QCI 9 floor)
      priority difference + modem capability (c′: 4×4 MIMO scheduler extraction)
      explain the phone's win at the same SINR.
      Latency recovery overnight already supports this branch.
```

**Current lean:** congestion + priority (latency fully recovers overnight; NR collapses
under load; loss is contention-shaped), with plan-throttle unresolved for the throughput
dimension specifically. The overnight latency recovery is the single strongest piece of
evidence and it points away from a pure hard throttle.

---

## Artifacts corrected in this analysis

- The "100% gateway loss" and "m6 dead after Jul 3" were **NOT** caused by deploy `76ab9e2`
  (docs/tests only). Root cause: a **stale deployed `credentials.py`** missing
  `GATEWAY_IP_OVERRIDE = "10.0.1.1"`, which let gateway discovery freeze on the fallback
  `192.168.1.1` after the on-site restart → M6 polled the wrong IP (dead scrape) and the
  gateway ping target was a dead host (false 100% loss). Fixed 2026-07-06 by redeploying
  correct creds; verified M6 telemetry + gateway loss=0 flowing. See the durable-fix task
  for retiring the bug class (periodic re-resolution + observable resolved IP).
- `gateway_tcp_ms = 0` across all of Jul 4–5 in the archive is the same frozen-IP artifact
  (TCP-connecting to the dead 192.168.1.1). Exclude from Jul 4 analysis; real gateway RTT
  was healthy (~2.2 ms) throughout on the correct IP.
```
