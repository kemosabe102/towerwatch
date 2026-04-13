# Towerwatch — 5G Cell Tower Network Quality Monitor

Continuously monitors latency, jitter, packet loss, DNS resolution, TCP connection time, throughput, and cellular signal quality on your 5G connection to build an evidence dataset for your cellular provider.

**Platform:** Raspberry Pi 3B (or newer) with wired Ethernet to a Netgear Nighthawk M6 5G hotspot.

---

## What It Measures

| Metric | Method | Interval |
|--------|--------|----------|
| RTT avg/min/max | ICMP ping (10 probes) to Google, Cloudflare, carrier gateway | 60s |
| Jitter | Std deviation of RTT (RFC 3550) | 60s |
| Packet loss | ICMP loss percentage | 60s |
| Connection state | Binary up/down with outage tracking | 60s |
| DNS resolution time | dnspython with explicit nameserver (bypasses cache) | 60s |
| TCP connection time | Socket connect to 8.8.8.8:443 | 60s |
| M6 signal quality | RSRP, RSRQ, SINR, band from M6 admin API | 60s |
| HTTP latency probe | Timed 10KB fetch from Cloudflare CDN | 5 min |
| HTTP throughput sample | Timed 1MB download from Cloudflare CDN | ~4x/day (random) |
| Download/upload speed | Ookla official CLI (manual only via SSH) | on demand |

All metrics push to Grafana Cloud over HTTPS using Influx line protocol. During outages, data buffers to a local CSV and flushes when connectivity returns.

---

## Hardware

| Component | Notes |
|-----------|-------|
| Raspberry Pi 3B | Built-in 10/100 Ethernet, 1GB RAM |
| MicroSD card (32GB) | Samsung or SanDisk recommended for reliability |
| Heatsink kit | Passive — prevents thermal throttling |
| 5V/2.5A micro-USB power supply | Must be 2.5A+; underpowered supplies cause random reboots |
| Ethernet cable | Cat5e/Cat6, connects Pi to M6 router |
| Case (optional) | Dust/short protection at remote site |

### Router: Netgear Nighthawk M6

Enable the Ethernet port before deploying:
1. Connect to M6 WiFi → go to `192.168.1.1`
2. Advanced Settings → enable Ethernet port
3. Enable Plugged-In Mode (USB-C power, runs off outlet)

---

## Quick Start

### 1. Flash SD Card

