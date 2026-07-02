# Standstill field playbook — phone vs M6, why is the phone better?

A Claude-driven, on-site investigation for the **standstill** site over a multi-day visit
(quiet baseline → congested peak → recovery). The question:

> The phone gives a better *experience* on the same Verizon network. **Why?** And if the RF looks
> similar, what's left to explain it?

You'll be on-site with a laptop + Tailscale, running Claude in this repo. Claude drives the queries
and reads the data; you do the physical steps (start the phone session, trigger speedtests, observe
hardware). This doc is the phased script.

---

## Start a session with Claude

Paste this to begin (or resume) an on-site session:

> We're on-site at standstill working `docs/field-playbook-standstill.md`. Load it plus
> `towerwatch-rflogger/docs/FIELD_CATALOG.md` and `docs/phone-rf-logger.md`. Start Phase **N**.

## The three data sources (know which is which)

All land on one Grafana stack (measurement `towerwatch`), split by `host` tag:

| host | device | method notes |
|---|---|---|
| `standstill` | M6 hotspot (Raspberry Pi probe) | ICMP ping; **4G/LTE-locked by choice** (NR flapped, so it was disabled) |
| `standstill-phone` + `triggered_by=rf-logger` | Pixel RF logger app | **TCP-connect** RTT (not ICMP); the RF layer (NR/neighbors/CA) |
| `standstill-phone` + `triggered_by=phone-compare` | Pixel ADB tool | true ICMP for `google`; throughput/latency only, no RF |

**Read note:** phone `rtt_*`/`pkt_loss_*` (rf-logger) are TCP-connect, the M6's are ICMP — compare
each device's own *delta*, not absolute ms. Cross-device **RF** (SINR/RSRP) is **directional only**
(different chipsets/scales). Only **loss & latency** are clean cross-device. **Throughput is NOT
cross-device comparable** (different probes) — use each device's own quiet→peak degradation.

## Query helper

`./scripts/twq.sh '<promql>'` (instant) or `./scripts/twq.sh '<promql>' <lookback_s> <step>` (range).
Reads `GRAFANA_READ_KEY` from `credentials.py`. Every `[tested ✓]` query below was validated against
live data.

---

## ★ Minimum Viable Capture (the must-do tier — if the trip gets rushed)

If nothing else happens, do these three — they yield a defensible answer:

1. **Pre-trip (5 min, potentially decisive): the SIM/tier check.** See Phase −1.
2. **Outcome baseline, both devices, quiet window** — loss, throughput p10, latency p95/jitter, each
   device vs *its own* baseline. See Phase 1.
3. **One congested-window read with the congestion gate** — confirm both devices degrade vs baseline
   (else the read is VOID, not a finding). See Phase 2.

Everything else (full hypothesis ladder, CA head-to-head, controlled+uncontrolled pair) is
**optional — "if time and energy permit."** This is a holiday; a rushed capture of the three above
beats a half-finished elaborate protocol.

**Primary outcome metric = "it doesn't drop":** connectivity-loss events + packet loss. Secondary =
jitter/p95 if real-time (calls) matter. (This follows the felt symptom — adjust if yours differs.)

---

## Phase −1 — Pre-trip SIM / priority-tier check (do this FIRST)

**Why:** Verizon consumer priority is a two-step ladder — **QCI 8 (premium)** vs **QCI 9
(deprioritized floor)**; there's nothing below QCI 9. This check can nearly *settle* the top
hypothesis before you drive out.

Grab **three** things from the accounts:
1. **M6 SIM** — Verizon-direct hotspot/data-only? (likely QCI 9)
2. **Phone line** — Xfinity Mobile plan tier (By-The-Gig / Unlimited Plus / Premium are usually
   **QCI 8**). Confirm it's a premium tier.
3. **This cycle's premium-data burn on each** — a QCI-8 line over its monthly allotment is
   *effectively* QCI 9 right now.

**Interpretation:**
- Phone QCI 8 vs M6 QCI 9 → **priority is the odds-on explanation**; July 4 becomes *confirmation,
  not do-or-die*.
- Both QCI 9 (phone not premium, or over cap) → priority is NOT the differentiator; RF / CA / antenna
  / selection-freedom carry it.
- Note: QCI narrows, it doesn't fully confirm — even with a tier gap, NR/CA/antenna can stack on top.

QCI is **not in Android's public API**, so it can't be logged; on July 4 it's inferred behaviorally
(Phase 3, hypothesis 1).

---

## Phase 0 — Setup & prove-it-flows (arrival day)

**Goal:** confirm both devices are reporting at the site before you trust any comparison.
**Entry gate for everything downstream: phone `phone_*` series flowing at standstill.**

**Human steps:** start a phone RF-logger session at the site (leave it plugged in for multi-hour
runs); confirm the M6/Pi is powered and on Tailscale.

