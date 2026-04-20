# Towerwatch Design Document

> **Purpose:** Reference for code reviews. Describes each component with file/function references.
> **Line numbers:** Based on commit `6cbedb1`. May drift — use function names as the stable anchor.

---

## 1. Overview

Towerwatch is a 5G connection quality monitor running on a Raspberry Pi 3B. It collects latency, jitter, packet loss, DNS resolution, TCP handshake, HTTP throughput, and cellular signal metrics on a 60-second loop, then pushes them to Grafana Cloud (Prometheus + Loki).

**Goal:** Build an evidence dataset of poor 5G quality to present to a cellular provider.

**Deployment:** systemd service on Pi 3B → wired Ethernet → Netgear M6 5G hotspot. Root filesystem is read-only (overlayfs); persistent state lives on a dedicated ext4 data partition (`/dev/mmcblk0p3`). Remote access via Tailscale.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    main() loop (60s)                    │
│                   pi/towerwatch.py:539                  │
├─────────────┬───────────┬───────────┬───────────────────┤
│  Every 60s  │ Every 60s │ Every 60s │    Conditional    │
│             │           │           │                   │
│ ┌─────────┐ │ ┌───────┐ │ ┌───────┐ │ ┌──────────────┐ │
│ │  Ping   │ │ │  TCP  │ │ │  DNS  │ │ │ HTTP Latency │ │
│ │ 3 tgts  │ │ │8.8.8.8│ │ │ 2 NS  │ │ │  (every 5m)  │ │
│ └────┬────┘ │ └───┬───┘ │ └───┬───┘ │ └──────┬───────┘ │
│      │      │     │     │     │     │        │         │
│ ┌─────────┐ │           │           │ ┌──────────────┐ │
│ │   M6    │ │           │           │ │  Throughput   │ │
│ │ Signal  │ │           │           │ │ (~4x/day rng) │ │
│ └────┬────┘ │           │           │ └──────┬───────┘ │
├──────┴──────┴─────┴─────┴─────┴─────┴────────┴─────────┤
│                    fields = { ... }                     │
│                         │                               │
│              format_influx_line(:117)                    │
│                         │                               │
│              _batch_and_push(:416)                       │
│          [accumulate PUSH_BATCH_SIZE lines]              │
│                         │                               │
│          ┌──── every PUSH_BATCH_SIZE cycles ────┐        │
│          │     push_metrics(batch)              │        │
│          │     on failure: drop batch (logged)  │        │
│          └──────────────────────────────────────┘        │
│                         │                               │
│              ┌──── Grafana Cloud ────┐                  │
│              │ Prometheus (metrics)  │                  │
│              │ Loki (structured log) │                  │
│              └───────────────────────┘                  │
└─────────────────────────────────────────────────────────┘
```

---

## 3. Components

### 3.1 Ping Probe

**Purpose:** ICMP ping burst to 3 targets — Google (8.8.8.8), Cloudflare (1.1.1.1), Gateway (192.168.1.1). Produces RTT avg/min/max, jitter (RFC 3550), packet loss, and per-target connectivity flags.

**Key functions (`pi/towerwatch.py`):**

| Function | Line | Signature | Behavior |
|----------|------|-----------|----------|
| `_build_ping_cmd` | 80 | `(target: str) -> list[str]` | Platform-specific argv: `-n`/`-w` (Win) vs `-c`/`-W` (Linux) |
| `_parse_ping_output` | 90 | `(stdout: str) -> dict` | Regex extraction of RTT summary + individual RTTs. Jitter = mean of consecutive RTT differences; falls back to `mdev` on Linux |
| `run_ping` | 137 | `(target: str) -> dict` | Subprocess with timeout guard (`PING_TIMEOUT_S * PING_COUNT + 5`). Returns `{rtt_avg, rtt_min, rtt_max, jitter, pkt_loss, connected}` |

**Error pattern:** On `TimeoutExpired`/`OSError` → returns all-zero dict with `connected=False`, emits Loki `WARN` with `LOG_EVENT_PING_FAILED`.

**Config:** `PROBE_TARGETS`, `PING_COUNT=10`, `PING_TIMEOUT_S=10` (`pi/config.py:8–15`)

---

### 3.2 TCP Connect Probe

**Purpose:** Measures TCP handshake latency to 8.8.8.8:443, isolating transport-layer performance from DNS/HTTP overhead.

| Function | Line | Signature | Behavior |
|----------|------|-----------|----------|
| `measure_tcp_connect` | 158 | `() -> float` | Raw socket connect with `perf_counter()` timing. Returns ms, 0 on failure |

**Error pattern:** Catches `OSError`/`socket.timeout` → returns 0, local log only (no Loki event).

**Config:** `TCP_TARGET_HOST`, `TCP_TARGET_PORT=443`, `TCP_TIMEOUT_S=5` (`pi/config.py:18–20`)

---

### 3.3 DNS Probe

**Purpose:** Measures DNS resolution time using explicit nameservers (bypasses systemd-resolved). Tests Google and Cloudflare DNS.

| Function | Line | Signature | Behavior |
|----------|------|-----------|----------|
| `measure_dns` | 176 | `(nameserver: str) -> float` | Fresh `dns.resolver.Resolver` per call, overrides `nameservers`. Resolves `example.com` A record. Returns ms, 0 on failure |

**Error pattern:** Catches all exceptions → returns 0, emits Loki `WARN` with `LOG_EVENT_DNS_FAILED`.

**Config:** `DNS_TARGETS=["8.8.8.8", "1.1.1.1"]`, `DNS_QUERY_DOMAIN="example.com"`, `DNS_TIMEOUT_S=5` (`pi/config.py:23–25`)

**Dependency:** `dnspython` (`dns.resolver`)

---

### 3.4 M6 Signal Probe

**Purpose:** Polls the Netgear M6 router admin API for cellular signal quality: RSRP, RSRQ, SINR, band.

| Function | Line | Signature | Behavior |
|----------|------|-----------|----------|
| `poll_m6_signal` | 279 | `() -> dict` | Uses persistent `_m6_session` (lazy-created at line 276). HTTP Basic auth to `M6_WWAN_URL`. Parses JSON response, handles both uppercase and lowercase field names for firmware resilience |

**Error pattern:** On 401 → clears session + Loki `WARN` (`LOG_EVENT_M6_AUTH_EXPIRED`), re-auths next cycle. All other exceptions → returns `{}` silently (`log.debug`). Empty dict means no M6 fields in that cycle's Influx line.

**Config:** `M6_WWAN_URL="http://192.168.1.1/api/wwanadv.json"`, `M6_TIMEOUT_S=5` (`pi/config.py:49–51`)

**Credential:** `secrets.M6_ADMIN_PASSWORD`

---

### 3.5 HTTP Latency Probe

**Purpose:** Downloads a 10KB Cloudflare CDN asset to measure HTTP round-trip time. Lightweight alternative to throughput tests.

| Function | Line | Signature | Behavior |
|----------|------|-----------|----------|
| `measure_http_latency` | 195 | `() -> float` | `requests.get` with full body read. Returns elapsed ms, 0 on failure |

**Schedule:** Every `HTTP_LATENCY_INTERVAL_S=300` seconds (5 min), tracked via `last_http_latency` in `main()` at line 591.

**Error pattern:** Catches all exceptions → returns 0, local log only.

**Config:** `HTTP_LATENCY_URL` (10KB), `HTTP_LATENCY_TIMEOUT_S=30` (`pi/config.py:31–33`)

---

### 3.6 HTTP Throughput Sample

**Purpose:** Downloads a 1MB Cloudflare CDN asset to estimate download throughput in Mbps. Replaces scheduled Ookla speedtests for routine monitoring (much cheaper data-wise).

| Function | Line | Signature | Behavior |
|----------|------|-----------|----------|
| `measure_http_throughput` | 214 | `() -> dict` | Computes `(bytes * 8) / elapsed_s / 1e6`. Returns `{http_throughput_ms, http_throughput_mbps}`, zeros on failure |
| `_build_daily_throughput_schedule` | 494 | `() -> list[float]` | Divides 24h into N equal slots, picks a random second in each. Skips past slots. Rebuilt at midnight |

**Schedule:** ~4 tests/day at random times (`HTTP_THROUGHPUT_TESTS_PER_DAY=4`). Schedule rebuilt when `tm_yday` changes (main loop line 597). Next test fires when `now >= schedule[0]` (line 601).

**Error pattern:** Emits Loki events on both success (`LOG_EVENT_HTTP_THROUGHPUT_OK`) and failure (`LOG_EVENT_HTTP_THROUGHPUT_FAILED`).

**Config:** `HTTP_THROUGHPUT_URL` (1MB), `HTTP_THROUGHPUT_TESTS_PER_DAY=4`, `HTTP_THROUGHPUT_TIMEOUT_S=60` (`pi/config.py:36–38`)

---

### 3.7 Speedtest (Manual Only)

**Purpose:** Runs Ookla CLI for accurate download/upload measurement. **Not auto-scheduled** — each test consumes ~400MB at 5G speeds.

| Function | Line | Signature | Behavior |
|----------|------|-----------|----------|
| `run_speedtest` | 244 | `() -> dict` | Subprocess with `--format=json --accept-license`. Parses `bandwidth` field → Mbps. Returns `{download_mbps, upload_mbps, success}` |

**Error pattern:** Three distinct paths: `TimeoutExpired` → `LOG_EVENT_SPEEDTEST_TIMEOUT`, other `Exception` → `LOG_EVENT_SPEEDTEST_FAILED`, success → `LOG_EVENT_SPEEDTEST_OK`. All failures return `success=0`.

**Config:** `SPEEDTEST_BINARY` (platform-conditional), `SPEEDTEST_TIMEOUT_S=120` (`pi/config.py:41–46`)

---

### 3.8 Metrics Push (Grafana Cloud)

**Purpose:** Formats collected metrics as Influx line protocol and pushes batches to Grafana Cloud Prometheus over HTTPS with gzip compression.

| Function | Line | Signature | Behavior |
|----------|------|-----------|----------|
| `_build_auth_header` | 319 | `() -> str` | `base64(INSTANCE_ID:API_KEY)` for HTTP Basic auth |
| `_get_grafana_session` | 327 | `() -> requests.Session` | Lazy singleton with auth + content-type headers. Reset to `None` on auth failure |
| `format_influx_line` | 338 | `(fields: dict, timestamp: int) -> str` | `towerwatch,host=towerwatch field1=v1,... <timestamp>`. Filters `None` values |
| `push_metrics` | 348 | `(lines: list[str]) -> bool` | Joins lines, gzip-compresses if `PUSH_COMPRESS`, POSTs to Grafana. Returns `True` if `status < 300` |

**Batch cadence:** Every `PUSH_BATCH_SIZE=2` cycles (~2 min at 60s interval). In-memory list `state.metric_batch` in `RuntimeState`. Batch is cleared before the push attempt; a failed push loses those samples.

**Error pattern:** On HTTP 401/403 → resets session. On any exception → resets session + Loki `WARN`. Returns `False`; caller (`_batch_and_push:416`) logs a warning and drops the batch. Metrics are intentionally not retried — this keeps uptime math honest.

**Config:** `GRAFANA_PUSH_URL`, `PUSH_BATCH_SIZE=2`, `PUSH_COMPRESS=True`, `GRAFANA_PUSH_TIMEOUT_S=10` (`pi/config.py:107–108`)

**Credentials:** `secrets.GRAFANA_INSTANCE_ID`, `secrets.GRAFANA_API_KEY`

---

### 3.9 Log Push (Loki)

**Purpose:** Ships structured JSON logs to Grafana Cloud Loki for event correlation and alerting. Fire-and-forget — failures are silently swallowed.

| Function | Line | Signature | Behavior |
|----------|------|-----------|----------|
| `push_log` | 389 | `(level: str, message: str, extra: dict = None)` | Level-filtered against `LOKI_PUSH_LEVEL`. Builds Loki push payload with `{job, host, level}` stream labels. Fresh `requests.post` per call (no persistent session). Bare `except: pass` |
| `_flush_log_buffer` | 158 | `()` | Drains the JSONL log buffer after a successful metric push. Reads `LOKI_BUFFER_FILE`, posts each entry to Loki, truncates the file on full drain |

**Payload structure:**
```json
{"streams": [{"stream": {"job": "towerwatch", "host": "towerwatch", "level": "warn"},
  "values": [["<nanosecond_timestamp>", "{\"msg\": \"...\", \"event\": \"...\"}"]]}]}
