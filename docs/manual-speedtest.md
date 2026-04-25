# Manual Speedtest — Guide for Remote Users

Running a speedtest on a Towerwatch Pi over SSH. Works on Windows, macOS, or Linux. No coding required.

## Who this is for

You've been asked to help verify a network's speed at a site where a Towerwatch Pi is already installed. The operator (the person who set up the Pi) will invite you to their Tailnet — a private network that lets you reach the Pi securely. You don't need anything the operator can't send you over chat.

## What you'll need from the operator

- A Tailscale invitation email.
- The Pi's Tailscale IP (looks like `100.x.y.z`).

That's it. The operator has set up Tailscale ACLs so that being on their Tailscale network is enough — no password, no SSH key, no extra setup on your side.

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
ssh towerwatch-user@<tailscale-ip>
```

Example:

```bash
ssh towerwatch-user@100.76.154.81
```

You don't need to type a command after the SSH target — the speedtest runs automatically when you connect, then exits. Your Tailscale identity is recorded automatically; you don't have to pass your name.

## What you'll see

```
Speedtest started on remote-site... (takes ~60s, uses ~400 MB)
✓ Success — results will appear on the Grafana dashboard within a minute.
```

If something went wrong:

```
Speedtest started on remote-site... (takes ~60s, uses ~400 MB)
✗ Failed — contact the operator.
```

The actual numbers (download / upload Mbps) appear on the operator's Grafana dashboard, tagged with your Tailscale email — so they know who ran it and when. Ask the operator for a dashboard link if you want to see the result yourself.

## Troubleshooting

- **"Connection timed out"** — the Pi may be offline, or Tailscale hasn't finished connecting on your machine. Open the Tailscale app and check that both you and the Pi show "Connected."
- **"Permission denied (publickey)"** or prompts for a password — the operator hasn't added you to the Tailscale ACL for this Pi yet. Send them your Tailscale account email so they can add you.
- **No output at all, just disconnects immediately** — the operator's Pi may be missing the speedtest CLI or symlink. Ask them to redeploy with `./scripts/deploy.sh`.

## Why ~400 MB?

A proper Ookla speedtest downloads and uploads large chunks to measure the network's real capacity. On a 5G connection that means about 400 MB of data per run. The operator's data budget assumes this is rare — don't run it more than a few times a day.
