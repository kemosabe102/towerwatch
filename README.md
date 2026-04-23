# Towerwatch — Cellular/Broadband Network Quality Monitor

A continuous network-quality probe for a Raspberry Pi. Ships latency, jitter, packet loss, DNS/TCP/HTTP timings, throughput, and (optionally) cellular-radio signal metrics to Grafana Cloud. Buffers **logs** to disk during outages and flushes on reconnect; metrics are dropped on push failure so uptime can't be backfilled. Designed to build a long-running evidence dataset for your ISP or cellular provider.

> **At a glance**
> - **Runtime:** Python 3 on Raspberry Pi OS (also runs on Windows/macOS for dev).
> - **Outputs:** Prometheus metrics (Influx line protocol) + structured JSON logs (Loki), both to Grafana Cloud over HTTPS.
> - **Cadence:** 60 s main loop. Pushes batched every ~2 min.
> - **Offline behaviour:** atomic JSONL **log** buffer on the data partition, capped at 256 KB, flushed on reconnect; metrics are not buffered.
> - **Data cost:** ~230 MB/month at defaults — tune `config.py` if you're on a metered connection.

---

## Further reading

| Document | Purpose |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Design narrative — composition root, Protocols, TickContext, testing philosophy |
| [`docs/runbook.md`](docs/runbook.md) | Symptom-indexed ops runbook — start here at 2am |
| [`docs/setup-pi.md`](docs/setup-pi.md) | Tailscale + read-only root hardening for unattended deployments |
| [`docs/probe-m6.md`](docs/probe-m6.md) | Optional Netgear M6 cellular signal probe — setup or port to another router |
| [`docs/design.md`](docs/design.md) | Per-component code reference (functions, config tables, error patterns) |
| [`docs/bench-tests.md`](docs/bench-tests.md) | Failure-mode bench test catalog |
| [`pi/bench/README.md`](pi/bench/README.md) | Bench harness quick-start — prerequisites, running, adding tests |
| [`docs/code-health.md`](docs/code-health.md) | Radon complexity tracker |
| [`CLAUDE.md`](CLAUDE.md) | Agent-facing invariants, CI/CD workflow, deploy gotchas |

---

## Contents

