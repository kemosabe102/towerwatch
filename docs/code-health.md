# Code Health Tracker

> **Last analyzed:** 2026-04-19 | **Tool:** `radon` (cyclomatic complexity + maintainability index)
> **Baseline commit:** `877596c`

---

## Summary

| Metric | Value |
|---|---|
| Total Python LOC | 846 |
| Total SLOC | 481 |
| Functions analyzed | 36 |
| Average CC | 3.69 (A) |
| Functions CC ≥ 10 | 4 |
| Maintainability Index (towerwatch.py) | 29.23 (A) |
| Maintainability Index (config.py) | 79.91 (A) |

---

## Function Complexity (ranked by CC)

| Function | File:Line | CC | Grade | Review Status | Notes |
|---|---|---|---|---|---|
| `_collect_probes` | towerwatch.py:619 | 12 | **C** | Reviewed | Sequential probe orchestration — complexity from ping loop branching |
| `_flush_log_buffer` | towerwatch.py:452 | 11 | **C** | — | Monitor priority — log batching and deferred flush logic |
| `_maybe_push` | towerwatch.py:603 | 7 | B | Reviewed | Extracted from main — batch push + deferred flush |
| `update_connection_state` | towerwatch.py:58 | 6 | B | Reviewed | Two-transition state machine — complexity inherent to connect/disconnect logic |
| `wait_for_data_partition` | towerwatch.py:473 | 6 | B | Reviewed | Boot-time partition poll with Windows early-return and degraded-mode fallback |
| `_parse_ping_output` | towerwatch.py:118 | 5 | A | Reviewed | Simplified via `_parse_rtt_stats` + `_calc_jitter` extraction |
| `_extract_m6_fields` | towerwatch.py:296 | 5 | A | Reviewed | Table-driven field extraction from M6 JSON |
| `push_metrics` | towerwatch.py:357 | 5 | A | Reviewed | HTTP push with optional gzip, session reset on auth failure |
| `push_log` | towerwatch.py:398 | 5 | A | — | |
| `read_buffer` | towerwatch.py:455 | 5 | A | — | |
| `_batch_and_push` | towerwatch.py:735 | 4 | A | — | Metric batching and deferred flush coordination |
| `_parse_rtt_stats` | towerwatch.py:90 | 4 | A | Reviewed | Platform-branching RTT regex, extracted from `_parse_ping_output` |
| `run_speedtest` | towerwatch.py:245 | 4 | A | — | |
| `_build_daily_throughput_schedule` | towerwatch.py:503 | 4 | A | — | |
| `main` | towerwatch.py:773 | 4 | A | Reviewed | Reduced from CC 21 via helper extraction |
| `push_annotation` | towerwatch.py:491 | 3 | A | — | Grafana outage annotation posting |
| `_calc_jitter` | towerwatch.py:110 | 3 | A | Reviewed | RFC 3550 jitter, extracted from `_parse_ping_output` |
| `poll_m6_signal` | towerwatch.py:306 | 3 | A | Reviewed | Reduced from CC 13 via table-driven field map |
| `format_influx_line` | towerwatch.py:347 | 3 | A | — | |
| `buffer_line` | towerwatch.py:438 | 3 | A | — | |
| `_build_loki_payload` | towerwatch.py:400 | 2 | A | — | Log JSON payload construction |
| `_buffer_log_entry` | towerwatch.py:417 | 2 | A | — | CSV log entry buffering |
| `_build_ping_cmd` | towerwatch.py:80 | 2 | A | — | |
| `run_ping` | towerwatch.py:138 | 2 | A | — | |
| `measure_tcp_connect` | towerwatch.py:159 | 2 | A | — | |
| `measure_dns` | towerwatch.py:177 | 2 | A | — | |
| `measure_http_latency` | towerwatch.py:196 | 2 | A | — | |
| `measure_http_throughput` | towerwatch.py:215 | 2 | A | — | |
| `_persist_last_push_ts` | towerwatch.py:674 | 2 | A | — | Persist last successful push timestamp |
| `_load_last_push_ts` | towerwatch.py:690 | 2 | A | — | Load last successful push timestamp |
| `_touch_last_alive` | towerwatch.py:699 | 2 | A | — | Update last alive signal timestamp |
| `_load_last_alive_ts` | towerwatch.py:709 | 2 | A | — | Load last alive signal timestamp |
| `_record_outage_annotation` | towerwatch.py:718 | 2 | A | — | Record persistent outage annotation |
| `_ensure_m6_session` | towerwatch.py:287 | 2 | A | Reviewed | Lazy session init, extracted from `poll_m6_signal` |
| `_get_grafana_session` | towerwatch.py:336 | 2 | A | — | |
| `_maybe_heartbeat` | towerwatch.py:759 | 2 | A | — | Conditional heartbeat push on idle |
| `flush_deferred_warnings` | towerwatch.py:428 | 2 | A | — | |
| `clear_buffer` | towerwatch.py:463 | 2 | A | — | |
| `_build_auth_header` | towerwatch.py:328 | 1 | A | — | |
| `_handle_sigterm` | towerwatch.py:535 | 1 | A | — | |
| `_log_cycle` | towerwatch.py:663 | 1 | A | Reviewed | Cycle logging, extracted from main |

---

## Review Priority

### Monitor (CC ≥ 10)
- [x] **`_collect_probes()`** — CC 12. Sequential probe runner, inherited from `main()` extraction. Complexity is from the ping loop's per-target field mapping — acceptable for now.
- [ ] **`_flush_log_buffer()`** — CC 11. Deferred log buffer flush with multi-destination writes (Loki + Grafana). Monitor for potential simplification.

### Previously Resolved
- [x] **`main()`** — CC 21 → 4. Extracted `_collect_probes`, `_maybe_push`, `_log_cycle`.
- [x] **`poll_m6_signal()`** — CC 13 → 3. Table-driven `_M6_FIELD_MAP` + `_extract_m6_fields` + `_ensure_m6_session`.
- [x] **`_parse_ping_output()`** — CC 10 → 5. Extracted `_parse_rtt_stats` + `_calc_jitter`.
