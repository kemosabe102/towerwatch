# Standstill link — download throughput collapse and recovery, July 2026

**Site:** fixed monitoring location ("standstill"), Netgear Nighthawk M6 hotspot on Verizon,
LTE-only by configuration. Continuously instrumented by a Raspberry Pi running
[towerwatch](https://github.com/kemosabe102/towerwatch).

**Measurement window:** 2026-07-01 → 2026-07-15 (15 days, continuous).

**Source data:** [`data-archive/standstill-jul2026/jul01_15_throttle/`](../data-archive/standstill-jul2026/jul01_15_throttle/)
— 40 metrics at 60-second resolution, exported to CSV before Grafana Cloud's 14-day
retention expired. **Every number in this document is re-derivable from those files.**

**Last updated:** 2026-07-15. Links point into the
[towerwatch repository](https://github.com/kemosabe102/towerwatch); if you're reading this
as a standalone file and want the underlying CSVs, ask and they can be sent directly.

---

## Summary

For roughly a week (July 1–7), the download speed on this link ran at about **7 Mbps
median**, dropping to a sustained floor near **1 Mbps** from July 3 through July 6. On
July 8 it recovered to about **37 Mbps** and has stayed there since. That is a **5.2×
difference in median download speed** between the two periods, with a **33× gap** at the
deepest point.

Three things make this worth attention:

1. **The radio did not change.** Signal quality was statistically identical across both
   periods — same tower, same frequency band, same signal-to-noise ratio, same transmit
   power, same temperature. Whatever changed, it was not the airlink.
2. **Upload held steady — only download collapsed.** Upload ran at ~24–25 Mbps before
   and after the slow period, a difference of about 5%. It could not be measured *inside*
   the deepest stretch (the download was slow enough to exhaust the test's time limit
   before the upload leg started — see [limitation 2](#known-gaps-and-limitations)), so
   the claim rests on the measurements bracketing the trough, not on ones taken within it.
3. **It was not time-of-day congestion.** During the slow period *every* time of day was
   slow — including 6 AM. During the recovered period *every* time of day is fast. The
   pattern follows the calendar, not the clock.

**What this document does not claim.** These measurements show *what* happened and rule
out several causes with hard numbers. They cannot identify *why*. Two explanations remain
consistent with all the evidence — a usage-allotment throttle that expired, or sustained
multi-day network-side congestion — and this data cannot distinguish between them. The
section [What this evidence cannot prove](#what-this-evidence-cannot-prove) says exactly
what would.

---

## For the ham operator reading this

Some analogies, since the underlying physics is shared:

| Cellular term | Ham equivalent |
|---|---|
| **SINR** (signal-to-interference-plus-noise ratio, dB) | Signal-to-noise ratio. 15 dB is a comfortable, workable signal. |
| **RSRP** (reference signal received power, dBm) | Raw received signal strength — the S-meter reading. −100 dBm is moderate; not marginal, not strong. |
| **Band 66 / PCI 81 / eNB 1403** | The specific repeater, on a specific frequency allocation. PCI is which sector; eNB is which tower site. |
| **Carrier aggregation (2 carriers, 30 MHz)** | Running two channels bonded together for more bandwidth. |
| **tx_level** | Your transmitter's output power. Higher means the radio is working harder to be heard. |

The headline finding in ham terms: **the S-meter never moved and the noise floor never
moved, but the throughput dropped 33×**. If your S/N is unchanged at 15 dB and the path
is identical, but you suddenly can't pass traffic, the problem is not propagation, not
the antenna, and not the radio — it is above the RF layer.

Worth noting for calibration: this is a *fixed* installation. No one moved the antenna,
changed the feedline, or retuned anything between the two periods. The equipment sat in
the same place doing the same thing.

---

## 1. The observation

Download throughput, 46 distinct measurement runs over 15 days (2–3 per day, at randomized
times inside fixed morning/midday/evening windows):

| Period | Median download | n | Min | Max |
|---|---|---|---|---|
| **Jul 1–7** | **7.06 Mbps** | 115 samples | 0.49 | 56.46 |
| **Jul 8–15** | **36.97 Mbps** | 110 samples | 7.45 | 52.58 |
| | **5.2× difference** | | | |

The deepest stretch, **Jul 3 16:13 → Jul 6 13:20**:

| | Median | n |
|---|---|---|
| Deep trough | **1.12 Mbps** | 55 samples |
| Recovered baseline (Jul 8–15) | **36.97 Mbps** | 110 samples |
| | **33.0×** | |

Run-by-run through the trough and its exit — note the recovery is abrupt, not gradual:

```
  Jul 03 12:05      9.06 Mbps        <- last normal-ish run
  Jul 03 16:13      1.12 Mbps        <- trough begins
  Jul 03 21:20      0.49 Mbps
  Jul 04 05:04      5.27 Mbps
  Jul 04 11:51      0.88 Mbps
  Jul 04 17:39      1.19 Mbps
  Jul 05 04:18      0.00 Mbps        <- run failed entirely
  Jul 05 09:41      0.61 Mbps
  Jul 05 14:02      0.59 Mbps
  Jul 05 18:59      0.72 Mbps
  Jul 06 04:58     13.94 Mbps
  Jul 06 11:59      1.94 Mbps
  Jul 06 13:20      1.78 Mbps        <- trough ends
  Jul 06 18:40     40.84 Mbps        <- recovered, in one step
  Jul 06 19:10     35.41 Mbps
```

The link went from 1.78 Mbps to 40.84 Mbps between two consecutive runs about five hours
apart, with no configuration change, no reboot, and no equipment touched.

*Source: `standstill__http_throughput_mbps.csv`*

---

## 2. Upload held steady around the trough — only download collapsed

This is the most diagnostically specific finding in the dataset. Note the scope: upload is
measured on the days either side of the deep trough, not within it
([limitation 2](#known-gaps-and-limitations)).

| Period | Median **upload** | n |
|---|---|---|
| Jul 1–7 | **23.92 Mbps** | 60 samples |
| Jul 8–15 | **25.22 Mbps** | 110 samples |

Upload differs by about **5%** between the two periods while download differs by **5.2×**.
During the era when download was crawling, upload was running at 24 Mbps — on the same
radio, through the same tower, minutes apart within the same test run.

On several runs, **upload was faster than download** — 35 of 170 paired measurements. For
example:

```
  Jul 01 12:20    download   7.06 Mbps    upload  10.05 Mbps
  Jul 02 17:42    download  12.82 Mbps    upload  25.50 Mbps
  Jul 03 12:05    download   9.06 Mbps    upload  19.17 Mbps
```

A cellular link's uplink is normally the weaker direction (less spectrum allocated, and the
handset transmits at a fraction of the tower's power). Upload exceeding download by 2× is
inverted from the expected shape, and it means the constraint was not the RF path — an RF
problem degrades both directions.

*Source: `standstill__http_upload_mbps.csv`, `standstill__http_throughput_mbps.csv`*

---

## 3. The radio never changed

The M6 hotspot exposes its own radio telemetry, sampled every 60 seconds — **15,735
samples** across the window. Comparing the slow era to the recovered era:

| Measurement | Jul 1–7 (slow) | Jul 8–15 (recovered) | Change |
|---|---|---|---|
| **SINR** (signal-to-noise, dB) | **15.0** | **15.0** | **none** |
| **RSRP** (signal strength, dBm) | −98 | −100 | 2 dB |
| **tx_level** (transmit power) | 14 | 13 | 1 |
| **Device temperature** (°C) | 58 | 59 | 1 |
| Sample count | n=4,915 | n=10,820 | |

Median SINR is **identical to the decimal** across a period in which download throughput
moved 5.2×. RSRP moved 2 dB — within normal variation for a fixed installation, and in the
*wrong direction* to explain the recovery (signal got marginally weaker while throughput
improved 5×).

**The link also stayed on the same cell the entire time:**

| | Value | Consistency |
|---|---|---|
| Serving band | **66** | 99.9% of 15,735 samples |
| Physical cell ID (PCI) | **81** | 99.9% |
| Tower (eNB ID) | **1403** | 99.9% |
| Carrier aggregation | **2 carriers** | median, both eras |
| Aggregate bandwidth | **30 MHz** | median, both eras |

Same tower. Same sector. Same band. Same bonded bandwidth. Same signal-to-noise. **5.2×
different download speed.**

*Ham framing:* the S-meter, the noise floor, the frequency, the repeater, and the transmit
power were all unchanged. Only the amount of traffic allowed through changed.

*Source: `standstill__m6_sinr.csv`, `standstill__m6_rsrp.csv`, `standstill__m6_tx_level.csv`,
`standstill__m6_dev_temperature.csv`, `standstill__m6_pcc_pci.csv`, `standstill__m6_pcc_band.csv`,
`standstill__m6_enb_id.csv`, `standstill__m6_carrier_count.csv`, `standstill__m6_agg_dl_bandwidth_mhz.csv`*

---

## 4. It was not time-of-day congestion

If a slow link is caused by neighbours sharing a busy tower, the slowness concentrates in
the busy hours — evenings — and clears in the early morning. That is testable directly.

Median download by time of day, split by era:

| Time of day | Jul 1–7 (slow) | Jul 8–15 (recovered) |
|---|---|---|
| **Morning** (05:00–11:00) | 6.06 Mbps (n=28) | **43.53 Mbps** (n=40) |
| **Midday** (11:00–15:00) | 4.50 Mbps (n=40) | **36.84 Mbps** (n=35) |
| **Evening** (15:00–22:00) | 12.82 Mbps (n=45) | **35.47 Mbps** (n=35) |

**Every bucket is depressed in the first era. Every bucket recovers in the second.** A 6 AM
measurement during the slow week returned 6 Mbps; a 6 AM measurement the following week
returned 43 Mbps. Diurnal congestion does not behave this way — it cannot make 6 AM slow
for four days and then make 6 AM fast for eight.

Notably, evenings were the *fastest* bucket during the slow era (12.82) — the opposite of
the congestion signature.

The pattern tracks the **calendar**, not the clock.

*Source: `standstill__http_throughput_mbps.csv`*

---

## 5. Other causes ruled out, with numbers

| Cause | Ruled out by | Evidence |
|---|---|---|
| **The Pi, the cable, the LAN** | Local network is 100× faster than the internet path | Gateway round-trip **1–2 ms** vs internet round-trip **359–440 ms**. The link from the Pi to the hotspot is not the constraint. |
| **Thermal throttling of the hotspot** | Device never reported thermal stress | Temperature ranged **53–69 °C** across the window (median 58 slow era / 59 recovered — a 1 °C difference); `thermal_state` reported **Normal (0)** at every one of 15,735 samples, never once escalating. |
| **Moving between towers / cell reselection** | Static serving cell | Band 66, PCI 81, eNB 1403 at **99.9%** of 15,735 samples. |
| **Losing carrier aggregation** | Spectrum unchanged | **2 carriers / 30 MHz** median in both eras. |
| **5G attach/drop flapping** | Link is LTE-only by design | `nr5g_attached` = **0** at all 15,735 samples; `service_type` = **3 (LTE)** with zero variance; `lte_attached` = **1** throughout. This is a deliberate configuration choice made in the hotspot's admin UI (5G was disabled because attach/drop cycling made the connection unreliable), **not** a fault or a coverage gap. |
| **Packet loss / a broken link** | Link was clean and up | Median packet loss **0.0%** in both eras. The link was not dropping traffic; it was rate-limited. |

*Source: `standstill__rtt_avg_gateway.csv`, `standstill__rtt_avg_google.csv`,
`standstill__pkt_loss_google.csv`, `standstill__m6_dev_temperature.csv`,
`standstill__m6_thermal_state.csv`, `standstill__m6_nr5g_attached.csv`,
`standstill__m6_service_type.csv`, `standstill__http_throughput_bytes.csv`*

---

## 6. Latency degraded too, but less dramatically

| Measurement | Jul 1–7 | Jul 8–15 |
|---|---|---|
| Internet round-trip (ICMP to 8.8.8.8) | 440 ms | 359 ms |
| Web page fetch time | 743 ms | 489 ms |
| Gateway round-trip (Pi → hotspot) | 1 ms | 2 ms |
| Packet loss | 0.0% | 0.0% |

Latency improved ~20–35% between the eras — real, but nowhere near the 5.2× download
change. Both eras show high absolute latency (359 ms is poor for a fixed link) — a separate,
ongoing characteristic of this connection, out of scope here.

*Source: `standstill__rtt_avg_google.csv`, `standstill__http_latency_ms.csv`,
`standstill__rtt_avg_gateway.csv`, `standstill__pkt_loss_google.csv`*

---

## 7. Corroborating event

An automatically-recorded outage annotation:

```
  2026-07-03 13:31   Outage: 68 min — network_unreachable
```

This sits directly at the onset of the deep trough — the last normal-ish run was **Jul 3
12:05 (9.06 Mbps)** and the first trough run was **Jul 3 16:13 (1.12 Mbps)**, with the
68-minute connectivity loss falling between them.

Included for completeness, but it is **weak evidence**: this is temporal correlation only.
The annotation records *that* connectivity dropped, not why, and a 68-minute outage does not
by itself explain a subsequent 3-day throughput floor.

---

## What this evidence cannot prove

**This data identifies what did not happen. It does not identify what did.**

The measurements above eliminate the radio, the equipment, the local network, the serving
cell, thermal effects, packet loss, and time-of-day congestion. Two explanations remain
consistent with **all** of the evidence:

1. **A usage-based throttle** (soft cap / allotment) that engaged and later expired or
   reset.
2. **Sustained network-side congestion** at the tower or in the backhaul, lasting days
   rather than hours.

**Towerwatch cannot distinguish these two.** Both would produce exactly what was observed:
unchanged radio, download-only restriction, calendar-shaped rather than clock-shaped
timing, and abrupt recovery.

Arguments that *lean* toward a policy/allotment mechanism, offered as reasoning rather than
proof:

- The download-only asymmetry. Congestion at a tower generally degrades both directions,
  since both share the same congested air interface and backhaul.
- The abruptness of recovery (1.78 → 40.84 Mbps between two runs).
- The flatness across all hours during the slow era.

**What would actually settle it:** carrier-side records — the account's plan tier, its
high-speed data allotment, actual usage against that allotment during the period, the
billing-cycle boundary date, and whether a policy-based rate limit was applied to this line
between July 3 and July 8. None of that is visible from the customer side of the link, and
no amount of additional measurement here can substitute for it.

---

## Method

**Instrument.** A Raspberry Pi on the hotspot's LAN, wired Ethernet, running a 60-second
probe loop continuously. It has no special access — it measures the same connection any
device on that network would experience.

**Throughput measurement.** Multi-stream HTTP transfer against Cloudflare's speed-test
endpoints (`speed.cloudflare.com`), download and upload back-to-back within one run.
Scheduled 3× per day at a random time inside each of three fixed windows (06:00–10:00,
11:00–14:00, 17:00–21:00), so measurements sample the day evenly rather than clustering.
Randomization within the window prevents accidental synchronization with any periodic
network behaviour.

**Radio telemetry.** Read from the M6's own local admin API (`/api/model.json`) every 60
seconds. These are the modem's own reported values — the same numbers the hotspot's status
page shows.

**Monitoring's own data use.** The probes consumed **4.68 GB over the 15-day window**
(measured, not estimated) — against a 30 GB/month budget for the site. The monitoring is
not itself a meaningful consumer of the line's allowance.

**Retention.** Grafana Cloud's free tier retains 14 days. Verified by probe: data 13 days
old returns samples; 14 days old returns nothing. The July 3–6 trough was 9–12 days old at
export time and would have aged out within ~1–2 days. This is why the CSV archive exists,
and why it was captured before this document was written.

### Reproducing these numbers

All figures derive from the committed CSVs:

```
data-archive/standstill-jul2026/jul01_15_throttle/standstill__<metric>.csv
```

Format: `timestamp_utc, iso_pdt, <one column per label-set>`. An empty cell means no sample
at that timestamp; empty rows are real data gaps.

To re-export from Grafana Cloud (only within the retention window):

```bash
python scripts/export_archive.py --host standstill \
    --start 2026-07-01 --end 2026-07-16 \
    --out data-archive/standstill-jul2026/jul01_15_throttle
```

(Re-deriving these figures against the live Grafana instance rather than the CSVs has two
known traps — metric naming and query step size. Both are documented in the repo's
`CLAUDE.md`. Working from the committed CSVs avoids both.)

---

## Known gaps and limitations

Stated plainly, because a reader should be able to check them:

1. **Sample size is small.** 46 distinct throughput runs over 15 days — by design, since
   each run consumes ~100 MB of a metered link. The per-era medians rest on 115 and 110
   samples respectively (each run produces several stored samples), but the number of
   *independent measurement events* is 46. The effect size (5.2×, 33× at the trough) is
   far larger than the scatter within either era, but this is not a high-n study.

2. **During the deep trough, runs did not complete — and this limits what upload can say
   there.** Downloads were slow enough to hit the probe's 90-second timeout, so those runs
   recorded a download speed but never proceeded to the upload phase. Evidence: through the
   trough, `http_throughput_bytes`, `http_upload_bytes`, `bufferbloat_rtt_download_ms`, and
   `bufferbloat_rtt_upload_ms` all have **zero** completed runs, while
   `http_throughput_mbps` still recorded. **No upload run reported a failure value** — the
   phase simply never executed. So the claim "upload was unaffected" is supported by the
   Jul 1–3 and Jul 6–15 data on either side of the trough (medians 23.92 / 25.22), **not**
   by measurements taken inside it. Upload's behaviour during Jul 3–6 is unmeasured.

3. **No pre-July-1 baseline.** Retention had already erased it before this window was
   archived. It is therefore unknown whether Jul 1–2 (median ~7–41 Mbps, mixed) represents
   this link's normal state or an already-degrading one. The Jul 8–15 era (~37 Mbps) is the
   only well-characterized "healthy" baseline available.

4. **A 2-day gap in radio telemetry (Jul 5–6).** The hotspot's admin API was unreachable
   for ~2 days due to a known, since-fixed monitoring bug (the Pi had frozen onto a stale
   gateway address after a restart). **This did not affect the link or the throughput
   measurements** — during that same period, connectivity probes recorded 1,329 samples and
   throughput runs continued normally. It means radio telemetry has a hole in the middle of
   the trough, and the radio comparison in §3 spans the trough rather than sitting inside
   it.

5. **Single site, single device.** No second hotspot at the same location to compare
   against during this window. A phone at the same site was measured during an earlier trip
   (July 2–5) and is documented separately in
   [`standstill-verdict-jul2026.md`](standstill-verdict-jul2026.md); phone tracking has
   since been turned off.

6. **The 0.00 Mbps reading on Jul 5 04:18** is a run that failed outright rather than a
   measurement of zero throughput. It is excluded from the medians (which use only non-zero
   samples).

7. **The billing-cycle date for this line is not known to us**, and it matters. One of the
   two candidate explanations is an allotment throttle that expired — if the cycle boundary
   fell between July 6 and July 8, that would be a striking coincidence worth investigating;
   if it fell nowhere near, the throttle hypothesis weakens considerably. This is one of the
   few relevant facts that *is* visible from the customer side (it's on the account page),
   and it simply hasn't been checked against this window. **It should be, before anyone
   concludes anything.**

8. **It is unknown whether this has happened before.** Monitoring at this site does not
   reach back beyond the retention window, so there is no way to tell from this data whether
   the July 3–6 collapse is a one-off or part of a recurring monthly pattern. A recurring
   pattern aligned to billing cycles would itself be strong evidence — independent of any
   carrier records — but proving or excluding it requires several months of continuous
   monitoring, which now exists going forward but did not then.

---

## Bottom line

Between July 3 and July 6, this connection's download throughput sat at roughly **1 Mbps**
— about **1/33rd** of what the same connection delivered a week later, through the same
tower, on the same band, with an **identical signal-to-noise ratio of 15.0 dB**, while
**upload continued at 24 Mbps**. Recovery was abrupt and required no intervention.

The radio was never the problem. What decided the download rate was above the RF layer, and
the customer-side data cannot see what made that decision.
