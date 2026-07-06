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
quiet window shows the M6's *latency/loss fully recover overnight*, and a live retest on a
normal (non-holiday) Monday evening shows throughput **partly recovered to ~11 Mbps** (from
the ~1 Mbps holiday floor), with **upload faster than download** — the wrong shape for a
hard plan throttle. Together these point at **congestion-time contention** as the dominant
driver, with plan-throttle now **unlikely but not fully excludable**. The tie-breaker
remains the M6's plan tier, still unknown.

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

### 4. Quiet-window verdict → congestion signature (latency), throughput INCONCLUSIVE
M6-link outcomes, Jul 5 02:00–06:00 vs Jul 4 peak afternoon:
| Metric | Jul 4 peak aftn | Jul 5 QUIET | Recovered? |
|---|---|---|---|
| RTT google | 508 ms | **51 ms** | ✅ fully (n=240) |
| HTTP latency | 1933 ms | **179 ms** | ✅ fully (n=239) |
| Loss google | 16.9% | **0.3%** | ✅ fully (n=240) |
| Throughput | 1.2 Mbps | *0.0* | ⚠️ **n=1, unusable** |

Latency, loss, and RTT **fully recover** overnight — the classic congestion fingerprint
(load comes off, link is healthy). A hard plan-throttle would keep latency degraded; it
didn't. **This weakens hypothesis (f) plan-throttle for the latency/loss dimensions.**

**Throughput caveat — the claim rests on almost no data.** The scheduled M6 throughput
probe runs only ~3×/day; the whole trip has **14 runs total**, and the Jul 5 quiet window
has exactly **one** (04:19 → 0.0 Mbps, almost certainly a probe failure). So "throughput
doesn't recover overnight" is **not supported** — there is no overnight throughput sample
worth the name. What the 14 runs *do* show: M6 throughput collapsed the afternoon of Jul 3
(16:13 → 1.1 Mbps) and stayed pinned 0.5–0.7 Mbps continuously through Jul 5 evening — a
~2.5-day floor that is *too sustained* for pure evening congestion but cannot be
distinguished from a plan throttle by this data. **Resolving it needs a live discriminator,
not the Jul 5 morning data:** a manual speedtest in a genuine non-holiday quiet window
(see §"Live discriminator" below).

### 5. Phone NR collapses under congestion (not absent)
`phone_nr_connected` at standstill: whole-trip mean **0.47**, Jul 5 quiet **1.0**, Jul 4
peak afternoon **0.22**. The phone **did** hold NR at the site (NR hypothesis does not die),
but NR availability **collapses under load** — itself a congestion signal, and a secondary
reason the phone felt rough at peak despite winning.

