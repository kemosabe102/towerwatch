#!/bin/bash
# =============================================================
# Towerwatch CD Script — deploy to a remote Pi over SSH.
# Run from your dev machine after ci.sh succeeds.
#
# Usage:
#   ./cd.sh <user@host>
#   e.g. ./cd.sh admin@towerwatch.local
#        ./cd.sh admin@100.76.154.81   (Tailscale)
#
# Requires: pi/version.txt stamped by ci.sh (refuses to deploy if stale).
# =============================================================
set -euo pipefail

PI_HOST="${1:?Usage: cd.sh <user@host>}"

REPO_DIR="~/towerwatch"
INSTALL_DIR="/opt/towerwatch"

# Guard: version.txt must exist and be at least as new as every .py under pi/
echo "=== Towerwatch Deploy to $PI_HOST ==="
if [[ ! -s pi/version.txt ]]; then
    echo "ERROR: pi/version.txt is missing or empty. Run ./ci.sh first."
    exit 1
fi

VERSION_TS=$(date -r pi/version.txt +%s 2>/dev/null || stat -c %Y pi/version.txt 2>/dev/null || echo 0)
for f in pi/*.py pi/probes/*.py; do
    F_TS=$(date -r "$f" +%s 2>/dev/null || stat -c %Y "$f" 2>/dev/null || echo 0)
    if [[ "$F_TS" -gt "$VERSION_TS" ]]; then
        echo "ERROR: $f is newer than pi/version.txt — re-run ./ci.sh."
        exit 1
    fi
done

VERSION="$(cat pi/version.txt)"
echo "    Version: $VERSION"

# Step 0: upload version.txt and credentials to Pi
echo "[0/3] Uploading version.txt and credentials.py..."
scp pi/version.txt "$PI_HOST:$REPO_DIR/pi/version.txt"
if [[ -f pi/credentials.py ]]; then
    scp pi/credentials.py "$PI_HOST:/tmp/towerwatch-credentials.py"
fi

# Steps 1–3 run on the Pi
ssh "$PI_HOST" bash -s "$REPO_DIR" "$INSTALL_DIR" "$VERSION" << 'REMOTE'
REPO_DIR="$1"
INSTALL_DIR="$2"

# Step 1: Pull latest code
echo "[1/3] Pulling latest code..."
cd "$REPO_DIR" && git pull --ff-only
sudo cp pi/towerwatch.py pi/config.py pi/loki.py pi/grafana.py \
    pi/events.py pi/scheduling.py pi/startup.py pi/lifecycle.py pi/tick.py \
    pi/version.txt "$INSTALL_DIR/"
sudo cp -r pi/probes "$INSTALL_DIR/"
sudo chown towerwatch:towerwatch \
    "$INSTALL_DIR/towerwatch.py" "$INSTALL_DIR/config.py" \
    "$INSTALL_DIR/loki.py" "$INSTALL_DIR/grafana.py" \
    "$INSTALL_DIR/events.py" "$INSTALL_DIR/scheduling.py" \
    "$INSTALL_DIR/startup.py" "$INSTALL_DIR/lifecycle.py" \
    "$INSTALL_DIR/tick.py" "$INSTALL_DIR/version.txt"
echo "  Files copied to $INSTALL_DIR (version: $(cat $INSTALL_DIR/version.txt))"

# Copy credentials if we uploaded them
if [[ -f /tmp/towerwatch-credentials.py ]]; then
    sudo mv /tmp/towerwatch-credentials.py "$INSTALL_DIR/credentials.py"
    sudo chown towerwatch:towerwatch "$INSTALL_DIR/credentials.py"
fi

# Step 2: Restart service
echo "[2/3] Restarting towerwatch service..."
sudo systemctl restart towerwatch

# Step 3: Verify
echo "[3/3] Verifying..."
sleep 10
if ! systemctl is-active --quiet towerwatch; then
    echo "=== ERROR: towerwatch failed to start ==="
    journalctl -u towerwatch --no-pager -n 30
    exit 1
fi
echo "=== Deploy OK — towerwatch is running ==="
journalctl -u towerwatch --no-pager -n 5
REMOTE
