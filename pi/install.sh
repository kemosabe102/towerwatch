#!/bin/bash
set -euo pipefail

# =============================================================
# Towerwatch Raspberry Pi Setup Script
# Run once on a fresh Pi OS Lite install.
# Usage: sudo bash install.sh
# =============================================================

INSTALL_DIR="/opt/towerwatch"
DATA_DEV="/dev/mmcblk0p3"
DATA_MOUNT="/opt/towerwatch/data"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Towerwatch Install ==="

# --- Preflight ---
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: Run as root (sudo bash install.sh)"
    exit 1
fi

# --- System packages ---
echo "[1/8] Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3-pip python3-venv fake-hwclock

# --- Python dependencies ---
echo "[2/8] Installing Python dependencies..."
pip3 install --break-system-packages --ignore-installed -r "$SCRIPT_DIR/requirements.txt"

# --- Ookla Speedtest CLI (direct ARM binary, NOT the broken apt repo) ---
echo "[3/8] Installing Ookla Speedtest CLI..."
if [ ! -f /usr/bin/speedtest ]; then
    ARCH=$(uname -m)
    TMPDIR=$(mktemp -d)
    curl -sL "https://install.speedtest.net/app/cli/ookla-speedtest-1.2.0-linux-${ARCH}.tgz" \
        -o "$TMPDIR/speedtest.tgz"
    tar -xzf "$TMPDIR/speedtest.tgz" -C "$TMPDIR"
    install -m 755 "$TMPDIR/speedtest" /usr/bin/speedtest
    rm -rf "$TMPDIR"
    echo "  Speedtest CLI installed at /usr/bin/speedtest"
else
    echo "  Speedtest CLI already installed"
fi

# --- Create towerwatch user ---
echo "[4/8] Creating towerwatch user..."
if ! id -u towerwatch &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin towerwatch
fi

# --- Install application files ---
echo "[5/8] Installing towerwatch to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
cp "$SCRIPT_DIR/towerwatch.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/config.py" "$INSTALL_DIR/"
# version.txt is stamped by ci.sh on the dev machine; cd.sh copies it during
# deploy. On a fresh install it may not exist yet — fall back to a marker.
if [ -f "$SCRIPT_DIR/version.txt" ]; then
    cp "$SCRIPT_DIR/version.txt" "$INSTALL_DIR/"
else
    echo "dev unknown" > "$INSTALL_DIR/version.txt"
fi

if [ -f "$SCRIPT_DIR/secrets.py" ]; then
    cp "$SCRIPT_DIR/secrets.py" "$INSTALL_DIR/"
    chmod 600 "$INSTALL_DIR/secrets.py"
    chown towerwatch:towerwatch "$INSTALL_DIR/secrets.py"
else
    echo "  WARNING: secrets.py not found. Copy secrets.py.example to secrets.py and re-run."
fi
chown -R towerwatch:towerwatch "$INSTALL_DIR"

# --- Mount data partition ---
echo "[6/8] Setting up data partition..."
mkdir -p "$DATA_MOUNT"

if [ -b "$DATA_DEV" ]; then
    # Add fstab entry if not already present
    if ! grep -q "$DATA_DEV" /etc/fstab; then
        echo "$DATA_DEV $DATA_MOUNT ext4 defaults,noatime 0 2" >> /etc/fstab
        echo "  Added fstab entry for $DATA_DEV"
    fi
    # Mount if not already mounted
    if ! mountpoint -q "$DATA_MOUNT"; then
        mount "$DATA_MOUNT" || echo "  WARNING: Could not mount $DATA_MOUNT"
    fi
else
    echo "  INFO: $DATA_DEV not found — skipping fstab/mount (data will use root filesystem)"
fi

# Create data subdirectories
mkdir -p "$DATA_MOUNT/buffer"
mkdir -p "$DATA_MOUNT/tailscale-state"
chown -R towerwatch:towerwatch "$DATA_MOUNT/buffer"

