#!/bin/bash
# =============================================================
# twq — Towerwatch Query. Ad-hoc read-only PromQL against Grafana Cloud.
# Run from your dev machine (or the Pi) with credentials.py present.
#
# Reads GRAFANA_INSTANCE_ID + GRAFANA_READ_KEY from src/towerwatch/credentials.py
# and queries the Grafana Cloud Prometheus HTTP API with Basic auth.
#
# NOTE on auth (learned the hard way — do not "fix"):
#   - Reads use GRAFANA_READ_KEY (a read-scoped glc_ token) with BASIC auth
#     (instance_id:read_key) against the prometheus-prod-NN host's
#     /api/prom/api/v1/query[_range] endpoint.
#   - GRAFANA_API_KEY is PUSH-only (401s on read). The stack datasource-proxy
#     (towerwatch.grafana.net/api/datasources/proxy/...) also 401s with these
#     tokens — the /api/prom path is the one that works.
#
# Usage:
#   ./scripts/twq.sh '<promql>'                       # instant query
#   ./scripts/twq.sh '<promql>' <lookback_s> <step>   # range query
#     e.g. ./scripts/twq.sh 'towerwatch_connected{host="standstill"}'
#          ./scripts/twq.sh 'rate(towerwatch_phone_nr_flap_events_total{host="standstill-phone"}[1h])*3600' 21600 300
#
# Output is raw JSON — pipe to `python -m json.tool` or jq to read it.
# =============================================================
set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "usage: $0 '<promql>' [lookback_seconds step]" >&2
    exit 2
fi

QUERY="$1"
HOST="https://prometheus-prod-67-prod-us-west-0.grafana.net"

# Pull creds from credentials.py without printing them. cd to the repo root and
# use a RELATIVE sys.path entry ('src') — an MSYS-style absolute path (/c/...)
# is not a valid sys.path entry for native Windows Python.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
eval "$(cd "${REPO_ROOT}" && python -c "
import sys
sys.path.insert(0, 'src')
from towerwatch import credentials as c
print(f'ID={c.GRAFANA_INSTANCE_ID}; RK={c.GRAFANA_READ_KEY}')
")"

if [[ $# -ge 3 ]]; then
    # Range query.
    END="$(date +%s)"
    START="$((END - $2))"
    curl -s -u "${ID}:${RK}" \
        --data-urlencode "query=${QUERY}" \
        --data-urlencode "start=${START}" \
        --data-urlencode "end=${END}" \
        --data-urlencode "step=$3" \
        -G "${HOST}/api/prom/api/v1/query_range"
else
    # Instant query.
    curl -s -u "${ID}:${RK}" \
        --data-urlencode "query=${QUERY}" \
        -G "${HOST}/api/prom/api/v1/query"
fi
echo
