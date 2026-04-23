#!/bin/bash
set -euo pipefail

# =============================================================
# Towerwatch Raspberry Pi one-time setup.
# Run once on a fresh Pi OS Lite install, from the checked-out repo.
#
#   cd ~/towerwatch && sudo bash scripts/install-pi.sh
#
# This replaces pi/install.sh. Differences:
#   * Creates /opt/towerwatch/.venv and pip installs the package into it.
#   * systemd ExecStart=/opt/towerwatch/.venv/bin/towerwatch.
#   * No file-by-file cp of .py sources. Deploy uses `pip install .`.
# =============================================================

INSTALL_DIR="/opt/towerwatch"
VENV_DIR="$INSTALL_DIR/.venv"
DATA_DEV="/dev/mmcblk0p3"
DATA_MOUNT="/opt/towerwatch/data"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Towerwatch Pi Install ==="
echo "    Repo: $REPO_DIR"
echo "    Install: $INSTALL_DIR"

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run as root (sudo bash scripts/install-pi.sh)"
    exit 1
fi

# --- System packages ---
echo "[1/9] Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip fake-hwclock curl

# --- Ookla Speedtest CLI (direct ARM binary, NOT the broken apt repo) ---
echo "[2/9] Installing Ookla Speedtest CLI..."
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
echo "[3/9] Creating towerwatch user..."
if ! id -u towerwatch &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin towerwatch
fi

# --- Create install dir and venv; pip install the repo ---
# We create and populate the venv as root (since it lives in /opt and we may
# need to write to /home/admin/towerwatch which `towerwatch` user can't read),
# then chown to the towerwatch user afterward.
echo "[4/9] Creating $VENV_DIR and installing package..."
mkdir -p "$INSTALL_DIR"
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip
"$VENV_DIR/bin/python" -m pip install --quiet --upgrade "$REPO_DIR"
chown -R towerwatch:towerwatch "$INSTALL_DIR"

# Warn if credentials.py is missing inside the repo (gitignored).
if [ ! -f "$REPO_DIR/src/towerwatch/credentials.py" ]; then
    echo "  WARNING: $REPO_DIR/src/towerwatch/credentials.py not found."
    echo "           Copy credentials.py.example to credentials.py and re-run install,"
    echo "           or deploy.sh will SCP yours over on first deploy."
fi

# --- Mount data partition ---
echo "[5/9] Setting up data partition..."
mkdir -p "$DATA_MOUNT"
if [ -b "$DATA_DEV" ]; then
    if ! grep -q "$DATA_DEV" /etc/fstab; then
        echo "$DATA_DEV $DATA_MOUNT ext4 defaults,noatime 0 2" >> /etc/fstab
        echo "  Added fstab entry for $DATA_DEV"
    fi
    if ! mountpoint -q "$DATA_MOUNT"; then
        mount "$DATA_MOUNT" || echo "  WARNING: could not mount $DATA_MOUNT"
    fi
else
    echo "  INFO: $DATA_DEV not found — skipping (data will use root fs)"
fi
mkdir -p "$DATA_MOUNT/buffer" "$DATA_MOUNT/tailscale-state"
chown -R towerwatch:towerwatch "$DATA_MOUNT/buffer"

# --- Tailscale state bind mount (if Tailscale installed) ---
if command -v tailscaled &>/dev/null; then
    if [ -d /var/lib/tailscale ] && [ ! -L /var/lib/tailscale ]; then
        cp -a /var/lib/tailscale/* "$DATA_MOUNT/tailscale-state/" 2>/dev/null || true
    fi
    if [ -L /var/lib/tailscale ]; then
        rm -f /var/lib/tailscale
        mkdir -p /var/lib/tailscale
    fi
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
    echo "  Tailscale not installed yet — skipping (re-run install-pi.sh after tailscale up)"
fi

# --- fakehwclock: point to writable partition ---
echo "[6/9] Configuring fakehwclock for writable partition..."
FHWC_FILE="$DATA_MOUNT/fake-hwclock.data"
if [ -f /etc/default/fake-hwclock ]; then
    sed -i "s|^#\?FILE=.*|FILE=$FHWC_FILE|" /etc/default/fake-hwclock
else
    echo "FILE=$FHWC_FILE" > /etc/default/fake-hwclock
fi
mkdir -p /etc/systemd/system/fake-hwclock.service.d
cat > /etc/systemd/system/fake-hwclock.service.d/writable.conf << 'EOF'
[Unit]
RequiresMountsFor=/opt/towerwatch/data
EOF
touch "$FHWC_FILE"

# --- Hardware watchdog ---
echo "[7/9] Enabling hardware watchdog..."
if ! grep -q "RuntimeWatchdogSec" /etc/systemd/system.conf; then
    sed -i 's/^#RuntimeWatchdogSec=.*/RuntimeWatchdogSec=15/' /etc/systemd/system.conf
    echo "  RuntimeWatchdogSec=15 set"
fi

# --- Tailscale watchdog timer ---
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
    echo "  Tailscale not installed yet — skipping watchdog"
fi

# --- Install systemd service ---
echo "[9/9] Installing towerwatch systemd service..."
cp "$REPO_DIR/scripts/towerwatch.service" /etc/systemd/system/towerwatch.service
systemctl daemon-reload
systemctl enable towerwatch.service

chown -R towerwatch:towerwatch "$INSTALL_DIR"

echo ""
echo "=== Install complete ==="
echo ""
echo "Next steps:"
echo "  1. If you haven't yet, copy secrets:"
echo "       cp src/towerwatch/credentials.py.example src/towerwatch/credentials.py"
echo "       chmod 600 src/towerwatch/credentials.py"
echo "       # ...edit with your Grafana Cloud creds..."
echo "  2. sudo systemctl restart towerwatch"
echo "  3. journalctl -u towerwatch -f"
