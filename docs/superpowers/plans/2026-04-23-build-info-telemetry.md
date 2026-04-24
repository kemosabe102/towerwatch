# Build-info Telemetry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the running `BUILD_VERSION` visible in Grafana at any lookback window — a Prometheus `towerwatch_build_info` gauge emitted every tick (primary) and two new fields on the hourly `service_heartbeat` Loki event (secondary).

**Architecture:** Add a `format_build_info_line(timestamp)` helper in `tick.py` that produces an Influx line-protocol line with `version` and `build_date` as **tags** (tags become Prometheus labels in Grafana Cloud; tag values are unquoted strings and safe, unlike field string values which are not currently quoted). Call it from `app.py` immediately after the existing `format_influx_line(fields, timestamp)` append in the main loop. Separately, extend `events.service_heartbeat` with required `version` and `build_date` kwargs and thread them from the single caller in `app.py`.

**Tech Stack:** Python 3, pytest, ruff, pyright. Influx line protocol → Grafana Cloud Prometheus ingest (labeled gauge). Loki JSON payload (heartbeat).

**Spec:** `docs/superpowers/specs/2026-04-23-build-info-telemetry-design.md`

---

## File Structure

**Modified:**
- `src/towerwatch/events.py` — extend `service_heartbeat` signature
- `src/towerwatch/tick.py` — add `format_build_info_line` helper
- `src/towerwatch/app.py` — call the helper each tick; pass version/build_date to heartbeat
- `tests/test_events.py` — update heartbeat test
- `tests/test_influx_line_format.py` — add tests for `format_build_info_line`

No new files. No config changes. No dashboard JSON change (done manually in Grafana).

---

### Task 1: Add `format_build_info_line` helper (TDD)

**Files:**
- Test: `tests/test_influx_line_format.py`
- Modify: `src/towerwatch/tick.py` (add new function after `format_influx_line`)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_influx_line_format.py`:

```python
def test_build_info_line_shape():
    """Prom build_info gauge line: version and build_date are TAGS, build_info is the field."""
    from towerwatch.tick import format_build_info_line

    line = format_build_info_line(
        ts=1700000000, version="abc1234", build_date="2026-04-23T16:30:25-07:00"
    )
    # Starts with measurement + host tag
    assert line.startswith("towerwatch,host=towerwatch,")
    # version and build_date are tags (before the first space)
    tag_section = line.split(" ", 1)[0]
    assert "version=abc1234" in tag_section
    assert "build_date=2026-04-23T16:30:25-07:00" in tag_section
    # build_info=1 is the only field (after the first space, before the timestamp)
    field_section = line.split(" ")[1]
    assert field_section == "build_info=1"
    # Timestamp last
    assert line.endswith(" 1700000000")


