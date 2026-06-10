# Towerwatch — Cellular/Broadband Network Quality Monitor

A continuous network-quality probe for a Raspberry Pi. Ships latency, jitter, packet loss, DNS/TCP/HTTP timings, throughput, and (optionally) cellular-radio signal metrics to Grafana Cloud. Buffers **logs** to disk during outages and flushes on reconnect; metrics are dropped on push failure so uptime can't be backfilled. Designed to build a long-running evidence dataset for your ISP or cellular provider.

> **At a glance**
> - **Runtime:** Python 3 on Raspberry Pi OS (also runs on Windows/macOS for dev).
> - **Outputs:** Prometheus metrics (Influx line protocol) + structured JSON logs (Loki), both to Grafana Cloud over HTTPS.
> - **Cadence:** 60 s main loop. Pushes batched every ~2 min.
> - **Offline behaviour:** atomic JSONL **log** buffer on the data partition, capped at 256 KB, flushed on reconnect; metrics are not buffered.
> - **Data cost:** ~230 MB/month for the always-on probes, plus the Cloudflare adaptive throughput probe at a per-site cadence (default 2/day, override via `credentials.py`). Typical sites: 1×/day on a home gigabit link, 3×/day on cellular with morning/midday/evening time-windowed sampling. Per-site allotment is 30 GB/month; the dashboard's "Speedtest Data (7d)" stat tracks actual usage.

---

## Further reading

| Document | Purpose |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Design narrative — composition root, Protocols, TickContext, testing philosophy |
| [`docs/runbook.md`](docs/runbook.md) | Symptom-indexed ops runbook — start here at 2am |
| [`docs/setup-pi.md`](docs/setup-pi.md) | Tailscale + read-only root hardening for unattended deployments |
| [`docs/manual-speedtest.md`](docs/manual-speedtest.md) | Non-technical user guide for triggering speedtests over SSH |
| [`docs/probe-m6.md`](docs/probe-m6.md) | Optional Netgear M6 cellular signal probe — setup or port to another router |
| [`docs/design.md`](docs/design.md) | Per-component code reference (functions, config tables, error patterns) |
| [`docs/bench-tests.md`](docs/bench-tests.md) | Failure-mode bench test catalog |
| [`docs/dashboard-sync.md`](docs/dashboard-sync.md) | Auto-sync `grafana/*.json` to Grafana Cloud from CI — token setup |
| [`docs/phone-compare.md`](docs/phone-compare.md) | Phone-vs-hotspot cellular comparison over ADB — congestion/deprioritization test |
| [`docs/phone-rf-logger.md`](docs/phone-rf-logger.md) | Android RF logger companion app (separate repo) — per-cell RSRP/NR/neighbors/CA |
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
7. **Cloudflare adaptive speedtest** — multi-stream against `speed.cloudflare.com`. Scheduled cadence is per-site (default 2/day, override via `CLOUDFLARE_THROUGHPUT_TESTS_PER_DAY_OVERRIDE`; supports named time windows like morning/midday/evening via `CLOUDFLARE_THROUGHPUT_WINDOWS_OVERRIDE`). Manual SSH-triggered runs (`towerwatch-speedtest`) share the same probe. Up to ~550 MB per run; the adaptive ramp sizes transfers to measured speed so slow links naturally use less.
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
| Download/upload speed | Cloudflare adaptive multi-stream | 2× day + manual |
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
│   ├── partition-pi-data.sh # One-time: creates /dev/mmcblk0p3 ext4 `twdata`
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
- In Imager's advanced settings:
  - Set a hostname (e.g. `towerwatch-<site>`).
  - Set the username (we use `admin` throughout this repo).
  - Enable SSH → **"Allow public-key authentication only"** → paste your `~/.ssh/id_ed25519.pub`. Don't use password auth — see [SSH access](#ssh-access) below.
- **Disable rootfs auto-expansion** before first boot. Pi OS Lite expands `rootfs` to fill the entire SD card on first boot, leaving no room for the `twdata` data partition. Edit `cmdline.txt` on the boot partition (FAT32, mounts on Mac/Windows/Linux automatically) and remove the bare `resize` token. The current Imager (1.8+) puts a `resize` flag in `cmdline.txt` that the initramfs firstboot hook reads to trigger expansion:

  ```bash
  # macOS — after Imager finishes, re-insert the SD card so bootfs remounts:
  sed -i '' 's| resize||' /Volumes/bootfs/cmdline.txt
  cat /Volumes/bootfs/cmdline.txt   # verify `resize` is gone, file is still ONE line

  # Linux — typical mount path:
  sudo sed -i 's| resize||' /media/$USER/bootfs/cmdline.txt

  # Windows — open bootfs in Explorer, edit cmdline.txt in Notepad++ or VS Code
  # (NOT regular Notepad — it adds a BOM that breaks boot). Remove the ` resize` token.
  ```

  Leave the `ds=nocloud;i=rpi-imager-...` token alone — that's cloud-init applying your hostname, user, and SSH key on first boot. `cmdline.txt` must remain a single line with no trailing newline. Eject properly before inserting into the Pi.

  > **Note on token name:** older Pi OS versions used `init=/usr/lib/raspberrypi-sys-mods/firstboot` instead. If you don't see `resize` in your `cmdline.txt`, look for that legacy token and remove it instead. Either way, `cat` your `cmdline.txt` first to see what's actually there.