**Claude steps:**
- Pi + phone both alive (heartbeat): `[tested ✓]`
  ```
  ./scripts/twq.sh 'count by (host)(towerwatch_connected)'
  ```
  Expect `standstill` and `standstill-phone` both present.
- Phone RF is real, not all-null (run after the session has a few minutes): `[tested ✓]`
  ```
  ./scripts/twq.sh 'towerwatch_phone_rsrp{host="standstill-phone"}'
  ./scripts/twq.sh 'towerwatch_phone_nr_connected{host="standstill-phone"}'
  ```
- Full on-device smoke test: see `towerwatch-rflogger/README.md` §"Verify RF reads".

**What good looks like:** heartbeats for both hosts; `phone_rsrp` a plausible dBm (−80…−120).
**Decision:** if phone `phone_*` isn't flowing, fix that before proceeding — the comparison needs it.

---

## Phase 1 — Per-device baseline + same-cell gate (quiet window)

**Goal (headline read is WITHIN-SUBJECT):** establish each device's own quiet baseline for the
outcome metrics. Cross-device is secondary.

**Outcome baseline (the dependent variable):** `[tested ✓]`
```
# Reachability / loss — the primary "doesn't drop" metric
./scripts/twq.sh 'towerwatch_pkt_loss_google{host="standstill"}' 21600 300
./scripts/twq.sh 'towerwatch_pkt_loss_google{host="standstill-phone"}' 21600 300
# Latency p95 + jitter (clean cross-device — same ping targets)
./scripts/twq.sh 'quantile_over_time(0.95, towerwatch_rtt_avg_google{host="standstill"}[1h])' 21600 300
./scripts/twq.sh 'quantile_over_time(0.95, towerwatch_rtt_avg_google{host="standstill-phone"}[1h])' 21600 300
./scripts/twq.sh 'towerwatch_jitter_google{host="standstill"}' 21600 300
./scripts/twq.sh 'towerwatch_jitter_google{host="standstill-phone"}' 21600 300
# Throughput floor p10 (DIRECTIONAL cross-device — read each device's own change)
./scripts/twq.sh 'quantile_over_time(0.1, towerwatch_http_throughput_mbps{host="standstill"}[6h])' 21600 300
./scripts/twq.sh 'quantile_over_time(0.1, towerwatch_speedtest_download_mbps{host="standstill-phone"}[6h])' 21600 300
```

**Same-cell gate (for the cross-device RF claims only):** `[tested ✓]`
```
./scripts/twq.sh 'towerwatch_m6_pcc_pci{host="standstill"}'
./scripts/twq.sh 'towerwatch_phone_pci{host="standstill-phone"}'
./scripts/twq.sh 'towerwatch_m6_band{host="standstill"}'
./scripts/twq.sh 'towerwatch_phone_band{host="standstill-phone"}'
```
Same PCI + band → cross-device RF comparison is meaningful. **Plan B if PCIs differ:** try forcing
reselection onto a shared cell (band lock / airplane-mode toggle on the phone). If that fails, fall
back to **same-band, outcomes-only** (drop the RF comparison) — and note it enables the
"selection freedom" hypothesis (Phase 3-#4).

**Record as fixed conditions:** whether the M6 has an external antenna the phone lacks; the "5 bars"
icon vs measured SINR (`towerwatch_m6_bars` vs `towerwatch_m6_sinr`).

**What good looks like:** stable per-device baselines; same-cell confirmed (or Plan B chosen).

---

## Phase 2 — Congested-window head-to-head (peak day)

**Goal:** capture both devices under load. **"Must-not-fail" only if Phase −1 didn't already settle
it.**

**★ Congestion gate (do this BEFORE interpreting anything):** confirm *both* devices' outcomes
degrade vs their Phase-1 baseline. **If neither moved, the priority read is VOID, not negative** —
there was nothing to reveal it. `[tested ✓]`
```
# loss + p95 rising on both = real congestion
./scripts/twq.sh 'towerwatch_pkt_loss_google{host="standstill"}' 7200 120
./scripts/twq.sh 'towerwatch_pkt_loss_google{host="standstill-phone"}' 7200 120
```

**Two capture windows:**
1. **Controlled (same-cell):** isolates mechanism. Devices co-located, on the same cell (Phase-1 gate).
2. **Uncontrolled (natural):** both devices left to do their own thing (phone free to reselect). This
   matches the daily-use observation and exposes the phone's *selection freedom*.

**Human steps — throughput, staggered:** run manual speedtests **alternating** (M6, wait, phone,
wait — rapid ABAB) so the two never saturate the tower at the same instant (they'd steal each other's
capacity). **Shrink the Pi run so you can alternate on a metered link:**
```
ssh admin@<pi-tailscale-ip> -- towerwatch-speedtest --triggered-by <you> --max-bytes 50M
```
Budget the alternation count from known sizes (~35 MB/phone run, ~50 MB/Pi run at the cap above);
reserve 1–2 full-size Pi runs (no `--max-bytes`) as accuracy anchors. **Watch the M6's Nighthawk data
counter** as your live budget gauge — speedtest byte usage is not visible in the metrics.

**Read it:** open `dashboard-compare.json` (the "Phone vs M6 — fixed overlay" row) — the OUTCOME
panels (reachability, p95/jitter, throughput p10) beside the INPUT panels (NR/flap, LTE, band, CA).
Headline = each device's own quiet→peak delta; cross-device throughput is directional only.

---

## Phase 3 — Hypothesis ladder (peak evening / recovery day)

Ranked by likelihood. **Outcomes carry the verdict; RF inputs are directional gates.** For each:
what confirms it, the query, and what to capture if data is missing.

**1. Priority / QCI tier.** *Claim:* the phone is served first under congestion.
*Discriminator:* phone holds throughput/p10 while M6 collapses at peak — **but only a confirmed
read if the congestion gate passed AND they're same-cell** (else per-cell load confounds it →
downgrade to *suggestive*). Phase −1 may pre-settle direction. QCI not loggable → behavioral only.