```

**Error pattern:** All exceptions swallowed with bare `except: pass` (line 415). Loki failures must never crash the monitor.

**Config:** `LOKI_PUSH_TIMEOUT_S=5`, `LOKI_PUSH_LEVEL="WARN"` (`pi/config.py:81–82`)

**Credentials:** `secrets.LOKI_URL`, `secrets.LOKI_USER`, `secrets.LOKI_TOKEN`

---

### 3.10 Log Buffer

**Purpose:** Append-only JSONL buffer for Loki log payloads on the writable data partition. Survives crashes, SIGTERM, and power loss. Accumulates during outages, flushed on reconnect. Metrics are **not** buffered — failed batches are dropped intentionally so Prometheus gaps are truthful.

| Function | File | Signature | Behavior |
|----------|------|-----------|----------|
| `_buffer_log_entry` | `pi/loki.py:42` | `(payload: dict)` | Appends JSON payload + newline to `LOKI_BUFFER_FILE` with `fsync()`. If file ≥ `LOKI_BUFFER_MAX_BYTES`, trims oldest ~10% before appending |
| `_flush_log_buffer` | `pi/towerwatch.py:158` | `()` | Reads buffer, posts each entry to Loki via `_post_loki`. On full drain, unlinks the file. On partial failure, truncates to remaining tail |

**Write path:** `push_log` in `pi/loki.py` calls `_buffer_log_entry` on any network exception.

**Flush path:** `_flush_log_buffer` is called from `_batch_and_push` after every successful metric push, and once at startup after markers are read.

**Config:** `LOKI_BUFFER_FILE`, `LOKI_BUFFER_MAX_BYTES=256KB` (`pi/config.py:111–114`)

---

### 3.11 Connection State Tracking

**Purpose:** Tracks outage transitions (UP→DOWN, DOWN→UP), computes outage duration, and emits Loki events for outage start/restore.

| Function | Line | Signature | Behavior |
|----------|------|-----------|----------|
| `update_connection_state` | 58 | `(connected: bool, timestamp: int)` | On restore: computes duration, increments `_total_outage_s`, emits `LOG_EVENT_CONN_RESTORED`. On drop: records `_outage_start`, increments `_outage_count`, emits `LOG_EVENT_CONN_DOWN` |

**Module-level state (`pi/towerwatch.py:52–55`):**

| Variable | Type | Purpose |
|----------|------|---------|
| `_connected` | `bool` | Current connection state |
| `_outage_start` | `int` | Unix timestamp when outage began |
| `_outage_count` | `int` | Lifetime outage count |
| `_total_outage_s` | `int` | Cumulative outage seconds |

Called from main loop at line 575 after all ping probes complete.

---

### 3.12 Main Loop Orchestrator

**Purpose:** Entry point and 60-second cycle orchestrator. Sequences all probes, manages push timing, and handles shutdown.

| Function | Line | Signature | Behavior |
|----------|------|-----------|----------|
| `main` | 539 | `()` | Startup: `wait_for_data_partition()`, init schedule, check for leftover buffer. Loop: run probes → buffer → batch push → sleep remainder |
| `wait_for_data_partition` | 464 | `(timeout: int = 30)` | Linux: polls `mountpoint -q` until mounted. Windows: `mkdir` and return. Defers Loki warning if partition missing |
| `_handle_sigterm` | 526 | `(signum, frame)` | Sets `_shutdown_requested = True`. Registered on Linux only (line 532) |

**Cycle structure (lines 557–644):**
1. Ping all `PROBE_TARGETS` → per-target fields + aggregate `connected`
2. `measure_tcp_connect()` → `tcp_connect_ms`
3. `measure_dns()` per nameserver → `dns_resolve_ms_{ns}`
4. `poll_m6_signal()` → `m6_rsrp`, `m6_rsrq`, `m6_sinr`, `m6_band`
5. `measure_http_latency()` (every 5 min) → `http_latency_ms`
6. `measure_http_throughput()` (random daily schedule) → `http_throughput_ms`, `http_throughput_mbps`
7. Record `collection_duration_ms`
8. `buffer_line()` → conditional `push_metrics()` + `clear_buffer()`
9. Sleep remainder of 60s

**`__main__` guard (lines 650–663):** `KeyboardInterrupt` → clean exit. Other `Exception` → log critical + Loki push + re-raise (systemd restarts). `finally` → reports buffered line count.

---

## 4. Configuration Reference

All constants in `pi/config.py` (100 lines). Secrets in `pi/secrets.py` (gitignored).

### Probe Targets & Timing

| Constant | Value | Line | Notes |
|----------|-------|------|-------|
| `PROBE_TARGETS` | 3 tuples | 8 | `(ip, label)` — labels baked into field names |
| `PING_COUNT` | 10 | 14 | Probes per burst |
| `PING_TIMEOUT_S` | 10 | 15 | |
| `METRIC_INTERVAL_S` | 60 | 28 | Main loop period (was 30, halved for data cap) |
| `TCP_TARGET_HOST` | `8.8.8.8` | 18 | |
| `TCP_TARGET_PORT` | 443 | 19 | |
| `TCP_TIMEOUT_S` | 5 | 20 | |
| `DNS_TARGETS` | `["8.8.8.8", "1.1.1.1"]` | 23 | |
| `DNS_QUERY_DOMAIN` | `example.com` | 24 | |
| `DNS_TIMEOUT_S` | 5 | 25 | |

### HTTP Probes

| Constant | Value | Line | Notes |
|----------|-------|------|-------|
| `HTTP_LATENCY_URL` | Cloudflare 10KB | 31 | |
| `HTTP_LATENCY_INTERVAL_S` | 300 | 32 | 5 minutes |
| `HTTP_LATENCY_TIMEOUT_S` | 30 | 33 | |
| `HTTP_THROUGHPUT_URL` | Cloudflare 1MB | 36 | |
| `HTTP_THROUGHPUT_TESTS_PER_DAY` | 4 | 37 | Random schedule |
| `HTTP_THROUGHPUT_TIMEOUT_S` | 60 | 38 | |

### Speedtest & M6

| Constant | Value | Line | Notes |
|----------|-------|------|-------|
| `SPEEDTEST_BINARY` | platform-conditional | 41–44 | `.exe` on Win, `/usr/bin/speedtest` on Linux |
| `SPEEDTEST_TIMEOUT_S` | 120 | 45 | |
| `M6_WWAN_URL` | `http://192.168.1.1/api/wwanadv.json` | 50 | |
| `M6_TIMEOUT_S` | 5 | 51 | |

