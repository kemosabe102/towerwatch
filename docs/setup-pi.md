# Pi hardening for unattended deployments

These are one-time setup steps after running `install.sh`. Both are optional but strongly recommended for a permanently-deployed Pi.

## Pre-boot SD-card prep

Two things to do on the dev machine after Imager finishes flashing but before the Pi's first boot. Both target files on the FAT32 `bootfs` partition, which Mac/Windows/Linux all mount automatically.

### Disable rootfs auto-expansion

Pi OS Lite's first-boot flow expands `rootfs` (`/dev/mmcblk0p2`) to fill the entire SD card. That leaves zero room for the `twdata` data partition. Remove the trigger token from `cmdline.txt` before first boot.

**Always `cat` the file first** — the token name has changed across Pi OS versions:

- **Imager 1.8+ / Pi OS Bookworm+ (2024+):** bare `resize` token (no `init=` path). The initramfs `local-premount/firstboot` hook reads it.
- **Older Pi OS:** `init=/usr/lib/raspberrypi-sys-mods/firstboot` instead.

Remove whichever applies (with one of the surrounding spaces so the line stays well-formed):

```bash
# macOS — modern (Imager 1.8+):
sed -i '' 's| resize||' /Volumes/bootfs/cmdline.txt

# macOS — legacy fallback:
sed -i '' 's| init=/usr/lib/raspberrypi-sys-mods/firstboot||' /Volumes/bootfs/cmdline.txt

# Linux — same patterns, mount path varies by distro:
sudo sed -i 's| resize||' /media/$USER/bootfs/cmdline.txt

# Windows — open bootfs in Explorer, edit cmdline.txt in Notepad++ or VS Code.
# DO NOT use plain Notepad — it inserts a UTF-8 BOM that prevents boot.
```

Leave the `ds=nocloud;i=rpi-imager-...` token alone — that's cloud-init applying hostname/user/SSH-key on first boot.

`cmdline.txt` is a single line with no trailing newline. Verify with `cat /Volumes/bootfs/cmdline.txt`.

After boot, run `scripts/partition-pi-data.sh` to append the `twdata` partition into the unallocated space.

### Pre-load your SSH public key

In Raspberry Pi Imager → advanced options → enable SSH → "Allow public-key authentication only" → paste `~/.ssh/id_ed25519.pub`. The Pi boots with your key already in `~admin/.ssh/authorized_keys`. No password ever needed. Don't use `sshpass` workarounds — they leak credentials via `ps`.

### Verify passwordless sudo before running install-pi.sh

When Imager provisions the user account it also writes `/etc/sudoers.d/010_pi-nopasswd` granting `admin` passwordless sudo. `scripts/deploy.sh` depends on this — without it, the deploy hangs on the first `sudo` call. Confirm before going further:

```bash
ssh admin@<hostname>.local 'sudo -n true && echo "passwordless sudo OK"'
```

If it prompts for a password instead of printing OK, Imager didn't apply the file (rare). Create it manually:

```bash
ssh admin@<hostname>.local
echo "admin ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/010_pi-nopasswd
sudo chmod 440 /etc/sudoers.d/010_pi-nopasswd
sudo visudo -c -f /etc/sudoers.d/010_pi-nopasswd
```

## Remote access (Tailscale)

Tailscale gives the Pi a stable private IP reachable from anywhere, without port forwarding. The free Personal plan is enough.

```bash
# On the Pi
curl -fsSL https://tailscale.com/install.sh | sh

# So Tailscale state survives an overlayfs root (see next section).
# This unit is created by install-pi.sh — only enable it AFTER running install-pi.sh.
sudo systemctl enable --now var-lib-tailscale.mount

sudo tailscale up --hostname=<hostname>   # opens an auth URL
```

Install Tailscale on your dev machine too, log in with the same account, and `ssh <user>@<tailscale-ip>` from anywhere.

**For unattended remote nodes, disable key expiry.** In the Tailscale admin console → Machines → `<this-node>` → "..." menu → "Disable key expiry". Without this, the node drops off the tailnet every 6 months and you have to re-auth in person — which defeats the point of putting it at a remote site.

**Pi-side clone uses HTTPS, not SSH.** The towerwatch repo is public, so `git clone https://github.com/<your-fork>/towerwatch.git` works without provisioning a deploy key on each Pi. Save SSH for git pushes from your dev machine.

## Two-tier SSH access (operator vs speedtest user)

`scripts/install-pi.sh` creates two accounts:

- **`admin`** (your operator account, set up by the Raspberry Pi Imager) — full sudo, used for deploys and shell access.
- **`towerwatch-user`** — a locked-down account for remote operators who only need to trigger a manual speedtest. sshd's `ForceCommand` pins their session to running exactly `/usr/local/bin/towerwatch-speedtest` and nothing else; no shell, no sudo, no port forwarding. Credentials are accessible to it via group membership only (mode 640, owned by `towerwatch:towerwatch`).

