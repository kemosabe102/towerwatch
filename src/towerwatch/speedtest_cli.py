"""
`towerwatch-speedtest` — console entry point for manually triggering an Ookla
speedtest and pushing results to Grafana Cloud (metric + Loki event).

Designed to be invoked by `sshd`'s ForceCommand when a remote operator on the
Tailnet logs in as `towerwatch-user`. Output is intentionally minimal — the
operator sees only Started/Success/Failed; the actual numbers land on the
Grafana dashboard tagged with the connecting Tailscale identity.

Operator name is auto-detected from `tailscale whois $SSH_CLIENT_IP`. The
`--triggered-by` flag remains as a hidden override for local dev/testing.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time

from towerwatch import config
from towerwatch.clients import grafana as grafana_mod
from towerwatch.clients import loki as loki_mod
from towerwatch.probes.ookla import run_speedtest
from towerwatch.tick import format_speedtest_line

log = logging.getLogger("towerwatch")


def _ssh_peer_ip() -> str | None:
    """Return the connecting client IP from sshd-set env vars, or None.

    sshd sets SSH_CLIENT="<peer-ip> <peer-port> <local-port>" on every session.
    SSH_CONNECTION="<peer-ip> <peer-port> <local-ip> <local-port>" as a fallback.
    """
    for var in ("SSH_CLIENT", "SSH_CONNECTION"):
        v = os.environ.get(var, "").strip()
        if v:
            parts = v.split()
            if parts:
                return parts[0]
    return None


def _tailscale_whois(peer_ip: str, *, run=subprocess.run) -> str | None:
    """Resolve a peer IP to a Tailscale user identity, or None on any failure.

    Uses `tailscale whois --json <ip>` which works for any local user (no sudo).
    Returns the value of UserProfile.LoginName, e.g. "alice@example.com".
    """
    if not shutil.which("tailscale"):
        return None
    try:
        proc = run(
            ["tailscale", "whois", "--json", peer_ip],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    user = data.get("UserProfile") or {}
    login = user.get("LoginName")
    return login.strip() if isinstance(login, str) and login.strip() else None


def _resolve_operator(*, run=subprocess.run) -> str:
    """Best-guess operator identity for this invocation.

    Resolution order:
      1. Tailscale whois against the SSH peer IP — the happy path for remote runs.
      2. $SUDO_USER / $USER — covers manual local invocation.
      3. "unknown" — never crashes the run on identity resolution.
    """
    peer = _ssh_peer_ip()
    if peer:
        who = _tailscale_whois(peer, run=run)
        if who:
            return who
    return os.environ.get("SUDO_USER") or os.environ.get("USER") or "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="towerwatch-speedtest",
        description="Run an Ookla speedtest and push results to Grafana Cloud.",
    )
    # Hidden override: the user-facing path auto-detects via tailscale whois.
    # Kept for local dev/testing where there's no SSH session.
    parser.add_argument(
        "--triggered-by",
        default=None,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    triggered_by = (args.triggered_by or _resolve_operator()).strip() or "unknown"

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    try:
        from towerwatch import credentials
    except ImportError:
        print("Speedtest FAILED — credentials missing. Contact the operator.", file=sys.stderr)
        return 2

    location = config.INFLUX_HOST_TAG
    print(
        f"Speedtest started on {location}... (takes ~60s, uses ~400 MB)",
        flush=True,
    )

    loki = loki_mod.LokiClient.from_config(config, credentials)
    grafana = grafana_mod.GrafanaClient.from_config(config, credentials)

    result = run_speedtest(loki=loki, triggered_by=triggered_by)

    if not result.get("success"):
        _flush(loki)
        print("✗ Failed — contact the operator.", file=sys.stderr)
        return 1

    ts = int(time.time())
    line = format_speedtest_line(
        ts,
        download_mbps=result["download_mbps"],
        upload_mbps=result["upload_mbps"],
        triggered_by=triggered_by,
    )
    pushed = grafana.push_metrics([line])
    _flush(loki)

    if not pushed:
        # The speedtest ran but the metric never reached Grafana — from the
        # remote user's perspective, the run is a failure (nothing on dashboard).
        # Operator can disambiguate via Loki.
        print("✗ Failed — contact the operator.", file=sys.stderr)
        return 1

    print("✓ Success — results will appear on the Grafana dashboard within a minute.")
    return 0


def _flush(loki) -> None:
    try:
        loki.flush()
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