### Push & Buffering

| Constant | Value | Line | Notes |
|----------|-------|------|-------|
| `GRAFANA_PUSH_URL` | Grafana Cloud Influx endpoint | 54–56 | `precision=s` |
| `GRAFANA_PUSH_TIMEOUT_S` | 10 | 58 | |
| `INFLUX_MEASUREMENT` | `towerwatch` | 59 | |
| `INFLUX_HOST_TAG` | `towerwatch` | 60 | Single tag on all lines |
| `PUSH_BATCH_SIZE` | 10 | 63 | 10 cycles × 60s = push every 10 min |
| `PUSH_COMPRESS` | `True` | 64 | gzip Influx POST body |
| `BUFFER_MAX_BYTES` | 512 KB | 65 | Drops oldest 10% when exceeded |
| `BUFFER_FILE` | platform-conditional | 68–75 | `./data/buffer/metrics.csv` (Win) or `/opt/towerwatch/data/buffer/metrics.csv` (Linux) |

### Logging & Loki

| Constant | Value | Line | Notes |
|----------|-------|------|-------|
| `LOG_LEVEL` | `INFO` | 78 | Local console logging |
| `LOKI_PUSH_TIMEOUT_S` | 5 | 81 | |
| `LOKI_PUSH_LEVEL` | `WARN` | 82 | Minimum level shipped to Loki (`INFO` for testing) |

