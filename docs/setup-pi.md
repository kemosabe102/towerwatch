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

## Passwordless SSH via Tailscale

If you want Tailnet members (e.g. someone running a manual speedtest) to reach the Pi without distributing SSH keys or sharing the `admin` password, enable Tailscale SSH:

```bash
sudo tailscale up --ssh   # re-run is safe; reuses existing login
```

Tailscale now brokers SSH auth using each peer's Tailnet identity. First connection from a given user prompts a one-tap approval in the [Tailscale admin console](https://login.tailscale.com/admin/machines); subsequent connections are seamless. To revoke access, remove the user from your Tailnet.

The Pi row in the admin console will show an `SSH` badge once this is active. No sshd/authorized_keys changes are required.

Hand off [`docs/manual-speedtest.md`](manual-speedtest.md) to any remote user who needs to trigger a speedtest.

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
