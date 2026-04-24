# Manual Speedtest — Guide for Remote Users

Running a speedtest on a Towerwatch Pi over SSH. Works on Windows, macOS, or Linux. No coding required.

## Who this is for

You've been asked to help verify a network's speed at a site where a Towerwatch Pi is already installed. The operator (the person who set up the Pi) will invite you to their Tailnet — a private network that lets you reach the Pi securely. You don't need anything the operator can't send you over chat.

## What you'll need from the operator

- A Tailscale invitation email.
- The Pi's Tailscale IP (looks like `100.x.y.z`).
- The SSH login name (usually `admin`).

## Step 1 — Install Tailscale

Download from <https://tailscale.com/download> for your OS. Sign in with the email address the operator invited. Once installed, you should see the Pi in your device list (if the operator has shared it with you).

## Step 2 — Run the speedtest

### Windows (easy path)

1. Download `run-speedtest.bat` from `docs/speedtest-tool/` in the repo (the operator can send it to you directly).
2. Double-click it.
3. Enter your name and the Pi's Tailscale IP when prompted.
4. Wait ~60 seconds. Results appear in the window.

### Any OS (one-liner)

Open a terminal (PowerShell on Windows, Terminal on macOS/Linux) and run:

```bash
ssh admin@<tailscale-ip> towerwatch-speedtest --triggered-by <your-name>
```

Example:

```bash
ssh admin@100.76.154.81 towerwatch-speedtest --triggered-by alice
```

If SSH prompts for a password, ask the operator.

## What you'll see

```
Running Ookla speedtest on 'remote-site' (triggered by 'alice').
This takes ~60s and uses ~400 MB of data.
Download: 123.4 Mbps
Upload:   45.6 Mbps
Location: remote-site
```

The operator will also see your result on their Grafana dashboard within a minute, tagged with your name — so they know who ran it and when.

## Troubleshooting

- **"Connection timed out"** — the Pi may be offline, or Tailscale hasn't finished connecting on your machine. Open the Tailscale app and check that both you and the Pi show "Connected."
- **"Permission denied (publickey)"** — the operator needs to add your SSH key or give you the password.
- **"command not found: towerwatch-speedtest"** — the Pi hasn't been redeployed since this feature was added. Ask the operator to run `./scripts/deploy.sh`.

## Why ~400 MB?

A proper Ookla speedtest downloads and uploads large chunks to measure the network's real capacity. On a 5G connection that means about 400 MB of data per run. The operator's data budget assumes this is rare — don't run it more than a few times a day.