### Log Event Identifiers (`pi/config.py:85–99`)

Stable machine-readable keys used in the `event` field of Loki JSON payloads. Used for LogQL filtering (e.g., `| json | event="ping_failed"`).

`service_started`, `connection_down`, `connection_restored`, `ping_failed`, `dns_failed`, `speedtest_complete`, `speedtest_timeout`, `speedtest_failed`, `m6_auth_expired`, `metrics_push_failed`, `metrics_buffered`, `buffer_flushed`, `partition_not_detected`, `http_throughput_complete`, `http_throughput_failed`

### Secrets (`pi/secrets.py`)

| Variable | Service | Notes |
|----------|---------|-------|
| `GRAFANA_INSTANCE_ID` | Prometheus | Numeric instance ID |
| `GRAFANA_API_KEY` | Prometheus | MetricsPublisher role API key |
| `LOKI_URL` | Loki | Full push endpoint URL (different instance) |
| `LOKI_USER` | Loki | Numeric Loki instance ID |
| `LOKI_TOKEN` | Loki | Same API token as Prometheus |
| `M6_ADMIN_PASSWORD` | Cellular router | Router admin login password |

---

## 5. Platform Branching

Detection: `IS_WINDOWS = sys.platform == "win32"` (`pi/towerwatch.py:46`)