- The `twdata` data partition is created on the Pi after first boot via `scripts/partition-pi-data.sh` (step 3 below).

### 2. First boot

```bash
ssh admin@<hostname>.local        # no password — your key from Imager works
sudo apt update && sudo apt upgrade -y
```

### 3. Install

```bash
git clone <your-fork-url> towerwatch
cd towerwatch
cp src/towerwatch/credentials.py.example src/towerwatch/credentials.py
# Edit credentials.py — see "Configuration" below
sudo bash scripts/partition-pi-data.sh   # creates /dev/mmcblk0p3 (twdata)
sudo bash scripts/install-pi.sh
```

`partition-pi-data.sh` is idempotent — re-running it on a Pi that already has a labelled `twdata` partition is a no-op.

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

### SSH access

Use built-in OpenSSH on every dev OS. No third-party clients needed.

| Dev OS | Client | Notes |
|---|---|---|
| Windows 10/11 | `C:\Windows\System32\OpenSSH\ssh.exe` (built in; also what Git Bash uses) | Enable the "OpenSSH Authentication Agent" service for ssh-agent. |
| macOS | `/usr/bin/ssh` (built in) | Add `UseKeychain yes` + `AddKeysToAgent yes` to `~/.ssh/config` to store passphrases. |
| Linux | `/usr/bin/ssh` (built in) | Standard ssh-agent. |

