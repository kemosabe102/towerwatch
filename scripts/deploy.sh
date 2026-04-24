#!/bin/bash
# =============================================================
# Towerwatch deploy script — push the current branch to a remote Pi.
# Run from your dev machine after ci.sh succeeds.
#
# Usage:
#   ./scripts/deploy.sh <user@host>
#   e.g. ./scripts/deploy.sh admin@towerwatch.local
#        ./scripts/deploy.sh admin@100.76.154.81   (Tailscale)
#
# Requires: src/towerwatch/_version.txt stamped by ci.sh (refuses to deploy
# if stale — a .py file newer than the stamp means CI hasn't seen the change).
#
# Assumes one-time setup already ran on the Pi (scripts/install-pi.sh),
# which created /opt/towerwatch, /opt/towerwatch/.venv, the towerwatch user,
# and the systemd unit pointing at .venv/bin/towerwatch.
# =============================================================
set -euo pipefail

PI_HOST="${1:?Usage: deploy.sh <user@host>}"

REPO_DIR="~/towerwatch"
INSTALL_DIR="/opt/towerwatch"
STAMP="src/towerwatch/_version.txt"

echo "=== Towerwatch Deploy to $PI_HOST ==="

# Guard: stamp must exist and not be stale relative to any .py under src/.
if [[ ! -s "$STAMP" ]]; then
    echo "ERROR: $STAMP is missing or empty. Run ./ci.sh first."
    exit 1
fi

VERSION_TS=$(date -r "$STAMP" +%s 2>/dev/null || stat -c %Y "$STAMP" 2>/dev/null || echo 0)
while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    F_TS=$(date -r "$f" +%s 2>/dev/null || stat -c %Y "$f" 2>/dev/null || echo 0)
    if [[ "$F_TS" -gt "$VERSION_TS" ]]; then
        echo "ERROR: $f is newer than $STAMP — re-run ./ci.sh."
        exit 1
    fi
done < <(find src -name '*.py' -type f)

VERSION="$(cat "$STAMP")"
echo "    Version: $VERSION"

# Upload stamp (so it's inside the git tree on the Pi before `pip install`),
# and upload credentials.py (gitignored).
echo "[0/3] Uploading stamp and credentials..."
scp "$STAMP" "$PI_HOST:$REPO_DIR/$STAMP"
if [[ -f src/towerwatch/credentials.py ]]; then
    scp src/towerwatch/credentials.py "$PI_HOST:/tmp/towerwatch-credentials.py"
fi

# Steps 1–3 run on the Pi
ssh "$PI_HOST" bash -s "$REPO_DIR" "$INSTALL_DIR" "$VERSION" << 'REMOTE'
set -euo pipefail
REPO_DIR="$1"
INSTALL_DIR="$2"

# 1. Pull latest and install into the existing venv
echo "[1/3] git checkout main && git pull && pip install ."
cd "$REPO_DIR"
git fetch origin
# Pin the Pi to `main`. Previously this script pulled the Pi's current branch,
# which silently left the Pi on whatever feature branch was last checked out —
# deploys appeared to succeed while installing stale code. Pin explicitly.
git checkout main
git pull --ff-only origin main
# Move any uploaded credentials into place before install (package needs it at import time).
if [[ -f /tmp/towerwatch-credentials.py ]]; then
    cp /tmp/towerwatch-credentials.py "$REPO_DIR/src/towerwatch/credentials.py"
    rm -f /tmp/towerwatch-credentials.py
fi
# Install into the production venv. `--force-reinstall --no-deps` is required
# because pyproject.toml has a static version (0.1.0); without it pip sees
# "same version already installed" and skips copying updated .py files. We run
# pip as root because the checked-out repo lives under /home/admin (not
# readable by the towerwatch user), then restore venv ownership.
# install-pi.sh uses the same pattern.
sudo "$INSTALL_DIR/.venv/bin/python" -m pip install --quiet --force-reinstall --no-deps "$REPO_DIR"

# Gitignored files (credentials, version stamp) don't ship in the wheel —
# copy them into the installed package dir post-install.
SITE_PKG="$(ls -d "$INSTALL_DIR"/.venv/lib/python*/site-packages/towerwatch 2>/dev/null | head -1)"
if [ -n "$SITE_PKG" ]; then
    if [ -f "$REPO_DIR/src/towerwatch/credentials.py" ]; then
        sudo cp "$REPO_DIR/src/towerwatch/credentials.py" "$SITE_PKG/credentials.py"
        sudo chmod 600 "$SITE_PKG/credentials.py"
    fi
    if [ -f "$REPO_DIR/src/towerwatch/_version.txt" ]; then
        sudo cp "$REPO_DIR/src/towerwatch/_version.txt" "$SITE_PKG/_version.txt"
    fi
fi
sudo chown -R towerwatch:towerwatch "$INSTALL_DIR"

# 2. Restart
echo "[2/3] Restarting towerwatch service..."
sudo systemctl restart towerwatch

# 3. Verify
echo "[3/3] Verifying..."
sleep 10
if ! systemctl is-active --quiet towerwatch; then
    echo "=== ERROR: towerwatch failed to start ==="
    sudo journalctl -u towerwatch --no-pager -n 40
    exit 1
fi
echo "=== Deploy OK — towerwatch is running ==="
sudo journalctl -u towerwatch --no-pager -n 8
REMOTE