| Location | Windows | Linux |
|----------|---------|-------|
| `_build_ping_cmd` (:80) | `-n COUNT -w TIMEOUT_MS` | `-c COUNT -W TIMEOUT_S` |
| `_parse_ping_output` RTT regex (:98–116) | `Minimum = Nms...Maximum...Average` | `rtt min/avg/max/mdev = N.N/...` |
| `_parse_ping_output` individual RTTs (:119–122) | `time[=<](\d+)ms` | `time=([\d.]+)` |
| `wait_for_data_partition` (:467–468) | `mkdir` + return | Polls `mountpoint -q` with 30s timeout |
| SIGTERM handler (:532–533) | Not registered | `signal.signal(SIGTERM, ...)` |
| `SPEEDTEST_BINARY` (`config.py:41–44`) | `./speedtest_bin/speedtest.exe` | `/usr/bin/speedtest` |
| Buffer/data paths (`config.py:68–75`) | `./data/...` relative | `/opt/towerwatch/data/...` absolute |

---

## 6. Observability

### Metric Naming

All metrics become `towerwatch_{field_name}` in Prometheus (via Influx line protocol). Target labels are baked into field names — not Prometheus label selectors.

**Examples:** `towerwatch_rtt_avg_google`, `towerwatch_jitter_cloudflare`, `towerwatch_tcp_connect_ms`, `towerwatch_m6_rsrp`

