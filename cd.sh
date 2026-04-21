#!/bin/bash
set -euo pipefail

# =============================================================
# Towerwatch CD Script — ship to the Pi.
# Run from your dev machine after ./ci.sh succeeds.
#
# Usage: bash cd.sh <user@host> [repo_dir] [install_dir]
#
# Arguments:
#   user@host    SSH target (required)
#   repo_dir     Git repo path on Pi (default: /home/admin/towerwatch)
#   install_dir  Service install path (default: /opt/towerwatch)
# =============================================================

if [ $# -lt 1 ]; then
    echo "Usage: bash cd.sh <user@host> [repo_dir] [install_dir]"
    echo "  Example: bash cd.sh pi@towerwatch.local"
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

# --- CI freshness check -----------------------------------------------------
# version.txt must exist and be at least as new as any .py file in pi/.
# Protects against deploying an un-stamped (or stale-stamp) tree.
if [[ ! -s pi/version.txt ]]; then
    echo "ERROR: pi/version.txt missing. Run ./ci.sh first."
    exit 1
fi
STAMP_MTIME=$(stat -c %Y pi/version.txt 2>/dev/null || stat -f %m pi/version.txt)
NEWER=$(find pi -name "*.py" -newer pi/version.txt -print -quit)
if [[ -n "$NEWER" ]]; then
    echo "ERROR: $NEWER is newer than pi/version.txt — run ./ci.sh again."
    exit 1
fi

PI_HOST="$1"
REPO_DIR="${2:-/home/admin/towerwatch}"
INSTALL_DIR="${3:-/opt/towerwatch}"

VERSION_STAMP="$(cat pi/version.txt)"
echo "=== Towerwatch Deploy to $PI_HOST ==="
echo "    Version: $VERSION_STAMP"

# SCP gitignored files the Pi needs but git pull won't deliver.
echo "[0/3] Uploading version.txt and credentials.py..."
scp pi/version.txt "$PI_HOST:$REPO_DIR/pi/version.txt"
if [[ -f pi/credentials.py ]]; then
    scp pi/credentials.py "$PI_HOST:/tmp/towerwatch-credentials.py"
    ssh "$PI_HOST" "sudo mv /tmp/towerwatch-credentials.py $INSTALL_DIR/credentials.py && sudo chown towerwatch:towerwatch $INSTALL_DIR/credentials.py && sudo chmod 600 $INSTALL_DIR/credentials.py"
else
    echo "  WARNING: pi/credentials.py not found locally — skipping credentials deploy."
    echo "           Ensure /opt/towerwatch/credentials.py is up to date on the Pi."
fi

ssh "$PI_HOST" bash -s "$REPO_DIR" "$INSTALL_DIR" << 'REMOTE'
set -euo pipefail
REPO_DIR="$1"
INSTALL_DIR="$2"

# Step 1: Pull latest code
echo "[1/3] Pulling latest code..."
cd "$REPO_DIR" && git pull --ff-only
sudo cp pi/towerwatch.py pi/config.py pi/loki.py pi/grafana.py \
    pi/events.py pi/scheduling.py pi/startup.py pi/version.txt "$INSTALL_DIR/"
sudo cp -r pi/probes "$INSTALL_DIR/"
sudo chown towerwatch:towerwatch \
    "$INSTALL_DIR/towerwatch.py" "$INSTALL_DIR/config.py" \
    "$INSTALL_DIR/loki.py" "$INSTALL_DIR/grafana.py" \
    "$INSTALL_DIR/events.py" "$INSTALL_DIR/scheduling.py" \
    "$INSTALL_DIR/startup.py" "$INSTALL_DIR/version.txt"
echo "  Files copied to $INSTALL_DIR (version: $(cat $INSTALL_DIR/version.txt))"

# Step 1.5: Clean up legacy metrics buffer (replaced by Loki log buffer)
sudo rm -f "$INSTALL_DIR/data/buffer/metrics.csv" "$INSTALL_DIR/data/buffer/metrics.csv.tmp"

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