1. [Architecture](#architecture)
2. [What it measures](#what-it-measures)
3. [Repository layout](#repository-layout)
4. [Hardware](#hardware)
5. [Quick start](#quick-start)
6. [Configuration](#configuration)
7. [Deploy](#deploy)
8. [Grafana dashboard & alerting](#grafana-dashboard--alerting)
9. [Data budget](#data-budget)
10. [For AI assistants](#for-ai-assistants)

---

## Architecture

`pi/towerwatch.py` is a long-running Python process managed by systemd (`Type=simple`). Each 60 s tick runs:

1. **ICMP ping** — 10-probe burst per target; parses RTT avg/min/max, jitter (RFC 3550), loss %.
2. **TCP connect** — socket handshake timing to a known endpoint.
3. **DNS resolution** — `dnspython` against explicit nameservers (bypasses `systemd-resolved`).
4. **Router signal** — optional; polls a router admin API for radio metrics (RSRP/RSRQ/SINR/band).
5. **HTTP latency** — timed small (10 KB) fetch from a fast CDN. Every 5 min.
6. **HTTP throughput** — timed 1 MB fetch, ~4x/day on a random schedule.
7. **Ookla speedtest** — manual only (~400 MB/run at 5G speeds).
8. **Push metrics** — Influx line protocol to Grafana Cloud Prometheus, gzipped, batched.
9. **Push logs** — structured JSON to Grafana Cloud Loki (fire-and-forget).
10. **On push failure** — failed metric batches are dropped (not buffered) so uptime stays truthful; failed log payloads append to the JSONL buffer on the data partition and flush on reconnect. Gaps ≥ 10 min also POST a sticky region annotation to Grafana.

Metric names are flattened: `towerwatch_{field}_{target_label}` (e.g. `towerwatch_rtt_avg_google`). Units are `_ms` throughout (not Prometheus-standard seconds).

---

## What it measures

| Metric | Method | Interval |
|---|---|---|
| RTT avg/min/max | ICMP ping, 10 probes per target | 60 s |
| Jitter | Std deviation of RTT (RFC 3550) | 60 s |
| Packet loss | ICMP loss % | 60 s |
| Connection state | Binary up/down with outage tracking | 60 s |
| DNS resolution time | `dnspython` with explicit nameservers | 60 s |
| TCP connection time | Socket connect to `8.8.8.8:443` | 60 s |
| HTTP latency | Timed 10 KB fetch | 5 min |
| HTTP throughput sample | Timed 1 MB fetch | ~4x/day (random) |
| Download/upload speed | Ookla CLI | manual |
| Router signal (optional) | RSRP, RSRQ, SINR, band via router admin API | 60 s |

---

## Repository layout

```
towerwatch/
├── src/towerwatch/         # The package — installed via `pip install .`
│   ├── main.py             # compose_root() + main() — [project.scripts] entry
│   ├── app.py              # run_loop(ctx, state) — the 60 s main loop
│   ├── config.py           # All tunable constants (intervals, targets, URLs)
│   ├── credentials.py.example  # Credential template — copy to credentials.py
│   ├── clients/            # GrafanaClient, LokiClient (outbound HTTP)
│   └── probes/             # Per-probe modules (ping, dns, tcp, http, m6, ookla)
├── tests/                  # pytest suite (hand-written fakes, no MagicMock)
├── pi/bench/               # Failure-mode test harness (imports installed pkg)
├── docs/                   # Architecture, runbook, probe guides
├── grafana/dashboard.json  # Importable Grafana Cloud dashboard
├── scripts/
│   ├── install-pi.sh       # One-time Pi setup (venv, systemd, data partition)
│   ├── deploy.sh           # Per-deploy: git pull, pip install, restart
│   └── towerwatch.service  # systemd unit
├── pyproject.toml          # Single source of truth (PEP 621)
├── ci.sh                   # Local CI: ruff + pyright + pytest + version stamp
├── cd.sh                   # Shim → scripts/deploy.sh
├── CLAUDE.md               # Agent-facing invariants
└── README.md
```

---

## Hardware

Any Raspberry Pi with wired Ethernet will work. This project was developed on a Pi 3B.

| Component | Notes |
|---|---|
| Raspberry Pi (3B or newer) | Wired Ethernet is strongly preferred over Wi-Fi for stable probes. |
| MicroSD card (16 GB+) | Any reputable brand; A-class rating helps but isn't required. |
| Power supply | Use the official PSU for your Pi model. Under-spec supplies cause random reboots. |
| Heatsink / case | Recommended if running 24/7 in a warm location. |
| Ethernet cable | Cat5e or better to the router under test. |

The probes don't care what's on the other end of the cable — 5G hotspot, fixed-wireless modem, fibre ONT, or LAN uplink. The optional cellular signal probe (`src/towerwatch/probes/m6.py`) disables itself cleanly if the router isn't reachable — see [`docs/probe-m6.md`](docs/probe-m6.md).

---

## Quick start

### 1. Flash the SD card

- Flash **Raspberry Pi OS Lite (64-bit)** with Raspberry Pi Imager.
- In Imager's advanced settings: enable SSH, set a hostname, set a user/password.
- After flashing, create a **third partition** (~1 GB, ext4, label `twdata`) on the card for persistent buffer storage. `install.sh` expects this at `/dev/mmcblk0p3`.

### 2. First boot

```bash
ssh <user>@<hostname>.local
sudo apt update && sudo apt upgrade -y
```

### 3. Install

```bash
git clone <your-fork-url> towerwatch
cd towerwatch
cp src/towerwatch/credentials.py.example src/towerwatch/credentials.py
# Edit credentials.py — see "Configuration" below
sudo bash scripts/install-pi.sh
```

`scripts/install-pi.sh` installs system deps, the Ookla Speedtest CLI, creates `/opt/towerwatch/.venv`, `pip install`s the package into it, mounts the data partition at `/opt/towerwatch/data`, installs the systemd unit (pointing at `.venv/bin/towerwatch`), and enables the service.

### 4. Verify

```bash
sudo systemctl status towerwatch
journalctl -u towerwatch -f
```

In Grafana Cloud:
- Metrics: Explore → your Prometheus datasource → query `towerwatch_connected`.
- Logs: Explore → your Loki datasource → `{job="towerwatch"} | json | event="service_started"`.

### Local dev (Windows/macOS/Linux)

Cross-platform — see [`CLAUDE.md`](CLAUDE.md) §Windows dev mechanics for path/flag differences.

```bash
# From repo root; uv is optional but recommended.
uv venv && uv pip install -e ".[dev]"
cp src/towerwatch/credentials.py.example src/towerwatch/credentials.py
# ...edit credentials.py...
python -m towerwatch       # or: .venv/bin/towerwatch
```

Optional: drop the Ookla CLI binary in `pi/speedtest_bin/` for manual speedtests. The cellular signal probe fails gracefully off-network.

---

## Configuration

All tuning lives in two files:

- **`src/towerwatch/config.py`** — non-secret constants: probe targets, intervals, push URLs, buffer paths, log event names. Read the top of the file; it's the source of truth.
- **`src/towerwatch/credentials.py`** — gitignored. Created from `credentials.py.example`. Contains:
  - Grafana Cloud Prometheus creds (`GRAFANA_INSTANCE_ID`, `GRAFANA_API_KEY`)
  - Loki creds (`LOKI_URL`, `LOKI_USER`, `LOKI_TOKEN`) — Loki has a **different** instance ID from Prometheus
  - Optional: router admin password, Grafana annotation service-account token

To generate Grafana Cloud credentials: log in to grafana.com → your stack → **Access Policies** → create a token with the `MetricsPublisher` role (also usable for Loki writes in the same stack).

To enable sticky outage annotations: create a service account in your Grafana stack with the `annotations:write` permission, mint a token, and paste it into `GRAFANA_ANNOTATION_TOKEN`. Also set `GRAFANA_ANNOTATIONS_URL` in `config.py` to `https://<your-stack>.grafana.net/api/annotations` (the user-facing stack URL, **not** the `prometheus-prod-*` push endpoint).

> Agents: see [`CLAUDE.md`](CLAUDE.md) for metric-naming and push-level invariants that must not be "cleaned up."

---

## Deploy

Run `./ci.sh full && ./scripts/deploy.sh <user@host>` from your dev machine. `ci.sh` stamps `src/towerwatch/_version.txt` (after ruff, pyright, pytest); `scripts/deploy.sh` SSHes in, `git pull`s, `pip install`s into the Pi's venv, and restarts the service. `./cd.sh` still works as a shim.

For dashboard updates: re-import `grafana/dashboard.json` in Grafana Cloud (Dashboards → New → Import → Upload JSON).

See [`docs/runbook.md#remote-deploy`](docs/runbook.md#remote-deploy) for post-deploy verification, failure modes, and manual fallback.

---

## Grafana dashboard & alerting

`grafana/dashboard.json` is an importable dashboard with panels for connection uptime, per-target RTT/jitter/loss (log2 scale, small multiples), DNS and TCP timings, HTTP download time, Ookla speedtest, router signal, and a live Loki event-log stream.

**Suggested alert:** a "no data" rule on `towerwatch_connected` — if no samples for 2+ hours, notify. This catches the case where the device itself has gone silent (power, SD-card failure, ISP outage exceeding the buffer).

---

## Data budget

If your connection is metered, treat this as a hard constraint. At defaults the probes use roughly **230 MB/month** (batched + gzipped pushes dominate). Anything that increases traffic — new probes, larger download samples, higher frequencies, smaller batches — should be evaluated against your cap. Ookla is manual-only for this reason (~400 MB per 5G run). To run manually, call `run_speedtest()` directly from a REPL or one-off script — it is not scheduled in the main loop.

---

## For AI assistants

If you're an AI agent working in this repo, read **[`CLAUDE.md`](CLAUDE.md)** first. It contains the authoritative working instructions: delegation patterns, data-budget guardrails, deployment conventions, and the metric-naming invariants that must not be "cleaned up." This README is for humans onboarding to the project; `CLAUDE.md` is for you.
