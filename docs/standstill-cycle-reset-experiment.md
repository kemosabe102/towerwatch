# Cycle-reset experiment — clean deprioritization measurement (mid-July 2026)

The pre-crossing home baseline is expired (usage was past 100 GB by the Jun 22 retention
floor). But the **cleanest** deprioritization experiment is still ahead: the Xfinity
billing cycle resets mid-July, flipping the phone line **back to premium priority** at the
same home cells, same phone, same rflogger app. That's a controlled same-location
before/after — better than the crossing ever was.

## The natural experiment

| | Before reset | After reset |
|---|---|---|
| Priority state | Deprioritized (>100 GB, congestion-time throttle active) | Premium (usage counter reset to 0) |
| Location | Home (cells 282148 / 282144) | Home (same) |
| Phone / app | Pixel 9 Pro / rflogger | same |
| Confounds held | cell, band, device, probe method, season | same |

The only thing that changes at the reset is priority tier. A peak-hour throughput jump
after the reset, with quiet-hour throughput unchanged, **is** the deprioritization penalty
— measured directly, not inferred.

## What to capture (action required)

1. **Keep rflogger running at home continuously** through the reset date, from at least
   **5 days before** to **5 days after**. Confirmed running as of 2026-07-06 15:34.
   Do not move the phone; the diff-in-diff needs the same home cells on both sides.
2. **Anchor the windows to the actual reset date.** Fill in below from the Xfinity app
   (Account → billing cycle):
   - Cycle reset date: `__________` (the deprioritization lifts here)
   - Current-cycle usage as of today: `__________` GB (confirms still >100 GB pre-reset)
3. Note any phone reboots / airplane-mode toggles in that window (they reset the NR-flap
   counter and can re-camp cells).

## Analysis (run after the after-window fills)

Diff-in-diff on `speedtest_download_mbps`, home-cell-only, controlled:

```
For each side (BEFORE = reset−5d..reset, AFTER = reset..reset+5d):
  filter: phone_cell_id in {home cells}, phone_rsrp >= -110
  segment: hour-bucket ∈ {quiet 02–06, peak 17–22}
  segment: phone_nr_connected ∈ {0,1}   # NR steering swamps the effect otherwise
  stat: mean, p50, p10, n  per (side, bucket, nr)

penalty_lifted = (AFTER.peak − BEFORE.peak)   # expect POSITIVE (premium faster at peak)
control        = (AFTER.quiet − BEFORE.quiet) # expect ~0 (quiet unaffected by priority)
DiD = penalty_lifted − control                # the clean deprioritization penalty
```

Fingerprint of a real effect: **DiD > 0 and control ≈ 0.** If quiet-hour throughput also
jumps, something other than priority changed (RF, tower upgrade) — investigate before
attributing to deprio.

Reuse the loader/controls from the retro analysis (see
`data-archive/standstill-jul2026/phone_full_history/` and the analysis in
`docs/standstill-verdict-jul2026.md` §6). Prefer the `triggered_by=rf-logger` series
(continuous), not `phone_compare` (sparse ADB tool).

## Known limitation (state it in the writeup)

If the home towers are **never meaningfully congested**, the peak/quiet gap is small on
both sides and DiD ≈ 0 — **not** because deprioritization doesn't matter, but because QCI 9
only bites when the tower must choose. A null here does **not** exonerate priority at
standstill on Jul 4 (where congestion ≫ home). It only means the home data can't measure
it. The pre-trip home data already showed a ~45–63% peak/quiet gap, so home congestion is
non-trivial — the experiment should have signal, but report sample counts and don't force a
conclusion from thin peak-hour buckets.

## Why this beats the expired crossing

The crossing diff-in-diff needed un-deprioritized *early-June* data (gone) and had to model
the exact 100 GB crossing date from usage counters (the app only sees its own ~0.05 GB of
probe traffic — can't reconstruct it). The reset experiment needs neither: the reset date
is a known calendar event, and both sides are freshly captured at 60s resolution.