- Download [Raspberry Pi OS Lite](https://www.raspberrypi.com/software/) (64-bit, no desktop)
- Flash with Raspberry Pi Imager
- In Imager settings: enable SSH, set hostname to `towerwatch`, set password
- After flashing, create a third partition (1GB, ext4) for persistent data storage

### 2. First Boot

```bash
ssh pi@towerwatch.local
sudo apt update && sudo apt upgrade -y
```

### 3. Install Towerwatch

```bash
git clone git@github.com:kemosabe102/towerwatch.git
cd towerwatch/pi
cp secrets.py.example secrets.py
# Edit secrets.py with your Grafana Cloud credentials
sudo bash install.sh
```

### 4. Install Tailscale (remote access)

Tailscale creates a private VPN mesh so you can SSH into the Pi from anywhere — no port forwarding needed. The free Personal plan (3 users, 100 devices) is sufficient.

```bash
# Install on Pi (sets up apt repo for auto-updates)
curl -fsSL https://tailscale.com/install.sh | sh

# Set up bind mount so Tailscale state survives overlayfs
# (install.sh creates the directory; the bind mount unit must be added manually)
sudo systemctl enable var-lib-tailscale.mount
sudo systemctl start var-lib-tailscale.mount

# Authenticate — opens a URL to log in
sudo tailscale up
```

Install Tailscale on your local machine too (Windows: `winget install Tailscale.Tailscale`). Log in with the same account. Both devices get stable 100.x.y.z IPs for SSH access from any network.

**Note:** Tailscale IPs are device-specific and not committed to this repo.

### 5. Configure Read-Only Filesystem

**Do NOT use `raspi-config` Overlay File System** — there is a confirmed bug in Bookworm that overlays all partitions including the data partition, making it non-persistent.

Instead, manually configure:
```bash
# Create the config file
echo 'overlayroot=tmpfs:recurse=0' | sudo tee /etc/overlayroot.local.conf
```

The `recurse=0` flag prevents the overlay from applying to the data partition.

**Before enabling overlayfs**, verify that `install.sh` has already:
- Bind-mounted `/var/lib/tailscale/` → `/opt/towerwatch/data/tailscale-state/` (via systemd mount unit)
- Configured fakehwclock to write to the data partition

### 6. Reboot and Verify

```bash
sudo reboot
# After reboot:
sudo systemctl status towerwatch
journalctl -u towerwatch -f
```

---

## Local Testing (Windows)

The monitoring script is cross-platform. Test the full push pipeline from your dev machine before deploying to the Pi:

```bash
cd pi
cp secrets.py.example secrets.py
# Edit secrets.py with your Grafana Cloud + Loki credentials
pip install requests dnspython
python towerwatch.py
```

Ookla speedtest is disabled from the automatic schedule (each test uses ~400 MB at 5G speeds). Run manually via SSH when needed. Download the [Ookla speedtest CLI](https://www.speedtest.net/apps/cli) and extract to `pi/speedtest_bin/`.

Platform differences (ping flags, paths, data partition) are handled automatically. M6 signal polling will fail gracefully (expected — no M6 router on your home network).

**Verify in Grafana Cloud:**
- Metrics: Explore → `grafanacloud-towerwatch-prom` → query `towerwatch_connected`
- Logs: Explore → `grafanacloud-towerwatch-logs` → query `{job="towerwatch"} | json | event="service_started"`

---

## Deploying Updates to the Pi

The Pi runs a read-only overlay filesystem (overlayroot), so the root partition resets on reboot. A deploy script handles the full process — entering the writable chroot, pulling from git, copying files, and restarting the service.

### Quick Deploy

From your dev machine (Windows or any machine with SSH access):

```bash
bash deploy.sh admin@100.76.154.81    # Generic script — host is required
bash deploy.sh admin@towerwatch.local # Or use mDNS on LAN
bash deploy-local.sh                  # Local wrapper with your default host (not committed)
```

The script:
1. SSHes into the Pi
2. Runs `git pull --ff-only` in `~/towerwatch`
3. Copies `towerwatch.py` and `config.py` to `/opt/towerwatch/`
4. Restarts the systemd service and verifies it's running

### Manual Deploy

If you prefer to do it by hand:

```bash
ssh admin@100.76.154.81
cd ~/towerwatch && git pull --ff-only
sudo cp pi/towerwatch.py pi/config.py /opt/towerwatch/
sudo chown towerwatch:towerwatch /opt/towerwatch/towerwatch.py /opt/towerwatch/config.py
sudo systemctl restart towerwatch
journalctl -u towerwatch -f
```

### Dashboard Updates

Dashboard changes don't require Pi access — re-import `grafana/dashboard.json` in Grafana Cloud directly (Dashboards → New → Import → Upload JSON).

---

## Grafana Dashboard

A pre-built dashboard is included at `grafana/dashboard.json` with 14 panels:

- **Connection Uptime** — headline evidence number
- **Current Status** — live UP/DOWN indicator
- **Speedtest Health** — OK/FAILING indicator
- **Google / Cloudflare / Gateway** — per-endpoint RTT avg+max and jitter (small multiples, log2 scale)
- **Packet Loss** — per target with threshold shading
- **DNS Resolution Time** — per nameserver
- **TCP Connection Time** — real-world app readiness
- **HTTP Download Time** — lightweight throughput proxy (every 5 min)
- **Download/Upload Speed** — Ookla speedtest (every 6 hours)
- **M6 Signal Quality** — RSRP, RSRQ, SINR from the router
- **Towerwatch Event Log** — live Loki log stream

Import: Grafana Cloud → Dashboards → New → Import → Upload JSON → select datasource.

### Alerting

Set up a "no data" alert in Grafana Cloud: if no `towerwatch_connected` data for 2+ hours, send a notification. Critical for knowing if the remote device has gone silent.

---

## Pre-Deployment Checklist

Complete at home before going to the remote site:

- [ ] Pi boots and reaches `towerwatch.local` via SSH
- [ ] `sudo systemctl status towerwatch` shows active
- [ ] `journalctl -u towerwatch -f` shows metric cycles every 60s
- [ ] Grafana Cloud Explore shows towerwatch metrics
- [ ] Speedtest runs successfully (check journalctl for "Speedtest:" log)
- [ ] Tailscale connected: `tailscale status` shows online
- [ ] SSH works over Tailscale from another device
- [ ] Pull power → Pi reboots cleanly → towerwatch restarts → buffer data survives
- [ ] Tailscale reconnects automatically after reboot (no re-auth needed)
- [ ] Leave running 24+ hours — check Grafana for continuous data, no gaps

---

## Secrets and Credentials

`secrets.py` is gitignored and must be created manually on each device:

```bash
cd towerwatch/pi
cp secrets.py.example secrets.py
chmod 600 secrets.py
# Edit with your Grafana Cloud instance ID and API key
```

To generate Grafana credentials:
1. Log in to [grafana.com](https://grafana.com) → your stack → Access Policies
2. Create a key with **MetricsPublisher** role
3. Your instance ID is `3009582`

---

## File Structure

```
towerwatch/
├── pi/                      # Raspberry Pi implementation
│   ├── towerwatch.py        # Main monitoring script
│   ├── config.py            # All configurable constants
│   ├── secrets.py.example   # Credential template
│   ├── requirements.txt     # Python dependencies
│   ├── install.sh           # One-shot setup script
│   └── towerwatch.service   # systemd unit file
├── deploy.sh                # Generic deploy script (host required as arg)
├── deploy-local.sh          # Local wrapper with default host (gitignored)
├── grafana/
│   └── dashboard.json       # Grafana dashboard (14 panels)
├── arduino/                 # Archived: original Arduino Uno implementation
│   ├── towerwatch.ino
│   ├── config.h
│   └── ...
└── README.md
```

---

## Arduino (Archived)

The original implementation targeted an Arduino Uno R3 + Ethernet Shield. It is preserved in the `arduino/` directory for reference. The Arduino version collects RTT, jitter, and packet loss via TCP probes but cannot push to Grafana Cloud due to the Uno's lack of TLS support. See `arduino/config.h` for its configuration.