Anyone you add to your Tailscale network and authorize via ACL can `ssh towerwatch-user@<pi-tailscale-ip>` to run a speedtest. The CLI auto-detects their Tailscale identity (`tailscale whois`) so the dashboard tag is accurate without the user passing their name.

### Tailscale ACL for the speedtest account

In the [Tailscale admin console](https://login.tailscale.com/admin/acls), tag the Pi (e.g. `tag:towerwatch`) and add an ACL entry that grants `someone@example.com` SSH-as-`towerwatch-user` (and nothing else):

```json
{
  "ssh": [
    {
      "action": "accept",
      "src":    ["someone@example.com"],
      "dst":    ["tag:towerwatch"],
      "users":  ["towerwatch-user"]
    }
  ]
}
```

The user can now SSH into the Pi as `towerwatch-user` but cannot reach `admin`. Revoke by removing the ACL entry or the Tailscale account.

Hand off [`docs/manual-speedtest.md`](manual-speedtest.md) once they're authorized.

### Onboarding a new remote user

Two paths — pick whichever fits the user. Most non-technical users start with the password and switch to a key later.

**Option A: Temporary password (fastest).** `install-pi.sh` allows password auth for `towerwatch-user` only (every other account is keys-only, including `admin`). Set or rotate it with:

```bash
ssh admin@<pi-tailscale-ip>
sudo passwd towerwatch-user   # interactive prompt; choose a fresh password
```

Share the password with the user over a private channel (signal, password manager, in person — not email or chat). The user logs in with `ssh towerwatch-user@<pi-tailscale-ip>` and pastes the password when prompted. Rotate any time by re-running `sudo passwd towerwatch-user`.

**Option B: SSH public key (recommended for ongoing use).**

The operator who flashed the SD card already has their key in `admin`'s `authorized_keys` (Pi OS Imager put it there). `install-pi.sh` automatically copies that file into `/home/towerwatch-user/.ssh/authorized_keys` on first run with the right ownership (`towerwatch-user:towerwatch`) and modes (700/600), so the operator can `ssh towerwatch-user@<pi>` immediately without any further setup. Re-running `install-pi.sh` later is a no-op for this file (it only seeds if the destination is empty or missing).

To grant a **different** user access — someone who didn't flash the SD card — append their pubkey to the Pi:

```bash
ssh admin@<pi-tailscale-ip>
echo "<paste-the-pubkey-line-here>" | sudo tee -a /home/towerwatch-user/.ssh/authorized_keys
sudo chown towerwatch-user:towerwatch /home/towerwatch-user/.ssh/authorized_keys
sudo chmod 600 /home/towerwatch-user/.ssh/authorized_keys
```

If `/home/towerwatch-user/.ssh/` doesn't exist (the operator-key seed was skipped, e.g. because `admin` had no `authorized_keys`), create it first: `sudo install -d -o towerwatch-user -g towerwatch -m 700 /home/towerwatch-user/.ssh`.

Once the key works, lock the password again so it can't be reused: `sudo passwd -l towerwatch-user`.

### Note on Tailscale SSH (optional, advanced)

`tailscale up --ssh` enables Tailscale's own SSH broker, which would bypass the standard sshd configuration this repo relies on for the `ForceCommand` lockdown. Don't enable it on the speedtest Pi unless you also rework the lockdown to use Tailscale ACL `ssh-action` rules instead. For your operator account (`admin`), continue using standard key-based SSH auth.

## Read-only root filesystem

Recommended for unattended remote deployments — the root partition resets on every reboot, so a stray write or SD-card glitch can't corrupt the system. The data partition stays writable so the buffer and Tailscale state persist.

> **Do not use `raspi-config` → Overlay File System on Bookworm if you rely on a separate data partition.** The overlay applies to *all* mounted partitions by default, making your data partition non-persistent. This is documented upstream ([raspberrypi/bookworm-feedback#137](https://github.com/raspberrypi/bookworm-feedback/issues/137), closed by design; proposed fix [RPi-Distro/raspi-config#225](https://github.com/RPi-Distro/raspi-config/pull/225) was never merged). Configure manually instead:

```bash
echo 'overlayroot=tmpfs:recurse=0' | sudo tee /etc/overlayroot.local.conf
sudo reboot
```

`recurse=0` is the critical flag — without it the data partition gets overlaid too.

Before enabling overlayroot, confirm `install.sh` has already:
- Bind-mounted `/var/lib/tailscale/` → `/opt/towerwatch/data/tailscale-state/`
- Configured `fake-hwclock` to write to the data partition
