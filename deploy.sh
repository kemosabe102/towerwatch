#!/bin/bash
set -euo pipefail

# =============================================================
# Towerwatch Deploy Script (generic)
# Pushes latest code from git to the Raspberry Pi.
# Run from your dev machine (not on the Pi).
#
# Usage: bash deploy.sh <user@host> [repo_dir] [install_dir]
#
# Arguments:
#   user@host    SSH target (required)
#   repo_dir     Git repo path on Pi (default: /home/admin/towerwatch)
#   install_dir  Service install path (default: /opt/towerwatch)
#
# Tip: Create a deploy-local.sh wrapper with your specific host/paths.
# =============================================================

if [ $# -lt 1 ]; then
    echo "Usage: bash deploy.sh <user@host> [repo_dir] [install_dir]"
    echo "  Example: bash deploy.sh admin@100.76.154.81"
    exit 1
fi

PI_HOST="$1"
REPO_DIR="${2:-/home/admin/towerwatch}"
INSTALL_DIR="${3:-/opt/towerwatch}"

echo "=== Towerwatch Deploy to $PI_HOST ==="

ssh "$PI_HOST" bash -s "$REPO_DIR" "$INSTALL_DIR" << 'REMOTE'
set -euo pipefail
REPO_DIR="$1"
INSTALL_DIR="$2"

# Step 1: Pull latest code
echo "[1/3] Pulling latest code..."
cd "$REPO_DIR" && git pull --ff-only
sudo cp pi/towerwatch.py pi/config.py "$INSTALL_DIR/"
sudo chown towerwatch:towerwatch "$INSTALL_DIR/towerwatch.py" "$INSTALL_DIR/config.py"
echo "  Files copied to $INSTALL_DIR"

# Step 2: Restart service
echo "[2/3] Restarting towerwatch service..."
sudo systemctl restart towerwatch

# Step 3: Verify
echo "[3/3] Verifying..."
sleep 2
if sudo systemctl is-active --quiet towerwatch; then
    echo "=== Deploy complete — towerwatch is running ==="
else
    echo "=== ERROR: towerwatch failed to start ==="
    sudo journalctl -u towerwatch --no-pager -n 20
    exit 1
fi
REMOTE