**Key-based auth only.** All accounts on the Pi (`admin`, plus the speedtest `towerwatch-user` once you've handed off a key — see [`docs/setup-pi.md`](docs/setup-pi.md)) are configured keys-only. Bootstrap by pasting your public key into Raspberry Pi Imager's advanced settings before flashing — the Pi boots with your key already in `authorized_keys` and you never need a password.

If you don't have a keypair yet:

```bash
ssh-keygen -t ed25519 -C "you@dev-machine"
cat ~/.ssh/id_ed25519.pub   # paste this into Imager
```

**Don't use `sshpass`.** It leaks credentials via `ps`, has been removed from Homebrew, and fails compliance checks. The cost of pasting a pubkey into Imager once is lower than installing `sshpass` everywhere forever.

Convenient `~/.ssh/config` entry once a Pi is up:

```
Host towerwatch-<site>
    HostName <site>.local           # or the Tailscale IP after install
    User admin
    IdentityFile ~/.ssh/id_ed25519
    AddKeysToAgent yes
    UseKeychain yes                  # macOS only
```

Then `ssh towerwatch-<site>` from anywhere.

---

## Configuration

All tuning lives in two files:

- **`src/towerwatch/config.py`** — non-secret constants: probe targets, intervals, push URLs, buffer paths, log event names. Read the top of the file; it's the source of truth.
- **`src/towerwatch/credentials.py`** — gitignored. Created from `credentials.py.example`. Contains:
  - `LOCATION` — per-site identifier (e.g. `"home"`, `"remote-site-1"`); becomes the `host` metric tag and Loki stream label. Each Pi gets its own credentials.py with its own `LOCATION`.
  - Grafana Cloud Prometheus creds (`GRAFANA_INSTANCE_ID`, `GRAFANA_API_KEY`)
  - Loki creds (`LOKI_URL`, `LOKI_USER`, `LOKI_TOKEN`) — Loki has a **different** instance ID from Prometheus
  - Optional: router admin password, Grafana annotation service-account token

To generate Grafana Cloud credentials: log in to grafana.com → your stack → **Access Policies** → create a token with the `MetricsPublisher` role (also usable for Loki writes in the same stack).

To enable sticky outage annotations: create a service account in your Grafana stack with the `annotations:write` permission, mint a token, and paste it into `GRAFANA_ANNOTATION_TOKEN`. Also set `GRAFANA_ANNOTATIONS_URL` in `config.py` to `https://<your-stack>.grafana.net/api/annotations` (the user-facing stack URL, **not** the `prometheus-prod-*` push endpoint).

> Agents: see [`CLAUDE.md`](CLAUDE.md) for metric-naming and push-level invariants that must not be "cleaned up."

---

## Deploy

Run `./ci.sh full && ./scripts/deploy.sh <user@host>` from your dev machine. `ci.sh` stamps `src/towerwatch/_version.txt` (after ruff, pyright, pytest); `scripts/deploy.sh` SSHes in, `git pull`s, `pip install`s into the Pi's venv, and restarts the service. `./cd.sh` still works as a shim.

Dashboard updates sync automatically: pushing a change to `grafana/*.json` on `main` triggers the **Sync Dashboards** workflow, which overwrites the live dashboards in place by `uid`. See [`docs/dashboard-sync.md`](docs/dashboard-sync.md) for the one-time token setup. (Manual fallback: re-import the JSON in Grafana Cloud — Dashboards → New → Import → Upload JSON.)

See [`docs/runbook.md#remote-deploy`](docs/runbook.md#remote-deploy) for post-deploy verification, failure modes, and manual fallback.

---

## Grafana dashboard & alerting

`grafana/dashboard.json` is an importable dashboard with panels for connection uptime, per-target RTT/jitter/loss (log2 scale, small multiples), DNS and TCP timings, HTTP download time, Ookla speedtest, router signal, and a live Loki event-log stream.

**Suggested alert:** a "no data" rule on `towerwatch_connected` — if no samples for 2+ hours, notify. This catches the case where the device itself has gone silent (power, SD-card failure, ISP outage exceeding the buffer).

---

## Running multiple sites

To compare two or more locations (e.g. home + remote site), deploy a separate Pi at each location with a distinct `LOCATION` value in its `credentials.py`. The dashboards support this out of the box:

- **`grafana/dashboard.json`** — the main dashboard has a `$location` dropdown. Pick a site; all panels scope to it.
- **`grafana/dashboard-compare.json`** — dedicated side-by-side dashboard with `$location_a` / `$location_b` selectors. Import it separately.

To trigger a manual speedtest on a remote Pi from anywhere on your Tailnet:

```bash
ssh admin@<pi-tailscale-ip> towerwatch-speedtest --triggered-by <your-name>
```

Results (download/upload Mbps, tagged with the operator name) appear on the dashboard within a minute. Non-technical users: see [`docs/manual-speedtest.md`](docs/manual-speedtest.md).

**Scaling:** Tailscale's free plan fits small teams comfortably. For dozens or hundreds of users (enterprise deployments), options include Tailscale ACLs with a paid plan, a future HTTP trigger endpoint behind SSO, or a dedicated jump host — out of scope for this repo today.

## Data budget

If your connection is metered, treat this as a hard constraint. **Per-site allotment is 30 GB/month.** The always-on probes (ping, DNS, TCP, gateway, M6, HTTP latency, metric pushes) use roughly **230 MB/month** at defaults. The Cloudflare adaptive throughput probe is the dominant variable cost; the dashboard's "Speedtest Data (7d)" stat surfaces actual usage so you don't have to guess.

The primary lever is the **per-site cadence** in `credentials.<site>.py`:

```python
# Home — gigabit link, daily sanity check is enough
CLOUDFLARE_THROUGHPUT_TESTS_PER_DAY_OVERRIDE = 1

# Cellular site — diurnal patterns matter, sample 3× day in named windows
CLOUDFLARE_THROUGHPUT_TESTS_PER_DAY_OVERRIDE = 3
CLOUDFLARE_THROUGHPUT_WINDOWS_OVERRIDE = [(6, 10), (11, 14), (17, 21)]
```

Each window is a `(start_hour, end_hour)` pair in 24-hour local time. The scheduler picks one random time within each window per day. Length must equal `TESTS_PER_DAY`.

Cloudflare's adaptive ramp sizes individual transfers to the measured speed, so byte-cap tuning is rarely needed. The escape hatches remain:

```python
CLOUDFLARE_THROUGHPUT_MAX_TOTAL_BYTES_OVERRIDE = 50_000_000   # 50 MB/run download
CLOUDFLARE_UPLOAD_MAX_TOTAL_BYTES_OVERRIDE = 25_000_000       # 25 MB/run upload
```

Smaller caps trade accuracy for data — at 50 MB/run on a 300 Mbps link, slow-start eats most of the transfer and the reading will under-report by 30–50%. Use these only if the cadence override alone isn't enough.

---

## For AI assistants

If you're an AI agent working in this repo, read **[`CLAUDE.md`](CLAUDE.md)** first. It contains the authoritative working instructions: delegation patterns, data-budget guardrails, deployment conventions, and the metric-naming invariants that must not be "cleaned up." This README is for humans onboarding to the project; `CLAUDE.md` is for you.
