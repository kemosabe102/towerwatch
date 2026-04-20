"""Preflight checks before running test suites."""

import subprocess
import sys

from pi.bench.harness.observe import GrafanaObserver, ObserveError
from pi.bench.harness.service import service_active


def preflight_check(observer: GrafanaObserver) -> None:
    """Verify service is active and Grafana is reachable.
    
    Args:
        observer: GrafanaObserver instance
    
    Raises:
        SystemExit: If any check fails
    """
    print("Preflight: checking towerwatch service is active...")
    active = service_active()
    if not active:
        r = subprocess.run(["systemctl", "is-active", "towerwatch"],
                           capture_output=True, text=True)
        print(f"ERROR: towerwatch service is not active ({r.stdout.strip()})")
        sys.exit(1)

    print("Preflight: verifying Grafana read access...")
    try:
        observer._resolve_ds_uids()
    except ObserveError as e:
        print(f"ERROR: Grafana preflight failed: {e}")
        sys.exit(1)

    print("Preflight: OK")
