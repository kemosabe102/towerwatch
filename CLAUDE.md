# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Towerwatch is a 5G connection quality monitor that runs on a Raspberry Pi 3B. It continuously measures latency, jitter, packet loss, DNS resolution, TCP connection time, HTTP download speed, Ookla speedtest throughput, and Netgear M6 signal quality — then pushes metrics to Grafana Cloud (Prometheus via Influx line protocol) and structured logs to Loki.

**Goal:** Build an evidence dataset of poor 5G connection quality to present to a cellular provider.

**Target hardware:** Raspberry Pi 3B with wired Ethernet to a Netgear Nighthawk M6 5G hotspot.
**Archived:** The original Arduino Uno implementation is preserved in `arduino/` — it was replaced because the Uno has no TLS support and Grafana Cloud requires HTTPS.

## Running Locally (Windows Testing)

The script is cross-platform. To test from a Windows machine:

```bash
cd pi
cp secrets.py.example secrets.py   # fill in Grafana + Loki credentials
pip install requests dnspython
python towerwatch.py                # Ctrl+C to stop
```

Platform differences handled automatically via `sys.platform`:
- Ping: `-n`/`-w` (Windows) vs `-c`/`-W` (Linux)
- Paths: `./data/` (Windows) vs `/opt/towerwatch/data/` (Linux)
- Speedtest binary: `./speedtest_bin/speedtest.exe` (Windows) vs `/usr/bin/speedtest` (Linux)
- Data partition: skips `mountpoint` check on Windows

M6 signal polling and speedtest will fail gracefully if unavailable — this is expected on Windows.

## Architecture

`pi/towerwatch.py` is a persistent Python process (systemd `Type=simple` on Pi) running a 60-second loop:

1. **ICMP ping** — 10-probe burst to 3 targets (Google, Cloudflare, Gateway), parses RTT avg/min/max, jitter (RFC 3550), packet loss
2. **TCP connect** — socket handshake timing to 8.8.8.8:443
3. **DNS resolution** — dnspython with explicit nameservers (bypasses systemd-resolved)
4. **M6 signal** — polls router admin API for RSRP/RSRQ/SINR/band
5. **HTTP download** (every 5 min) — timed 500KB fetch from Cloudflare CDN
6. **Speedtest** (every 6 hours) — Ookla CLI via subprocess with 120s timeout
7. **Push metrics** — Influx line protocol to Grafana Cloud Prometheus
8. **Push logs** — structured JSON to Grafana Cloud Loki (fire-and-forget)
9. **Buffer on failure** — atomic CSV write to writable partition, flush on reconnect

## Key Files

- `pi/config.py` — All constants: `PROBE_TARGETS` (ip, label) tuples, intervals, Loki config, `LOG_EVENT_*` identifiers
- `pi/secrets.py` — **Gitignored**. Grafana + Loki credentials. Copy from `secrets.py.example`
- `pi/install.sh` — One-shot Pi setup: deps, speedtest CLI, data partition, Tailscale, systemd
- `grafana/dashboard.json` — 13-panel dashboard, import directly into Grafana Cloud

## Metric Naming

Influx line protocol fields become Prometheus metrics as `towerwatch_{field_name}`. Target labels are baked into field names (e.g., `rtt_avg_google`, `jitter_cloudflare`) — not Prometheus label selectors. Units are `_ms` throughout (not Prometheus-standard seconds).

## Observability

- **Metrics**: pushed to Grafana Cloud Prometheus via Influx line protocol (`/api/v1/push/influx/write?precision=s`)
- **Logs**: pushed directly to Loki HTTP API from Python (no sidecar — Grafana Alloy doesn't run on Pi 3B). Each log entry has a stable `event` field for LogQL filtering (e.g., `| json | event="ping_failed"`)
- **Log levels**: controlled by `LOKI_PUSH_LEVEL` in config.py. Use `INFO` for home testing, `WARN` in production
- **Deferred warnings**: boot-time warnings (before network is up) are queued and flushed on first successful metric push