def test_build_info_line_uses_config_defaults():
    """When version/build_date are omitted, falls back to config.BUILD_VERSION/BUILD_DATE."""
    from towerwatch import config
    from towerwatch.tick import format_build_info_line

    line = format_build_info_line(ts=1700000000)
    assert f"version={config.BUILD_VERSION}" in line
    assert f"build_date={config.BUILD_DATE}" in line
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_influx_line_format.py -v`
Expected: both new tests FAIL with `ImportError: cannot import name 'format_build_info_line'`.

- [ ] **Step 3: Implement the helper**

Add to `src/towerwatch/tick.py`, immediately after `format_influx_line` (around line 55):

```python
def format_build_info_line(
    ts: int,
    *,
    version: str | None = None,
    build_date: str | None = None,
) -> str:
    """Influx line for the `towerwatch_build_info` Prom gauge.

    `version` and `build_date` are emitted as Influx **tags** (not fields) so
    Grafana Cloud Prom ingest turns them into metric labels. Tag values are
    unquoted strings by spec; field string values are not (see the pinned
    characterization test in test_influx_line_format.py).
    """
    v = version if version is not None else _config.BUILD_VERSION
    d = build_date if build_date is not None else _config.BUILD_DATE
    return (
        f"{_config.INFLUX_MEASUREMENT},"
        f"host={_config.INFLUX_HOST_TAG},"
        f"version={v},"
        f"build_date={d} "
        f"build_info=1 {ts}"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_influx_line_format.py -v`
Expected: all tests PASS (the two new ones + all pre-existing characterization tests).

- [ ] **Step 5: Commit**

```bash
git add tests/test_influx_line_format.py src/towerwatch/tick.py
git commit -m "tick: add format_build_info_line helper for Prom build_info gauge"
```

---

### Task 2: Extend `service_heartbeat` with version/build_date (TDD)

**Files:**
- Test: `tests/test_events.py:69-75`
- Modify: `src/towerwatch/events.py:72-80`

- [ ] **Step 1: Update the existing heartbeat test to require new fields**

Replace `test_service_heartbeat_level_and_uptime` in `tests/test_events.py`:

```python
def test_service_heartbeat_level_and_uptime():
    loki = _make_loki()
    events.service_heartbeat(
        loki,
        uptime_h=2.5,
        version="abc1234",
        build_date="2026-04-23T16:30:25-07:00",
    )
    level, msg, extra = _pushed(loki)
    assert level == "WARN"
    assert extra["event"] == config.LOG_EVENT_HEARTBEAT
    assert extra["uptime_h"] == 2.5
    assert extra["version"] == "abc1234"
    assert extra["build_date"] == "2026-04-23T16:30:25-07:00"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_events.py::test_service_heartbeat_level_and_uptime -v`
Expected: FAIL with `TypeError: service_heartbeat() got an unexpected keyword argument 'version'`.

- [ ] **Step 3: Extend the function signature**

Replace `service_heartbeat` in `src/towerwatch/events.py`:

```python
def service_heartbeat(
    loki, *, uptime_h: float, version: str, build_date: str
) -> None:
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

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_events.py -v`
Expected: all PASS (the updated heartbeat test + all other event tests unchanged).

- [ ] **Step 5: Verify nothing else calls `service_heartbeat` yet**

Run: `grep -rn "service_heartbeat" src/ tests/`
Expected: only `src/towerwatch/events.py` definition, `src/towerwatch/app.py:82` call site, `src/towerwatch/config.py` `LOG_EVENT_HEARTBEAT` constant, and the test. App-side call will break next — that's Task 3.

- [ ] **Step 6: Run pyright to confirm app.py is now broken (expected)**

Run: `.venv/Scripts/python.exe -m pyright`
Expected: pyright reports an error at `src/towerwatch/app.py:82` — `service_heartbeat` is missing `version` and `build_date`. This is the intended transitional state.

- [ ] **Step 7: Do NOT commit yet** — leave the working tree dirty across Tasks 2–3 so they land as one atomic change. (If you must take a break, use `git stash` and resume.)

---

### Task 3: Wire `app.py` to emit both telemetry signals

**Files:**
- Modify: `src/towerwatch/app.py:78` (add build_info line emission)
- Modify: `src/towerwatch/app.py:80-82` (thread version/build_date into heartbeat call)

- [ ] **Step 1: Update `tick.py` imports**

Edit the `from towerwatch.tick import (...)` block in `src/towerwatch/app.py` (lines 19-25). Add `format_build_info_line`:

```python
from towerwatch.tick import (
    TickContext,
    collect_probes,
    format_build_info_line,
    format_influx_line,
    push_batch,
    update_connection_state,
)
```

- [ ] **Step 2: Emit the build_info line every tick**

In `src/towerwatch/app.py`, find line 78:

```python
startup_mod.write_marker(Path(config.LAST_ALIVE_MARKER_FILE), time.time())
push_batch(ctx, state, format_influx_line(fields, timestamp), any_connected)
```

Insert a `state.metric_batch.append(format_build_info_line(timestamp))` call between the marker write and the `push_batch` call so the build_info line rides the same batch:

```python
startup_mod.write_marker(Path(config.LAST_ALIVE_MARKER_FILE), time.time())
state.metric_batch.append(format_build_info_line(timestamp))
push_batch(ctx, state, format_influx_line(fields, timestamp), any_connected)
```

Note: `push_batch` flushes when `len(state.metric_batch) >= batch_size`. The `build_info` line pre-appended here becomes one of the entries in the batch — this is identical to the pattern already used on line 49 for `service_restart`.

- [ ] **Step 3: Pass version/build_date to `service_heartbeat`**

In `src/towerwatch/app.py`, find lines 80-82:

```python
if scheduler and scheduler.should_heartbeat(time.time()):
    uptime_h = round((time.monotonic() - state.start_ts) / 3600, 1)
    events_mod.service_heartbeat(loki, uptime_h=uptime_h)
```

Replace with:

```python
if scheduler and scheduler.should_heartbeat(time.time()):
    uptime_h = round((time.monotonic() - state.start_ts) / 3600, 1)
    events_mod.service_heartbeat(
        loki,
        uptime_h=uptime_h,
        version=config.BUILD_VERSION,
        build_date=config.BUILD_DATE,
    )
```

- [ ] **Step 4: Run full test suite**

Run: `.venv/Scripts/python.exe -m pytest -x -q`
Expected: all 172+ tests PASS. If any `test_main_narrative.py` test asserts on batch contents or heartbeat call shape, update it using the existing FakeLoki/FakeEvents pattern — not MagicMock.

- [ ] **Step 5: Run pyright**

Run: `.venv/Scripts/python.exe -m pyright`
Expected: `0 errors, 0 warnings, 0 informations`.

- [ ] **Step 6: Run ruff**

Run: `.venv/Scripts/python.exe -m ruff check src tests && .venv/Scripts/python.exe -m ruff format --check src tests`
Expected: no issues. If `ruff format --check` fails, run without `--check` to auto-fix, then re-run the check.

- [ ] **Step 7: Commit Tasks 2 + 3 together**

```bash
git add src/towerwatch/events.py src/towerwatch/app.py tests/test_events.py
git commit -m "telemetry: emit build_info Prom gauge + version in heartbeat"
```

---

### Task 4: Full CI and smoke verification

**Files:** none modified — this task only runs the existing CI tooling.

- [ ] **Step 1: Run full CI**

Run: `PYTHON=.venv/Scripts/python.exe ./ci.sh full`
Expected:
- `[1/5] ruff check... All checks passed!`
- `[2/5] ruff format --check... N files already formatted`
- `[3/5] pyright... 0 errors, 0 warnings, 0 informations`
- `[4/5] pytest... all passed`
- `[5/5] stamping src/towerwatch/_version.txt... stamped: <new-hash> <iso-date>`
- `[smoke] boot python -m towerwatch for 30s... smoke complete`
- `=== CI OK ===`

- [ ] **Step 2: Inspect smoke output for the build_info line**

During the 30s smoke, towerwatch boots and runs a few ticks. The smoke output won't show the batch contents directly, but the absence of crashes confirms `format_build_info_line` is callable and `service_heartbeat` signature changes didn't break boot. Manual verification of the actual Grafana series happens in Task 5.

- [ ] **Step 3: Confirm the version stamp updated**

Run: `cat src/towerwatch/_version.txt`
Expected: a newer short-hash than the pre-change stamp.

- [ ] **Step 4: Commit stamp**

`ci.sh` modifies `_version.txt` during the clean-tree step **only after** confirming the tree was clean. This step is typically included in the previous commit. If `_version.txt` shows up as untracked/modified now, include it:

```bash
git status
# if _version.txt shows as modified:
git add src/towerwatch/_version.txt
git commit --amend --no-edit
# otherwise skip this step
```

---

### Task 5: Deploy and verify in Grafana

**Files:** none modified — deployment and manual dashboard work.

- [ ] **Step 1: Deploy to the Pi**

Run: `./scripts/deploy.sh admin@100.76.154.81`
Expected: `=== Deploy OK — towerwatch is running ===` with a fresh `Started towerwatch.service` line in the tailed journal output.

- [ ] **Step 2: Wait ≥ 2 minutes for the first post-deploy metric batch to land**

Push batches every `PUSH_BATCH_SIZE` ticks (~2 min at defaults). You need at least one successful push after the deploy for `towerwatch_build_info` to appear in Prom.

- [ ] **Step 3: Verify the new Prom metric exists**

In Grafana Cloud → Explore → Prometheus datasource → query:

```
towerwatch_build_info
```

Expected: one series with labels `{host="towerwatch", version="<new-hash>", build_date="<iso-date>"}`, value 1. If the series is absent after 3 minutes, check `journalctl -u towerwatch -n 100 --no-pager` on the Pi for push errors.

- [ ] **Step 4: Replace the existing "Deployed Version" panel with the Prom-backed one**

The current panel is Loki-backed (`{job="towerwatch"} | json | event=\`service_restarted\``) and uses `lastNotNull` on a `/^Line$/` field. Grafana sorts logs oldest-first, so the reducer picks the *oldest* restart in the range, not the newest — producing a stale version string. Switching to the Prom gauge eliminates both the retention concern and the sort-order bug.