**Units:** `_ms` throughout (not Prometheus-standard seconds). Exception: `_mbps` for throughput fields.

### Loki Log Structure

Stream labels: `{job="towerwatch", host="towerwatch", level="warn"}`. Each log value is a JSON string with a stable `event` field for LogQL filtering.

### Dashboard Panels (`grafana/dashboard.json`)

| Panel | Type | Metric(s) | Notes |
|-------|------|-----------|-------|
| Connection Uptime | stat | `towerwatch_connected` | % over range. Red <95%, yellow 95–99.5%, green ≥99.5% |
| Current Status | stat | `towerwatch_connected` | Live UP/DOWN mapping |
| Speedtest Health | stat | `towerwatch_speedtest_success` | Last 12h |
| Google (8.8.8.8) | timeseries | `rtt_avg_google`, `rtt_max_google`, `jitter_google` | Log2 scale |
| Cloudflare (1.1.1.1) | timeseries | `rtt_avg_cloudflare`, `rtt_max_cloudflare`, `jitter_cloudflare` | Log2 scale |
| Gateway (192.168.1.1) | timeseries | `rtt_avg_gateway`, `rtt_max_gateway`, `jitter_gateway` | Log2 scale |
| DNS Resolution Time | timeseries | `dns_resolve_ms_8_8_8_8`, `dns_resolve_ms_1_1_1_1` | Log2 scale |
| TCP Connection Time | timeseries | `tcp_connect_ms` | Linear scale |
| HTTP Latency Probe | timeseries | `http_latency_ms` | Every 5 min |
| HTTP Throughput Sample | timeseries | `http_throughput_mbps` | Bar chart, ~4x/day |
| Packet Loss | timeseries | `pkt_loss_google`, `pkt_loss_cloudflare`, `pkt_loss_gateway` | Bar chart, % |
| Download / Upload Speed | timeseries | `download_mbps`, `upload_mbps` | Manual speedtest results |
| M6 Signal Quality | timeseries | `m6_rsrp`, `m6_rsrq`, `m6_sinr` | dBm/dB |
| Towerwatch Event Log | logs (Loki) | `{job="towerwatch"} \| json` | Full-width structured log |

**Annotations:** "Outages" (red, `connected == 0`) and "Pi Offline" (purple, `absent_over_time(...[2m])`).

---

## 7. Infrastructure

