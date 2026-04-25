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

# --- Create towerwatch users ---
# Two accounts:
#   `towerwatch`       — system user, owns /opt/towerwatch, runs the daemon. No login.
#   `towerwatch-user`  — login account for remote operators on the Tailnet who
#                        need to trigger a manual speedtest. Member of the
#                        `towerwatch` group so they can read credentials.py
#                        (mode 640). sshd ForceCommand restricts them to running
#                        only `/usr/local/bin/towerwatch-speedtest`.
echo "[3/9] Creating towerwatch users..."
if ! id -u towerwatch &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin towerwatch
fi
if ! id -u towerwatch-user &>/dev/null; then
    useradd --create-home --shell /bin/bash --gid towerwatch towerwatch-user
    # No password — login is SSH-only via Tailscale.
    passwd -l towerwatch-user >/dev/null
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

# Credentials and the build-version stamp are intentionally gitignored, so
# they don't ship inside the wheel. Copy them into the installed package
# location post-install so runtime imports ("from towerwatch import credentials",
# "Path(_version.txt)") resolve.
SITE_PKG="$(ls -d "$VENV_DIR"/lib/python*/site-packages/towerwatch 2>/dev/null | head -1)"
if [ -z "$SITE_PKG" ]; then
    echo "ERROR: could not locate installed towerwatch package under $VENV_DIR"
    exit 1
fi
if [ -f "$REPO_DIR/src/towerwatch/credentials.py" ]; then
    cp "$REPO_DIR/src/towerwatch/credentials.py" "$SITE_PKG/credentials.py"
    # 640 (not 600) so towerwatch-user can read via group membership.
    chown towerwatch:towerwatch "$SITE_PKG/credentials.py"
    chmod 640 "$SITE_PKG/credentials.py"
fi
if [ -f "$REPO_DIR/src/towerwatch/_version.txt" ]; then
    cp "$REPO_DIR/src/towerwatch/_version.txt" "$SITE_PKG/_version.txt"
else
    # _version.txt is gitignored and the dev machine is the version authority
    # (see CLAUDE.md). It arrives via scripts/deploy.sh, which scp's it from
    # the dev machine after ci.sh stamps it. install-pi.sh runs FIRST during
    # onboarding (it sets up venv/systemd/data partition); the immediately
    # following deploy.sh call places _version.txt and restarts the service
    # with a real version. Until then BUILD_VERSION shows "dev" — that's
    # expected for the brief window between install and first deploy.
    echo "  NOTE: _version.txt not present (expected for first-time onboarding)."
    echo "        Run './ci.sh && ./scripts/deploy.sh admin@<host>' from the dev"
    echo "        machine to stamp + ship a real BUILD_VERSION."
fi
chown -R towerwatch:towerwatch "$INSTALL_DIR"

# Warn if credentials.py is missing inside the repo (gitignored).
if [ ! -f "$REPO_DIR/src/towerwatch/credentials.py" ]; then
    echo "  WARNING: $REPO_DIR/src/towerwatch/credentials.py not found."
    echo "           Copy credentials.py.example to credentials.py and re-run install,"
    echo "           or deploy.sh will SCP yours over on first deploy."
fi

# --- /usr/local/bin symlink + sshd lockdown for towerwatch-user ---
# Symlink: stable PATH-accessible name for the speedtest CLI; insulates
# ForceCommand from venv path drift.
# sshd drop-in: when towerwatch-user logs in, run only the speedtest CLI.
# Any client-supplied command is ignored. No TCP/X11/agent forwarding.
echo "[4.5/9] Locking down towerwatch-user SSH + symlink..."
ln -sf "$VENV_DIR/bin/towerwatch-speedtest" /usr/local/bin/towerwatch-speedtest

SSHD_DROPIN="/etc/ssh/sshd_config.d/99-towerwatch-user.conf"
mkdir -p /etc/ssh/sshd_config.d
cat > "$SSHD_DROPIN" << 'EOF'
# Lock the towerwatch-user account to running only the speedtest CLI.
# Edited by scripts/install-pi.sh — do not hand-modify.
Match User towerwatch-user
    ForceCommand /usr/local/bin/towerwatch-speedtest
    PermitTTY yes
    X11Forwarding no
    AllowAgentForwarding no
    AllowTcpForwarding no
    PermitTunnel no
    # Password auth is allowed for towerwatch-user only — operators may
    # share a temporary password with a new remote user until that user's
    # SSH key is installed. ForceCommand still pins the session to the
    # speedtest CLI, so the password unlocks nothing else.
    PasswordAuthentication yes
    PubkeyAuthentication yes

# Everyone else (admin, root, etc.) is keys-only. This overrides the
# Pi-OS default in /etc/ssh/sshd_config.d/50-cloud-init.conf which sets
# PasswordAuthentication yes globally.
Match User *,!towerwatch-user
    PasswordAuthentication no
EOF
# Validate config before reload (sshd -t exits non-zero on bad config).
if sshd -t; then
    systemctl reload ssh
    echo "  sshd reloaded with towerwatch-user ForceCommand"
else
    echo "  ERROR: sshd config invalid; drop-in not activated. Inspect $SSHD_DROPIN"
    exit 1
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
# Trixie's /etc/default/fake-hwclock ships without a FILE= line at all (only
# FORCE=). Older Bookworm shipped it commented out as "#FILE=...". Handle both:
# replace any existing (commented or not) FILE= line, or append one if missing.
echo "[6/9] Configuring fakehwclock for writable partition..."
FHWC_FILE="$DATA_MOUNT/fake-hwclock.data"
if [ ! -f /etc/default/fake-hwclock ]; then
    echo "FILE=$FHWC_FILE" > /etc/default/fake-hwclock
elif grep -qE '^#?FILE=' /etc/default/fake-hwclock; then
    sed -i "s|^#\?FILE=.*|FILE=$FHWC_FILE|" /etc/default/fake-hwclock
else
    echo "FILE=$FHWC_FILE" >> /etc/default/fake-hwclock
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