**2. Aggregate bandwidth / CA (the "how many channels" question).** *Claim:* the phone bonds more
carriers / MHz. `[M6 tested ✓; phone gated on CA capture]`
```
./scripts/twq.sh 'towerwatch_m6_carrier_count{host="standstill"}'         # baseline: 2
./scripts/twq.sh 'towerwatch_m6_agg_dl_bandwidth_mhz{host="standstill"}'  # baseline: 30
./scripts/twq.sh 'towerwatch_phone_ca_count{host="standstill-phone"}'     # populates once CA capture works
```

**3. NR access net of flapping.** *Claim:* the phone reaches NR capacity the M6 forswore.
`[tested ✓]`
```
./scripts/twq.sh 'avg_over_time(towerwatch_phone_nr_connected{host="standstill-phone"}[6h])'
./scripts/twq.sh 'rate(towerwatch_phone_nr_flap_events_total{host="standstill-phone"}[1h])*3600' 21600 300
```

**4. Selection freedom / cell-lock.** *Claim:* the 4G-locked (possibly antenna-pinned) M6
*structurally can't* reselect a quieter cell the way the phone does. *Discriminator:* the
**uncontrolled** window — phone on a different/quieter PCI than the pinned M6 under load, with better
outcomes.

**5. MIMO / antenna.** *Claim:* matched RF/band/CA but the phone still ~2× throughput. **Inferred
only, and the RF-match is directional** (H1/RT3) — never present as measured.

**6. Flap cost.** *Claim (to test, not assume):* NR flapping costs latency even with the LTE anchor
holding. `[tested ✓ — cadence confirmed 60s]` Correlate flap deltas against jitter/p95:
```
./scripts/twq.sh 'rate(towerwatch_phone_nr_flap_events_total{host="standstill-phone"}[10m])*3600' 21600 300
./scripts/twq.sh 'towerwatch_jitter_google{host="standstill-phone"}' 21600 300
```

**7. Thermal / uplink effort.** `towerwatch_m6_thermal_state`, `towerwatch_m6_tx_level` (lower tx =
better uplink path).

---

## Phase 4 — Verdict & teardown (departure day)

- Write the one-paragraph finding **keyed to the outcome variable** ("under congestion, the phone's
  loss/p95 held while the M6's degraded" — or whatever the data shows).
- Stop the phone RF session.
- If an `EXPERIMENT_LABEL` was set for the trip, reset it to `None` and note it.
- Escalation / fix call: SIM-plan/tier change · tether the phone · external antenna ·
  retry-NR-on-M6-with-flap-mitigation · Verizon escalation. Most are two-way doors.

---

## Appendix — field reality (verified against live data)

- **M6 is LTE-only by choice** (4G-locked; `m6_nr5g_attached`=0, `m6_endc_enabled`=1). Its NR *signal*
  fields (`m6_nr5g_rsrp/sinr`) are empty — expected, not a bug.
- **Phone reports NR ~70%** (`phone_nr_connected`) but NR *signal* (`phone_nr_rsrp/sinr/csi`) reads
  empty — likely the public-API NSA limit (`CellSignalStrengthNr` absent). Confirm via smoke test;
  if empty, treat as a known limit, not missing data.
- **CA count** (`phone_ca_count`) may be empty pending on-device capture confirmation.
- **Throughput metric names differ:** Pi scheduled = `http_throughput_mbps`; manual + phone =
  `speedtest_*`. Overlay panels query both.
- **Correct M6 CA field names:** `m6_carrier_count`, `m6_ca_scc_count`, `m6_agg_dl_bandwidth_mhz`.

**Pointers:** `towerwatch-rflogger/docs/FIELD_CATALOG.md` (phone field truth), `docs/phone-rf-logger.md`
(push contract), `docs/runbook.md` (ops), `docs/manual-speedtest.md` + `probes/cloudflare.py` docstring
(speedtest design: scheduled-vs-manual, adaptive ramp, warm-up discard, byte cap).
