# Towerwatch — Cellular/Broadband Network Quality Monitor

A continuous network-quality probe for a Raspberry Pi. Ships latency, jitter, packet loss, DNS/TCP/HTTP timings, throughput, and (optionally) cellular-radio signal metrics to Grafana Cloud. Buffers to disk during outages and flushes on reconnect. Designed to build a long-running evidence dataset for your ISP or cellular provider.

> **At a glance**
> - **Runtime:** Python 3 on Raspberry Pi OS (also runs on Windows/macOS for dev).
> - **Outputs:** Prometheus metrics (Influx line protocol) + structured JSON logs (Loki), both to Grafana Cloud over HTTPS.
> - **Cadence:** 60 s main loop. Pushes batched every ~2 min.
> - **Offline behaviour:** atomic CSV buffer on a dedicated data partition, capped at 512 KB, flushed on reconnect.
> - **Data cost:** ~230 MB/month at defaults — tune `config.py` if you're on a metered connection.

---

## Contents

1. [Architecture](#architecture)
2. [What it measures](#what-it-measures)
3. [Repository layout](#repository-layout)
4. [Hardware](#hardware)
5. [Quick start](#quick-start)
6. [Configuration](#configuration)
7. [Remote access (Tailscale)](#remote-access-tailscale)
8. [Read-only root filesystem](#read-only-root-filesystem)
9. [Deploying updates](#deploying-updates)
10. [Grafana dashboard & alerting](#grafana-dashboard--alerting)
11. [Data budget](#data-budget)
12. [Optional: cellular-router signal probe](#optional-cellular-router-signal-probe)
13. [For AI assistants](#for-ai-assistants)

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
10. **Buffer on failure** — atomic CSV write to the data partition; flush on reconnect. Gaps ≥ 10 min also POST a sticky region annotation to Grafana.

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
├── pi/
│   ├── towerwatch.py       # Main monitoring loop
│   ├── config.py           # All tunable constants (intervals, targets, URLs)
│   ├── secrets.py.example  # Credential template — copy to secrets.py
│   ├── requirements.txt    # Python dependencies
│   ├── install.sh          # One-shot Pi setup (systemd, data partition, deps)
│   ├── towerwatch.service  # systemd unit
│   └── probes/             # Per-probe modules (ping, dns, tcp, http, m6, ookla)
├── deploy.sh               # Generic deploy: ssh HOST, git pull, copy, restart
├── deploy-local.sh         # Thin wrapper with a default host (gitignored)
├── grafana/
│   └── dashboard.json      # Importable Grafana Cloud dashboard
├── CLAUDE.md               # Instructions for AI assistants working in this repo
└── README.md
```

Agents editing this repo: start at `pi/config.py` (constants), then `pi/towerwatch.py` (loop), then `pi/probes/` (individual collectors).

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

The probes don't care what's on the other end of the cable — 5G hotspot, fixed-wireless modem, fibre ONT, or LAN uplink. The cellular-specific signal probe (`pi/probes/m6.py`) is optional and disables itself cleanly if the router isn't reachable.

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
cd towerwatch/pi
cp secrets.py.example secrets.py
# Edit secrets.py — see "Configuration" below
sudo bash install.sh
```

`install.sh` installs Python deps, the Ookla Speedtest CLI, mounts the data partition at `/opt/towerwatch/data`, installs the systemd unit, and starts the service.

### 4. Verify

```bash
sudo systemctl status towerwatch
journalctl -u towerwatch -f
```

In Grafana Cloud:
- Metrics: Explore → your Prometheus datasource → query `towerwatch_connected`.
- Logs: Explore → your Loki datasource → `{job="towerwatch"} | json | event="service_started"`.

### Local dev (Windows/macOS/Linux)

The script is cross-platform. Paths, ping flags, and the data partition check are gated on `sys.platform`.

```bash
cd pi
cp secrets.py.example secrets.py
pip install -r requirements.txt
python towerwatch.py
```

Optional: drop the Ookla CLI binary in `pi/speedtest_bin/` for manual speedtests. The cellular signal probe fails gracefully off-network.

---

## Configuration

All tuning lives in two files:

- **`pi/config.py`** — non-secret constants: probe targets, intervals, push URLs, buffer paths, log event names. Read the top of the file; it's the source of truth.
- **`pi/secrets.py`** — gitignored. Created from `secrets.py.example`. Contains:
  - Grafana Cloud Prometheus creds (`GRAFANA_INSTANCE_ID`, `GRAFANA_API_KEY`)
  - Loki creds (`LOKI_URL`, `LOKI_USER`, `LOKI_TOKEN`) — Loki has a **different** instance ID from Prometheus
  - Optional: router admin password, Grafana annotation service-account token

To generate Grafana Cloud credentials: log in to grafana.com → your stack → **Access Policies** → create a token with the `MetricsPublisher` role (also usable for Loki writes in the same stack).

To enable sticky outage annotations: create a service account in your Grafana stack with the `annotations:write` permission, mint a token, and paste it into `GRAFANA_ANNOTATION_TOKEN`. Also set `GRAFANA_ANNOTATIONS_URL` in `config.py` to `https://<your-stack>.grafana.net/api/annotations` (the user-facing stack URL, **not** the `prometheus-prod-*` push endpoint).

### Invariants worth knowing

- Metric units are `_ms`, not seconds. Don't "fix" this — dashboards depend on it.
- Target labels are baked into field names (e.g. `rtt_avg_google`), not Prometheus label selectors. This is deliberate; dashboards query by metric name.
- The buffer is capped at 512 KB (`BUFFER_MAX_BYTES`) to avoid filling the 1 GB data partition.
- `LOKI_PUSH_LEVEL` defaults to `WARN` in production. `INFO` is for local dev only; `INFO` in production will flood Loki.

---

## Remote access (Tailscale)

Optional. Tailscale gives the Pi a stable private IP reachable from anywhere, without port forwarding. The free Personal plan is enough.

```bash
# On the Pi
curl -fsSL https://tailscale.com/install.sh | sh

# So Tailscale state survives an overlayfs root (see next section)
sudo systemctl enable --now var-lib-tailscale.mount

sudo tailscale up   # opens an auth URL
```

Install Tailscale on your dev machine too, log in with the same account, and `ssh <user>@<tailscale-ip>` from anywhere.

---

## Read-only root filesystem

Recommended for unattended remote deployments — the root partition resets on every reboot, so a stray write or SD-card glitch can't corrupt the system. The data partition stays writable so the buffer and Tailscale state persist.

> **Do not use `raspi-config` → Overlay File System on Bookworm.** There is a confirmed bug that overlays *all* partitions including the data partition, making it non-persistent. Configure manually instead:

```bash
echo 'overlayroot=tmpfs:recurse=0' | sudo tee /etc/overlayroot.local.conf
sudo reboot
```

`recurse=0` is the critical flag — without it the data partition gets overlaid too.

Before enabling overlayroot, confirm `install.sh` has already:
- Bind-mounted `/var/lib/tailscale/` → `/opt/towerwatch/data/tailscale-state/`
- Configured `fake-hwclock` to write to the data partition

---

## Deploying updates

From any machine with SSH to the Pi:

```bash
bash deploy.sh <user>@<host-or-tailscale-ip>
# e.g. bash deploy.sh pi@towerwatch.local
```

The script SSHes in, `git pull --ff-only`s the repo, copies `pi/*.py` to `/opt/towerwatch/`, and restarts the systemd unit.

`deploy-local.sh` is a gitignored wrapper that hardcodes your host.

### Manual deploy

```bash
ssh <user>@<host>
cd ~/towerwatch && git pull --ff-only
sudo cp pi/towerwatch.py pi/config.py pi/probes/*.py /opt/towerwatch/
sudo systemctl restart towerwatch
journalctl -u towerwatch -f
```

### Dashboard updates

Re-import `grafana/dashboard.json` in Grafana Cloud (Dashboards → New → Import → Upload JSON). No Pi access required.

---

## Grafana dashboard & alerting

`grafana/dashboard.json` is an importable dashboard with panels for connection uptime, per-target RTT/jitter/loss (log2 scale, small multiples), DNS and TCP timings, HTTP download time, Ookla speedtest, router signal, and a live Loki event-log stream.

**Suggested alert:** a "no data" rule on `towerwatch_connected` — if no samples for 2+ hours, notify. This catches the case where the device itself has gone silent (power, SD-card failure, ISP outage exceeding the buffer).

---

## Data budget

If your connection is metered, treat this as a hard constraint. At defaults the probes use roughly **230 MB/month** (batched + gzipped pushes dominate). Anything that increases traffic — new probes, larger download samples, higher frequencies, smaller batches — should be evaluated against your cap. Ookla is manual-only for this reason (~400 MB per 5G run).

---

## Optional: cellular-router signal probe

`pi/probes/m6.py` polls the admin API of a **Netgear Nighthawk M6** 5G/LTE hotspot for RSRP, RSRQ, SINR, and current band. If you have a different router, either:

- Disable the probe by clearing `M6_ADMIN_URL` in `config.py`, or
- Write a sibling module in `pi/probes/` that exposes the same metric shape.

For an M6 specifically: connect to its Wi-Fi, visit `http://192.168.1.1`, enable the Ethernet port and Plugged-In Mode in Advanced Settings, then set the admin password in `secrets.py`.

---

## For AI assistants

If you're an AI agent working in this repo, read **`CLAUDE.md`** first. It contains the authoritative working instructions: delegation patterns, data-budget guardrails, deployment conventions, and the metric-naming invariants that must not be "cleaned up." This README is for humans onboarding to the project; `CLAUDE.md` is for you.
