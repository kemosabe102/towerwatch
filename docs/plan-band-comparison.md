# Plan — M6 band/cell comparison + Packet Loss panel fix

Status: **proposal, awaiting go/no-go.** Date: 2026-06-06.

## TL;DR

- **Active band-locking is not feasible on this hardware.** Netgear removed band
  selection from M6 firmware; there's no write API; the Verizon-locked unit
  blocks the telnet/AT-command fallback. The "connect to each band and rank"
  idea is a dead end on the M6. (Full evidence: `docs/m6-band-research.md`.)
- **Passive observation works and gets ~80% of the value.** The M6 already
  auto-roams across bands/cells (the live capture shows it aggregating B66 + B2
  simultaneously). We already log which band/PCI/eNB we're on every 60 s next to
  RSRP/SINR. So instead of *forcing* bands, we **observe** which the device
  naturally uses and **rank them by the signal/throughput we actually got.**
- **One source change unlocks the ranking:** band/PCI are currently metric
  *values*, not labels, so `avg SINR by band` isn't queryable. Emitting band as a
  Prometheus *label* on a small signal metric fixes that.

Three independently-shippable pieces, in priority order.

---

## Piece 1 — Fix the Packet Loss HUD stat (small, ship first)

**Problem.** The top-row "Packet Loss (1h max)" stat showed 4 cramped values.
The fix already shipped (`label_replace` per target, vertical orientation), but
the query is still fragile: `max_over_time(metric[1h:1m])` returns one series
*per label combination*, and the metrics carry `carrier`/`connection_type`/
`__proxy_source__` labels. Today that's 1 series each (clean), but any label
drift re-introduces duplicates.

**Fix.** Wrap each target in an explicit aggregation so it can only ever return
one value:

```promql
max(max_over_time(towerwatch_pkt_loss_google{host="$location"}[1h:1m]))
```

...with `label_replace(..., "target", "Google", ...)` on the outside for the row
name. Robust regardless of underlying label cardinality.

**Effort:** dashboard-only, ~10 min. No deploy.

---

## Piece 2 — M6 cell panel redesign (dashboard-only, ship second)

Replace the single misleading state-timeline (id 51, magnitude gradient on
categorical IDs) with a small cluster that answers all three questions:

1. **Current cell at a glance** — a stat row, plain values, no good/bad colors:
   `PCI | band | eNB ID | RSRP | SINR` (last value).
2. **Handover / flapping history** — keep a state-timeline, but switch
   `color.mode` from `continuous-GrYlRd` (magnitude) to **`palette-classic`**
   (distinct color per distinct value). Constant flips become visually obvious;
   a stable cell is one long band. Rows: PCI, band, eNB ID.
3. **Signal-vs-band correlation** — a timeseries of SINR + RSRP with the **band**
   value drawn as a step line on a second axis (or band changes as annotations).
   Lets you eyeball "signal was better while on band 66 than band 2" even before
   Piece 3 makes it numeric.

**Effort:** dashboard-only, ~45 min. No deploy. Uses metrics already flowing.

---

## Piece 3 — Make "avg signal per band" queryable (source change, optional)

**Why it's needed.** Band/PCI are Influx *fields* (→ Prom values), so you cannot
`avg by (band)`. To rank bands numerically we must emit **band as a tag** (→ Prom
label) on a signal line.

**The change** (mirrors the existing `build_info`/`speedtest` tag pattern in
`tick.py`, which is the sanctioned exception to the "labels baked into names"
invariant in CLAUDE.md):

- Add a new Influx line per tick, e.g.
  `towerwatch,host=…,carrier=…,connection_type=…,band=66,pci=81 m6_sig_rsrp=-97,m6_sig_sinr=18 <ts>`
  — i.e. signal fields tagged with the **current band + pci**. New metric names
  (`m6_sig_rsrp`/`m6_sig_sinr`) so the existing untagged `m6_rsrp`/`m6_sinr`
  history is untouched.
- Source: a new `format_band_sig_line()` in `tick.py`, fed from the m6 fields the
  probe already extracts (`m6_pcc_band`, `m6_pcc_pci`, `m6_rsrp`, `m6_sinr`). No
  probe change needed — the data's already there; it's purely a serialization
  shape change.
- Guard cardinality: band has ~3–5 distinct values, pci maybe a few dozen — fine
  for Prom. Do **not** also tag eNB/cellId (too high-cardinality).

**Then the ranking panel becomes a real table:**

```promql
avg by (band) (avg_over_time(towerwatch_m6_sig_sinr[$__range]))
avg by (band) (avg_over_time(towerwatch_m6_sig_rsrp[$__range]))
count by (band) (count_over_time(towerwatch_m6_sig_sinr[$__range]))   # dwell time
```

A Grafana **table** panel: one row per band, columns = avg SINR, avg RSRP,
samples (dwell), sorted by SINR. That's the "which band is best" answer, built
from passive data over days.

**Effort:** source change + test + CI + deploy to both Pis (~2 h). Standard
`ci.sh full` → push → `deploy.sh` to home + standstill. Data-budget impact: ~0 —
it's one extra line per existing tick (~bytes), well under the 230 MB/mo base.

**Invariant note:** this adds `band`/`pci` as label dimensions. Must update
CLAUDE.md's metric-naming invariant to record the exception (like build_info).

---

## What we explicitly are NOT doing

- **Active band locking.** Not possible on a Verizon-locked M6 (no API, firmware
  removed it, telnet/AT locked). Documented in `docs/m6-band-research.md` so we
  don't re-investigate.
- **Per-speedtest band sweep.** Was the original idea; depends on band locking.
  Dead.

## Suggested sequencing

Ship **1 + 2 together** (dashboard-only, immediate, no risk), live with it for a
few days to confirm the passive data is rich enough, then decide on **3**.

## Open questions for you

1. Ship Pieces 1+2 now and defer 3? Or do all three in one go?
2. For Piece 2's correlation view: band as a step-line overlay on the SINR chart,
   or band changes as vertical annotations? (Overlay is denser; annotations are
   cleaner.)
3. Piece 3: OK to add `band`/`pci` as label dimensions (the invariant exception)?