# --- Tailscale state directory ---
# Bind mount to writable partition (symlinks break systemd StateDirectory)
if command -v tailscaled &>/dev/null; then
    # Copy existing state to data partition if needed
    if [ -d /var/lib/tailscale ] && [ ! -L /var/lib/tailscale ]; then
        cp -a /var/lib/tailscale/* "$DATA_MOUNT/tailscale-state/" 2>/dev/null || true
    fi
    # Remove symlink from old installs
    if [ -L /var/lib/tailscale ]; then
        rm -f /var/lib/tailscale
        mkdir -p /var/lib/tailscale
    fi
    # Install bind mount unit.
    # WantedBy=multi-user.target (not local-fs.target) avoids an ordering cycle:
    # the data partition mount is Before=local-fs.target, so making the bind
    # mount WantedBy=local-fs.target creates a cycle systemd breaks by dropping
    # the job, leaving Tailscale stateless on reboot.
    cat > /etc/systemd/system/var-lib-tailscale.mount << MOUNTEOF
[Unit]
Description=Bind mount Tailscale state to data partition
After=opt-towerwatch-data.mount
Requires=opt-towerwatch-data.mount

[Mount]
What=$DATA_MOUNT/tailscale-state
Where=/var/lib/tailscale
Type=none
Options=bind

[Install]
WantedBy=multi-user.target
MOUNTEOF
    # tailscaled must start after the bind mount is active
    mkdir -p /etc/systemd/system/tailscaled.service.d
    cat > /etc/systemd/system/tailscaled.service.d/after-state-mount.conf << 'DROPIN'
[Unit]
After=var-lib-tailscale.mount
Requires=var-lib-tailscale.mount
DROPIN
    systemctl daemon-reload
    systemctl enable var-lib-tailscale.mount 2>/dev/null
    echo "  Tailscale bind mount unit installed"
else
    echo "  Tailscale not installed yet — skipping state migration"
fi

# --- fakehwclock: point to writable partition ---
echo "[7/8] Configuring fakehwclock for writable partition..."
FHWC_FILE="$DATA_MOUNT/fake-hwclock.data"

# Step 1: Set FILE path in config
if [ -f /etc/default/fake-hwclock ]; then
    sed -i "s|^#\?FILE=.*|FILE=$FHWC_FILE|" /etc/default/fake-hwclock
else
    echo "FILE=$FHWC_FILE" > /etc/default/fake-hwclock
fi

# Step 2: Ensure the cron script sources the config
# (most Pi OS versions already do this, but verify)

# Step 3: systemd RequiresMountsFor so it waits for data partition
mkdir -p /etc/systemd/system/fake-hwclock.service.d
cat > /etc/systemd/system/fake-hwclock.service.d/writable.conf << 'EOF'
[Unit]
RequiresMountsFor=/opt/towerwatch/data
EOF

# Touch the clock file so fakehwclock has something to read
touch "$FHWC_FILE"

# --- Hardware watchdog ---
if ! grep -q "RuntimeWatchdogSec" /etc/systemd/system.conf; then
    sed -i 's/^#RuntimeWatchdogSec=.*/RuntimeWatchdogSec=15/' /etc/systemd/system.conf
    echo "  Enabled hardware watchdog (15s)"
fi

# --- Tailscale watchdog ---
echo "[8/9] Installing Tailscale watchdog timer..."
if command -v tailscaled &>/dev/null; then
    cat > /etc/systemd/system/tailscale-watchdog.service << 'EOF'
[Unit]
Description=Tailscale watchdog — restart tailscaled if tunnel is down
After=network-online.target tailscaled.service
Requires=tailscaled.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/tailscale-watchdog.sh
EOF
    cat > /etc/systemd/system/tailscale-watchdog.timer << 'EOF'
[Unit]
Description=Run Tailscale watchdog every 5 minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=5min
AccuracySec=30s

[Install]
WantedBy=timers.target
EOF
    cat > /usr/local/bin/tailscale-watchdog.sh << 'EOF'
#!/bin/bash
# Restart tailscaled if it is not running or if the tunnel is down.
# "tailscale status" exits non-zero when stopped/not-running.
if ! systemctl is-active --quiet tailscaled; then
    echo "tailscale-watchdog: tailscaled not active — starting"
    systemctl start tailscaled
    exit 0
fi
if ! tailscale status --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('BackendState') == 'Running' else 1)"; then
    echo "tailscale-watchdog: tunnel not Running — restarting tailscaled"
    systemctl restart tailscaled
fi
EOF
    chmod +x /usr/local/bin/tailscale-watchdog.sh
    systemctl daemon-reload
    systemctl enable --now tailscale-watchdog.timer
    echo "  Tailscale watchdog timer enabled (runs every 5 min)"
else
    echo "  Tailscale not installed yet — skipping watchdog (re-run install.sh after tailscale up)"
fi

# --- Install and enable systemd service ---
echo "[9/9] Installing systemd service..."
cp "$SCRIPT_DIR/towerwatch.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable towerwatch.service

echo ""
echo "=== Install complete ==="
echo ""
echo "Next steps:"
echo "  1. Copy secrets.py.example to secrets.py and fill in credentials"
echo "  2. Run: sudo bash install.sh  (again, to install secrets.py)"
echo "  3. Install Tailscale: curl -fsSL https://tailscale.com/install.sh | sh"
echo "  4. Run: sudo tailscale up"
echo "  5. Configure overlayfs (see README — do NOT use raspi-config):"
echo "     Edit /etc/overlayroot.local.conf: overlayroot=tmpfs:recurse=0"
echo "  6. Reboot and verify: sudo reboot"
echo "  7. Check: sudo systemctl status towerwatch"
echo "  8. Check: journalctl -u towerwatch -f"
