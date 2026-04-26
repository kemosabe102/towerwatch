# Manual Speedtest — Guide for Remote Users

Running a speedtest on a Towerwatch Pi over SSH. Works on Windows, macOS, or Linux. No coding required.

## Who this is for

You've been asked to help verify a network's speed at a site where a Towerwatch Pi is already installed. The operator (the person who set up the Pi) will invite you to their Tailnet — a private network that lets you reach the Pi securely. You don't need anything the operator can't send you over chat.

## What you'll need from the operator

- A Tailscale invitation email.
- The Pi's Tailscale IP (looks like `100.x.y.z`).
- **Either** a temporary password for the `towerwatch-user` account (easiest to start), **or** the operator can install your SSH public key on the Pi (see [Switching to SSH keys](#switching-to-ssh-keys-recommended) below).

The operator has set up the Pi so that being on their Tailscale network plus one of the two auth options above is all you need.

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

If the operator gave you a temporary password, paste it when prompted. The first time you connect, your terminal may ask `Are you sure you want to continue connecting (yes/no)?` — type `yes` and press Enter. From then on, only the password prompt appears.

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
- **"Permission denied (publickey,password)"** — either the password is wrong, or your SSH key isn't installed yet. Double-check the password (case matters), or ask the operator to confirm.
- **"Permission denied (publickey)"** without a password prompt — the operator has set this account to keys-only and your key isn't installed. Send them your public key (see [Switching to SSH keys](#switching-to-ssh-keys-recommended) below) or ask for a temporary password.
- **No output at all, just disconnects immediately** — the operator's Pi may be missing the speedtest CLI or symlink. Ask them to redeploy with `./scripts/deploy.sh`.
- **`ssh: unknown option -- -`** — you tried to pass a flag to the speedtest, e.g. `ssh towerwatch-user@<ip> --triggered-by alice`, and `ssh` interpreted the flag as its own. Put `--` before the speedtest args so `ssh` stops parsing options: `ssh towerwatch-user@<ip> -- --triggered-by alice`. Quoting (`"--triggered-by alice"`) does **not** help — your shell strips the quotes before `ssh` sees them.

## Switching to SSH keys (recommended)

The temporary password gets you running on day one, but SSH keys are easier and more secure long-term. Once you switch, you'll never type a password again.

### 1. Generate a key (one time, on your machine)

If you've never used SSH keys before, run this in a terminal:

**Windows (PowerShell), macOS, Linux:**

```bash
ssh-keygen -t ed25519 -C "your-email@example.com"
```

Press Enter at every prompt to accept defaults. (You can set a passphrase if you want extra protection — leave it blank for the simplest experience.) This creates two files in `~/.ssh/`:

- `id_ed25519` — your **private** key. Never share this. Never email it. Never paste it anywhere.
- `id_ed25519.pub` — your **public** key. This one is safe to share.

### 2. Send your public key to the operator

Print it and copy the output:

**macOS / Linux:** `cat ~/.ssh/id_ed25519.pub`
**Windows (PowerShell):** `Get-Content $HOME\.ssh\id_ed25519.pub`

It looks like one long line starting with `ssh-ed25519 AAAA...` and ending with your email. Send the **entire line** to the operator. They'll install it on the Pi.

### 3. Test it

After the operator confirms it's installed, run the speedtest command again — no password prompt this time.

## Why ~400 MB?

A proper Ookla speedtest downloads and uploads large chunks to measure the network's real capacity. On a 5G connection that means about 400 MB of data per run. The operator's data budget assumes this is rare — don't run it more than a few times a day.
