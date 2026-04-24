# Build-info telemetry — design

**Date:** 2026-04-23
**Status:** approved, ready for implementation plan

## Problem

The currently-deployed `BUILD_VERSION` reaches Grafana Cloud only via two
transient log events: `service_restarted` (on boot) and `outage_recorded` (on
push-gap detection). Neither is re-emitted on a cadence, so a Grafana panel
that queries for them is **blank between deployments**.

Compounding this, Grafana Cloud retention is **14 days**. A user inspecting a
narrow time window ("last 1 hour") a month after the most recent deploy must
still be able to see which version is running now.

## Goal

Make the currently-running `BUILD_VERSION` and `BUILD_DATE` visible in Grafana
at any lookback window ≥ 60 s, indefinitely after deploy, via a conventional
Prometheus pattern. Additionally, enrich the hourly `service_heartbeat` log
event so that LogQL queries over heartbeats can correlate events with the
version that produced them.

## Non-goals

- Historical "what version was running in February?" queries. Grafana Cloud's
  14-day retention is authoritative; long-term deploy history belongs in the
  repo or external storage and is out of scope here.
- Changes to `grafana/dashboard.json`. The user imports the dashboard manually
  after deploys; this spec describes the query to add, not the JSON edit.
- Refactoring existing per-target metrics (e.g. `rtt_avg_google`) into labeled
  metrics. `CLAUDE.md` explicitly forbids that; this spec introduces a *new*
  labeled metric, which is compatible with that invariant.

## Design

### Part 1 — Prometheus `towerwatch_build_info` gauge (primary)

On every tick (60 s), the push batch gains one additional Influx line
alongside the existing measurement line:

```
towerwatch,host=<INFLUX_HOST_TAG>,version=<short-hash>,build_date=<iso-date> build_info=1 <timestamp>
```

Grafana Cloud's Influx-line ingest converts this into a Prometheus metric:

```
towerwatch_build_info{host="<tag>",version="68a602a",build_date="2026-04-23T16:30:25-07:00"} 1
```

A stat panel with query `last(towerwatch_build_info)` and display value
`{{version}}` will render the current version at any lookback window that
contains at least one tick (≥ 60 s). Because it is re-emitted every tick,
retention cannot erase it — the panel is always populated.

**Label choice rationale.** The `CLAUDE.md` invariant against labels applies
to refactoring existing per-target fields into label selectors. `build_info`
is the canonical Prometheus pattern (mirrors `node_exporter`'s `node_os_info`
and Prometheus's own `prometheus_build_info`) where the *point* of the metric
is that its labels carry structured metadata — there is no sensible "value"
dimension here. A purely-numeric field like `build_hash_int` would not be
queryable as a string in Grafana.

**Data budget.** One extra line of ~80 bytes per tick, 60 ticks/h × 24 h × 30
d ≈ 3.5 MB/month raw, ≤ 1.7 MB/month after gzip. Negligible against the ~230
MB/month baseline.

### Part 2 — `service_heartbeat` version fields (secondary)

`events.service_heartbeat` gains two new keyword-only fields:

```python
def service_heartbeat(loki, *, uptime_h: float, version: str, build_date: str) -> None:
    loki.push(
        "WARN",
        "Service heartbeat",
        {
            "event": config.LOG_EVENT_HEARTBEAT,
            "uptime_h": uptime_h,
            "version": version,
            "build_date": build_date,
        },
    )
```

The caller in `app.py` passes `config.BUILD_VERSION` and `config.BUILD_DATE`.
This enriches the existing hourly WARN heartbeat so log-stream panels can
show which version produced which entries. It is **not** the source for the
"current version" stat panel — Part 1 owns that.

## Architecture impact

- **`events.py`:** one changed function signature (`service_heartbeat`).
- **`tick.py` or `app.py`:** one added line per tick to append the
  `build_info` Influx line into the batch. Cleanest placement is in `app.py`
  right next to the existing `format_influx_line(fields, timestamp)` call, or
  as a small helper `format_build_info_line(timestamp)` in `tick.py` to keep
  the influx-string knowledge co-located with `format_influx_line`. Decision
  deferred to the implementation plan.
- **`config.py`:** no changes. `BUILD_VERSION`/`BUILD_DATE` already exist.
- **`grafana/dashboard.json`:** no code change in this spec. The user will add
  a stat panel manually with query `last(towerwatch_build_info)` and display
  `{{version}}`. (A follow-up PR can bake this into the dashboard JSON.)

## Testing

- **`tests/test_events.py`:** update/extend the `service_heartbeat` test to
  assert the new `version` and `build_date` fields appear in the payload.
- **`tests/test_tick.py` (or `test_app.py`):** add a test that after a tick,
  the metric batch contains a line matching
  `^towerwatch,.*version=<v>.*build_date=<d>.* build_info=1 \d+$`. The
  existing tick-level tests use hand-written fakes (no `MagicMock`), so the
  new assertion slots into the existing harness.
- **No dashboard testing.** The dashboard JSON is not changed in this spec.

## Rollout

1. Land the code change (one PR).
2. `./ci.sh full` stamps a new version.
3. `./scripts/deploy.sh` to the Pi.
4. In Grafana: add a stat panel to the dashboard with query
   `last(towerwatch_build_info)`, display mode "value", override display name
   to `{{version}}`. Optionally add a second panel showing `{{build_date}}`.
5. Verify: after ≥ 60 s of post-deploy runtime, the panel shows the new
   short-hash at any lookback window.

## Risks

- **Influx-line tag cardinality.** Every deploy mints a new `version` label
  value, which creates a new Prom series. At one deploy per day for a year
  that is 365 series — orders of magnitude below any Grafana Cloud cardinality
  ceiling. No risk in practice.
- **Backwards-incompatible event signature.** `service_heartbeat` gains
  required kwargs. The only caller is `app.py`; tests that construct the
  event directly must be updated. Detected at pyright time.

## Open questions

None. Proceed to implementation plan.