**Decomposition (important):** because NR was mostly *gone* at peak (0.22), the phone was
running **mostly on LTE during the July 4 peak — and still beat the M6 by ~20× on
download.** Therefore the phone's **peak-time win cannot be attributed to NR access.** NR
may explain part of its *quiet-time* edge (extra capacity when the tower isn't busy), but
the thing we care about — why the phone was usable when the M6 collapsed at peak — happened
on LTE for both. That **narrows the July 4 win to: priority (QCI), plan throttle, or modem
capability (c')** — and removes NR from the peak-time explanation entirely. The M6 Verizon
plan screenshots now arbitrate nearly all of what remains.

### 6. Deprioritization penalty is real and measurable (home baseline)
The phone line was deprioritized the entire trip (125 GB June usage > 100 GB threshold,
mid-June→mid-July cycle). Pre-trip **home** data (Jun 22–Jul 1, stable cell 282148/282144,
RSRP ≥ −110) shows the diurnal deprio fingerprint:
- Quiet-hour (02–06) download: **~42 Mbps**
- Evening-peak (17–22) download: **~15–26 Mbps** → **~45–63% peak-hour penalty**

This is a *within-deprioritized* diurnal gap, and it is **NOT a clean deprioritization
penalty** — it is a **composite of congestion + deprioritization**. A *premium* line also
slows at peak hours (the tower is busy for everyone); the peak/quiet gap captures that
baseline congestion PLUS whatever extra the QCI-9 deprioritization adds on top. So:

- The ~45–63% gap is an **upper bound** on the deprioritization penalty *at town-congestion
  levels* — most of it may be ordinary congestion that a premium line would feel too.
- It is **not transferable to standstill** in either direction. Standstill congestion,
  tower, and load are different; the town number does not scale to July 4.
- The deprioritization penalty is **isolated only by the cycle-reset experiment**
  (`docs/standstill-cycle-reset-experiment.md`), which holds location constant and flips
  *only* the priority tier — the peak/quiet gap before vs after the reset is the clean
  measurement this composite gap cannot provide.

What this data *does* establish: the phone line was deprioritized the whole trip, and
congestion-time slowdown at the home cell is real and sizable. It does **not** quantify how
much of July 4's phone-vs-M6 gap was deprioritization — that stays open.

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

**Current lean:** congestion + priority. Latency fully recovers overnight; NR collapses
under load; loss is contention-shaped; and the live Monday-evening retest (~11 Mbps, upload
> download) is the wrong shape for a hard throttle and shows the holiday floor has partly
lifted. Plan-throttle is now **unlikely** (not merely unresolved). The overnight latency
recovery plus the live-retest asymmetry are the two strongest pieces of evidence, both
pointing away from a hard throttle. Final confirmation still waits on the M6 plan.

---

## Live discriminator (congestion vs throttle, run 2026-07-06 evening)

The Jul 5 quiet-window throughput data is unusable (n=1). A cleaner test is available now:
the Pi is remote-reachable and 2026-07-06 (Monday) is a **normal non-holiday** evening —
July 4 holiday congestion is gone. Manual `towerwatch-speedtest` runs against the M6 tonight
discriminate directly:

- **M6 recovers to 25–40 Mbps** → the Jul 3–5 floor was congestion (holiday load). Plan
  throttle (f) **dies**. July 4 collapse = congestion + priority.
- **M6 still pinned at 0.5–3 Mbps** on a normal quiet Monday evening → a persistent floor
  independent of holiday congestion = **plan throttle confirmed** (or a hard capacity cap).

**Result (2026-07-06 ~16:05 PDT, `triggered_by=post-review-live`, n=3):**
download **11.3 / 11.1 / 12.7 Mbps** (tight ~11–13), upload **16.2 / 17.8 / 14.3 Mbps**.
The download<upload asymmetry is consistent across all three runs.

**Interpretation — intermediate, and it argues AGAINST a hard throttle:**
- **Not** recovered to 25–40 Mbps → holiday congestion wasn't the *whole* story; the link
  is still below its Jul 2–3 pre-collapse baseline (41–56 Mbps).
- **Not** pinned at 0.5–3 Mbps → clearly above the Jul 3–5 holiday floor (~4× the 0.6–1.2
  Mbps seen then). So the July 4 collapse **was substantially holiday congestion** — it has
  partly lifted on a normal Monday.
- **Upload (16–18) > download (11).** A classic post-allotment hotspot **throttle caps the
  downlink hard** (600 Kbps–3 Mbps) — an *upload-faster-than-download* result is the
  opposite signature. This points away from plan throttle (f) and toward **downlink
  congestion / capacity limiting** that persists at moderate levels even off-peak.

**Provisional verdict shift:** hypothesis (f) hard-throttle is **weakened further** — the
link isn't floored on a normal evening, and the asymmetry is wrong for a throttle. The
residual ~11 Mbps downlink cap (vs 40+ baseline) reads as ongoing congestion/capacity, not
a plan cap. Caveat: 16:05 is late-afternoon, not the deepest quiet window; a 02:00–04:00
run would strengthen it. Still, combined with the overnight latency recovery, the weight of
evidence now sits on **congestion + priority**, with plan-throttle unlikely but not fully
excludable until the M6 plan is known.

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
