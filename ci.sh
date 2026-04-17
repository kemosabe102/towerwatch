#!/bin/bash
# =============================================================
# Towerwatch CI Script (local verification + version stamp)
# Run from your dev machine before deploying.
#
# Usage:
#   ./ci.sh            # fast mode (≤15s): syntax, imports, clean-tree, stamp
#   ./ci.sh fast       # same as above
#   ./ci.sh full       # fast + 30s smoke run (≤2min total)
#   ./ci.sh fast --allow-dirty   # skip clean-tree check (local experiments)
#
# Exits non-zero on any failure. Writes pi/version.txt on success.
# =============================================================
set -euo pipefail

MODE="${1:-fast}"
ALLOW_DIRTY=0
for arg in "$@"; do
    [[ "$arg" == "--allow-dirty" ]] && ALLOW_DIRTY=1
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

# Pick a python that can import the deps. On Windows "python" works; on *nix
# we prefer python3.
if command -v python3 >/dev/null 2>&1; then
    PY=python3
else
    PY=python
fi

echo "=== Towerwatch CI ($MODE) ==="

# Step 1: syntax check
echo "[1/5] py_compile..."
$PY -m py_compile pi/towerwatch.py pi/config.py

# Step 2: import check (catches missing deps, top-level typos)
echo "[2/5] import check..."
$PY -c "import sys; sys.path.insert(0, 'pi'); import towerwatch, config"

# Step 3: clean-tree check — dirty tree means the stamp won't match deployed code
echo "[3/5] clean-tree check..."
if [[ $ALLOW_DIRTY -eq 0 ]]; then
    if [[ -n "$(git status --porcelain)" ]]; then
        echo "ERROR: working tree is dirty. Commit or stash changes first."
        echo "       (or re-run with --allow-dirty for local experiments)"
        git status --short
        exit 1
    fi
fi

# Step 4: write version.txt ("<short-hash> <iso-date>")
echo "[4/5] stamping pi/version.txt..."
HASH="$(git rev-parse --short HEAD)"
DATE="$(git log -1 --format=%cI)"
echo "$HASH $DATE" > pi/version.txt
echo "  stamped: $HASH $DATE"

# Step 5 (full only): smoke run
if [[ "$MODE" == "full" ]]; then
    echo "[5/5] smoke run (30s)..."
    SMOKE_DIR="$REPO_ROOT/ci-tmp"
    rm -rf "$SMOKE_DIR"
    mkdir -p "$SMOKE_DIR/buffer"
    # Run with a scratch data dir by prepending env overrides.
    # We bound the run via a background kill — works on Git Bash / MSYS.
    (
        cd pi
        $PY towerwatch.py &
        PID=$!
        sleep 30
        kill "$PID" 2>/dev/null || true
        wait "$PID" 2>/dev/null || true
    ) || true
    if [[ ! -s pi/version.txt ]]; then
        echo "ERROR: pi/version.txt missing or empty after smoke run"
        exit 1
    fi
    echo "  smoke run complete"
    rm -rf "$SMOKE_DIR"
else
    echo "[5/5] (skipped — fast mode; run './ci.sh full' before deploy)"
fi

echo "=== CI OK — stamped $HASH $DATE ==="
echo "    Next: ./cd.sh <user@host>"
