# Pi hardening for unattended deployments

These are one-time setup steps after running `install.sh`. Both are optional but strongly recommended for a permanently-deployed Pi.

## Remote access (Tailscale)

Tailscale gives the Pi a stable private IP reachable from anywhere, without port forwarding. The free Personal plan is enough.

```bash
# On the Pi
curl -fsSL https://tailscale.com/install.sh | sh

# So Tailscale state survives an overlayfs root (see next section)
sudo systemctl enable --now var-lib-tailscale.mount

sudo tailscale up   # opens an auth URL
```

Install Tailscale on your dev machine too, log in with the same account, and `ssh <user>@<tailscale-ip>` from anywhere.

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
