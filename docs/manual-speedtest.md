# Manual Speedtest — Guide for Remote Users

Running a speedtest on a Towerwatch Pi over SSH. Works on Windows, macOS, or Linux. No coding required.

## Who this is for

You've been asked to help verify a network's speed at a site where a Towerwatch Pi is already installed. The operator (the person who set up the Pi) will invite you to their Tailnet — a private network that lets you reach the Pi securely. You don't need anything the operator can't send you over chat.

## What you'll need from the operator

- A Tailscale invitation email.
- The Pi's Tailscale IP (looks like `100.x.y.z`).

That's it. With Tailscale SSH enabled (the operator handles this once), you don't need a password or an SSH key — your Tailnet identity is the auth.

## Step 1 — Join the Tailnet

1. **Accept the invite.** Click the link in the Tailscale invitation email and sign in with the email address the operator used.
2. **Install Tailscale** from <https://tailscale.com/download> for your OS:
   - Windows: `.exe` installer.
   - macOS: Mac App Store or direct download.
   - Linux: one-line `curl -fsSL https://tailscale.com/install.sh | sh`.
3. **Sign in** through the Tailscale app using the same email.
4. Verify it's working: open the Tailscale app (or run `tailscale status`) and confirm the Pi appears in your device list.

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

The first time you connect, Tailscale may briefly open a browser window asking you to authorize this device — that's the Tailscale-SSH check-in, not an SSH password prompt. One click and you're in. Subsequent connections skip the check-in.

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
- **Prompts for an SSH password** — the operator hasn't enabled Tailscale SSH on the Pi yet. Ask them to run `sudo tailscale up --ssh` on it.
- **Browser check-in didn't open on first connect** — open the Tailscale app, sign out, sign back in, and try again. The check-in only appears when Tailscale can reach the OS browser; headless terminals may not trigger it.
- **"Permission denied (publickey)"** — same cause as the password prompt above: Tailscale SSH isn't enabled on the Pi. Ask the operator.
- **"command not found: towerwatch-speedtest"** — the Pi hasn't been redeployed since this feature was added. Ask the operator to run `./scripts/deploy.sh`.

## Why ~400 MB?

A proper Ookla speedtest downloads and uploads large chunks to measure the network's real capacity. On a 5G connection that means about 400 MB of data per run. The operator's data budget assumes this is rare — don't run it more than a few times a day.
