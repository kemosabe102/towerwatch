#!/bin/bash
# =============================================================
# Towerwatch CI Script (local verification + version stamp)
# Run from your dev machine before deploying.
#
# Usage:
#   ./ci.sh            # fast mode: ruff, pyright, pytest, clean-tree, stamp
#   ./ci.sh fast       # same as above
#   ./ci.sh full       # fast + 30s smoke run
#   ./ci.sh fast --allow-dirty   # skip clean-tree check (local experiments)
#
# Exits non-zero on any failure. Writes src/towerwatch/_version.txt on success.
#
# The GitHub Actions workflow runs this same script (fast mode).
# =============================================================
set -euo pipefail

MODE="${1:-fast}"
ALLOW_DIRTY=0
for arg in "$@"; do
    [[ "$arg" == "--allow-dirty" ]] && ALLOW_DIRTY=1
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

PY="${PYTHON:-}"
if [[ -z "$PY" ]]; then
    if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi
fi

echo "=== Towerwatch CI ($MODE) ==="

# Guard: every tracked *.sh must have the executable bit set in the git index.
# Windows filesystems don't preserve +x, but the index does — Linux CI runners
# read the index mode, so a 100644 shell script fails with exit 126 there.
missing_x="$(git ls-files --stage '*.sh' | awk '$1 != "100755" {print $4}')"
if [[ -n "$missing_x" ]]; then
    echo "ERROR: these tracked *.sh files are not executable in the git index:"
    echo "$missing_x" | sed 's/^/  /'
    echo "Fix with: git update-index --chmod=+x <path>"
    exit 1
fi

# Ensure credentials.py exists (gitignored in real deployments; CI needs a stub
# so pyright can resolve `from towerwatch import credentials`). On dev machines
# a real credentials.py is already present — skip stubbing.
STUBBED_CREDS=0
if [[ ! -f src/towerwatch/credentials.py ]]; then
    cp src/towerwatch/credentials.py.example src/towerwatch/credentials.py
    STUBBED_CREDS=1
    echo "  (stubbed src/towerwatch/credentials.py from .example for CI)"
fi
# Always verify the .example itself is format-clean, so GitHub's stub step
# won't hit "Would reformat: credentials.py" on a hosted runner.
$PY -m ruff format --check src/towerwatch/credentials.py.example >/dev/null

# Step 1: ruff lint (replaces py_compile + import walk)
echo "[1/5] ruff check..."
$PY -m ruff check src tests

# Step 2: ruff format check
echo "[2/5] ruff format --check..."
$PY -m ruff format --check src tests

# Step 3: pyright (type check)
echo "[3/5] pyright..."
$PY -m pyright

# Step 4: pytest
echo "[4/5] pytest..."
$PY -m pytest -x -q

# Step 5a: clean-tree check
if [[ $ALLOW_DIRTY -eq 0 ]]; then
    if [[ -n "$(git status --porcelain)" ]]; then
        echo "ERROR: working tree is dirty. Commit or stash changes first."
        echo "       (or re-run with --allow-dirty for local experiments)"
        git status --short
        exit 1
    fi
fi

# Step 5b: stamp version file
echo "[5/5] stamping src/towerwatch/_version.txt..."
HASH="$(git rev-parse --short HEAD)"
DATE="$(git log -1 --format=%cI)"
echo "$HASH $DATE" > src/towerwatch/_version.txt
echo "  stamped: $HASH $DATE"

# Optional: smoke run
if [[ "$MODE" == "full" ]]; then
    echo "[smoke] boot python -m towerwatch for 30s..."
    (
        $PY -m towerwatch &
        PID=$!
        sleep 30
        kill "$PID" 2>/dev/null || true
        wait "$PID" 2>/dev/null || true
    ) || true
    echo "  smoke complete"
fi

echo "=== CI OK — stamped $HASH $DATE ==="
echo "    Next: ./scripts/deploy.sh <user@host>   (or ./cd.sh — shim)"