In the Towerwatch dashboard → click the "Deployed Version" panel → Edit:
- Datasource: switch from Loki to Prometheus (`grafanacloud-towerwatch-prom`)
- Query: replace with `last(towerwatch_build_info)` (no `expr` on logs, no `| json`)
- Reduce options:
    - Field filter: change `/^Line$/` to `/^version$/` so the reducer reads the `version` label
    - Calc: keep `lastNotNull`
- Keep panel title, size, position, and thresholds unchanged.
- Update the panel description to: "Currently deployed version (short-hash). Gauge re-emitted every tick — always shows the running version regardless of lookback window."
- Optionally add a second Prom query `last(towerwatch_build_info)` with field filter `/^build_date$/` as secondary text.

Save the dashboard.

- [ ] **Step 5: Verify the panel renders for a short lookback window**

Set dashboard range to "Last 15 minutes." Expected: the new panel shows the deployed version. Switch to "Last 5 minutes" and "Last 1 hour" — the panel should remain populated in all cases (because the gauge re-emits every tick).

- [ ] **Step 6: Export updated dashboard JSON back to the repo (optional follow-up)**

If you want the new panel to ship with the repo dashboard:

```bash
# In Grafana: Dashboard settings → JSON Model → Copy to clipboard
# Paste into grafana/dashboard.json, then:
git add grafana/dashboard.json
git commit -m "dashboard: add current-version stat panel (towerwatch_build_info)"
```