### install.sh (`pi/install.sh`, 175 lines)

One-shot Pi setup. Must run as root.

| Step | Lines | What it does |
|------|-------|--------------|
| 1/8 System packages | 23–27 | `python3-pip`, `python3-venv`, `fake-hwclock` |
| 2/8 Python deps | 29–30 | `pip3 install -r requirements.txt` (`requests`, `dnspython`) |
| 3/8 Ookla CLI | 33–45 | Downloads ARM binary directly (apt repo is broken) |
| 4/8 System user | 47–51 | Creates `towerwatch` user (no-login, no-home) |
| 5/8 App files | 54–66 | Copies `towerwatch.py`, `config.py`, `secrets.py` to `/opt/towerwatch` |
| 6/8 Data partition | 69–124 | Mounts `/dev/mmcblk0p3` at `/opt/towerwatch/data`. Creates Tailscale bind mount unit (overlayfs workaround) |
| 7/8 fakehwclock | 127–154 | Redirects clock file to data partition. Enables hardware watchdog |
| 8/8 systemd service | 157–160 | Installs + enables `towerwatch.service` |

### deploy.sh (`deploy.sh`, 47 lines)

Pushes code updates from dev machine to Pi via SSH. Target is the first positional argument (`user@host` — typically a Tailscale IP or `.local` mDNS name).

1. **Pull + copy** (inside `overlayroot-chroot`): `git pull --ff-only`, copy files to `/opt/towerwatch/`
2. **Restart** (outside chroot): `systemctl restart towerwatch`
3. **Verify**: checks `is-active`, tails journal on failure

### systemd unit (`pi/towerwatch.service`)

- `After=network-online.target`, `Restart=on-failure`, `RestartSec=10`
- `User=towerwatch`, `WorkingDirectory=/opt/towerwatch`
- Logs to journal (`SyslogIdentifier=towerwatch`)

### Data Partition

Dedicated 3rd partition (`/dev/mmcblk0p3`, ext4, ~1GB) at `/opt/towerwatch/data`. Holds:
- `buffer/metrics.csv` — metric buffer
- `tailscale-state/` — Tailscale identity (bind-mounted to `/var/lib/tailscale`)
- `fake-hwclock.data` — persisted clock

Required because the root filesystem runs overlayfs (read-only, resets on reboot).

---

## 8. Error Handling Patterns

| Pattern | Used by | Behavior |
|---------|---------|----------|
| **Sentinel zeros** | `run_ping`, `measure_tcp_connect`, `measure_dns`, `measure_http_latency` | Return 0/empty on failure. Influx line still written — dashboards see the data point but with zero values |
| **Success flag** | `run_speedtest` | Returns `{success: 0}` on failure so dashboards distinguish "failed" from "not run" |
| **Silent swallow** | `push_log` | Bare `except: pass` — Loki failures must never interrupt monitoring |
| **Session reset + retry** | `push_metrics`, `poll_m6_signal` | Clears persistent session on auth failure or exception. Recreates lazily next cycle |
| **Deferred warnings** | `wait_for_data_partition` | Queues Loki messages in `_deferred_warnings` during startup (network may not be up). Flushed after first successful metric push |
| **Buffer persistence** | `buffer_line` | `fsync()` after every write. Data survives crashes, SIGTERM, and power loss |
| **Retry on failure** | main loop push block | `cycles_since_push` not reset on failure → retries every cycle until success |

---

## 9. Dependencies

### Runtime

| Package | Version | Purpose |
|---------|---------|---------|
| `requests` | 2.33.1 | HTTP client for all probes, Grafana push, Loki push, M6 poll |
| `dnspython` | 2.8.0 | DNS resolution with explicit nameservers |

### External Binaries

| Binary | Platform | Used by |
|--------|----------|---------|
| `ping` | Both (system) | `run_ping` via `subprocess.run` |
| `speedtest` | Both (Ookla CLI) | `run_speedtest` via `subprocess.run` |
| `mountpoint` | Linux only | `wait_for_data_partition` via `subprocess.run` |

### Standard Library (notable)

`base64` (auth encoding), `gzip` (push compression), `socket` (TCP probe), `subprocess` (ping, speedtest), `statistics` (jitter calculation), `signal` (SIGTERM handler), `pathlib.Path` (buffer file ops)
