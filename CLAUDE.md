# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Towerwatch is a network quality monitor that runs on a Raspberry Pi. It continuously measures latency, jitter, packet loss, DNS resolution, TCP connection time, HTTP latency/throughput, Ookla speedtest throughput, and (optionally) cellular-router signal metrics — then pushes metrics to Grafana Cloud (Prometheus via Influx line protocol) and structured logs to Loki.

**Typical goal:** Build a long-running evidence dataset of connection quality to present to an ISP or cellular provider.

**Target hardware:** Any Raspberry Pi with wired Ethernet to the router under test. Developed on a Pi 3B against a Netgear Nighthawk M6 5G hotspot; the cellular-signal probe (`pi/probes/m6.py`) is optional and disables itself cleanly if the router isn't reachable.

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

Router signal polling and speedtest will fail gracefully if unavailable — this is expected on Windows.

## Architecture

`pi/towerwatch.py` is a persistent Python process (systemd `Type=simple` on Pi) running a 60-second loop:

1. **ICMP ping** — 10-probe burst to 3 targets (Google, Cloudflare, Gateway), parses RTT avg/min/max, jitter (RFC 3550), packet loss
2. **TCP connect** — socket handshake timing to 8.8.8.8:443
3. **DNS resolution** — dnspython with explicit nameservers (bypasses systemd-resolved)
4. **Router signal** (optional) — polls router admin API for RSRP/RSRQ/SINR/band
5. **HTTP latency** (every 5 min) — timed 10 KB fetch from a fast CDN
6. **HTTP throughput** (~4x/day, random schedule) — timed 1 MB fetch
7. **Ookla speedtest** — manual only (~400 MB/run at 5G speeds)
8. **Push metrics** — Influx line protocol to Grafana Cloud Prometheus (batched, gzipped)
9. **Push logs** — structured JSON to Grafana Cloud Loki (fire-and-forget)
10. **Buffer on failure** — atomic CSV write to writable partition, flush on reconnect. Gaps ≥ 10 min also POST a sticky region annotation to Grafana.

## Key Files

- `pi/config.py` — All constants: `PROBE_TARGETS` (ip, label) tuples, intervals, Loki config, `LOG_EVENT_*` identifiers
- `pi/secrets.py` — **Gitignored**. Grafana + Loki credentials. Copy from `secrets.py.example`
- `pi/install.sh` — One-shot Pi setup: deps, speedtest CLI, data partition, Tailscale, systemd
- `grafana/dashboard.json` — 13-panel dashboard, import directly into Grafana Cloud

## Metric Naming

Influx line protocol fields become Prometheus metrics as `towerwatch_{field_name}`. Target labels are baked into field names (e.g., `rtt_avg_google`, `jitter_cloudflare`) — not Prometheus label selectors. Units are `_ms` throughout (not Prometheus-standard seconds).

## Observability

- **Metrics**: pushed to Grafana Cloud Prometheus via Influx line protocol (`/api/v1/push/influx/write?precision=s`)
- **Logs**: pushed directly to Loki HTTP API from Python (no sidecar — Grafana Alloy is too heavy for older Pis). Each log entry has a stable `event` field for LogQL filtering (e.g., `| json | event="ping_failed"`)
- **Log levels**: controlled by `LOKI_PUSH_LEVEL` in config.py. Use `INFO` for local dev only, `WARN` in production (`INFO` in production will flood Loki)
- **Deferred warnings**: boot-time warnings (before network is up) are queued and flushed on first successful metric push

## Deployment

- **Pi code:** `git push && bash deploy.sh <user>@<host>` (or a `deploy-local.sh` wrapper) — SSHes in, `git pull --ff-only`s the repo, copies `pi/*.py` to `/opt/towerwatch/`, restarts the systemd unit
- **Dashboard:** import `grafana/dashboard.json` manually in Grafana Cloud (Settings → JSON Model → paste → save)
- **Secrets:** edit directly on Pi at `/opt/towerwatch/secrets.py` — never committed
- **Outage annotations token (one-time):** Grafana Cloud UI → Administration → Service accounts → create `towerwatch-annotations` with role `Editor` (or custom role with `annotations:write`) → Add service account token → paste into `GRAFANA_ANNOTATION_TOKEN` in `/opt/towerwatch/secrets.py`. Also confirm `GRAFANA_ANNOTATIONS_URL` in `config.py` points to your `<stack>.grafana.net` (the user-facing URL, not the `prometheus-prod-*` push endpoint).

## Data Budget

Towerwatch is designed to run over metered connections (e.g. a cellular hotspot). At defaults the probes use roughly **230 MB/month** (batched + gzipped pushes dominate). Any change that adds network traffic — new probes, larger downloads, higher frequencies, lower batch sizes — must be evaluated against the operator's cap before merging. Ookla speedtest is manual-only (~400 MB/run at 5G speeds) for this reason.