This step is optional — the spec's rollout section explicitly lists this as a follow-up, not a requirement of this plan.

---

## Self-Review

**Spec coverage:**
- Part 1 Prom `build_info` gauge → Task 1 (helper) + Task 3 Step 2 (call site). ✓
- Part 2 `service_heartbeat` version fields → Task 2 (signature) + Task 3 Step 3 (call site). ✓
- Data-budget assertion → verified implicitly by Task 5 deployment (one extra ~80B line/tick).
- Testing plan (events.py and tick.py tests) → Tasks 1 and 2. ✓
- Rollout steps → Task 5. ✓
- Deferred placement decision (tick.py vs app.py helper) → resolved: helper in `tick.py`, called from `app.py`. ✓

**Placeholder scan:** none found. Every step has exact file paths, concrete code, and explicit expected output.

**Type consistency:** `format_build_info_line(ts, *, version, build_date)` — signature used in Task 1 test, Task 1 implementation, and Task 3 Step 2 call site all match. `service_heartbeat(loki, *, uptime_h, version, build_date)` — matches across Task 2 test, Task 2 implementation, and Task 3 Step 3 call site.

**One deliberate cross-task state:** Task 2 leaves pyright red; Task 3 resolves it. This is flagged explicitly in Task 2 Step 6 and Task 2 Step 7.
