"""
`towerwatch-speedtest` — console entry point for manually triggering an Ookla
speedtest and pushing results to Grafana Cloud (metric + Loki event).

Designed to be run over SSH by a remote operator on the Tailnet. Results are
tagged with the local `LOCATION` (from credentials.py) and the `--triggered-by`
operator name so the dashboard can attribute each run.

Usage:
    towerwatch-speedtest                      # triggered_by = $SUDO_USER or $USER
    towerwatch-speedtest --triggered-by alice
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from towerwatch import config
from towerwatch.clients import grafana as grafana_mod
from towerwatch.clients import loki as loki_mod
from towerwatch.probes.ookla import run_speedtest
from towerwatch.tick import format_speedtest_line

log = logging.getLogger("towerwatch")


def _default_operator() -> str:
    return os.environ.get("SUDO_USER") or os.environ.get("USER") or "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="towerwatch-speedtest",
        description="Run an Ookla speedtest and push results to Grafana Cloud.",
    )
    parser.add_argument(
        "--triggered-by",
        default=_default_operator(),
        help="Operator name recorded with the result (default: $SUDO_USER or $USER).",
    )
    args = parser.parse_args(argv)
    triggered_by = args.triggered_by.strip() or "unknown"

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    try:
        from towerwatch import credentials
    except ImportError:
        print(
            "ERROR: credentials.py not found. "
            "Copy credentials.py.example to credentials.py and fill in values.",
            file=sys.stderr,
        )
        return 2

    location = config.INFLUX_HOST_TAG
    print(
        f"Running Ookla speedtest on {location!r} (triggered by {triggered_by!r}).\n"
        f"This takes ~60s and uses ~400 MB of data.",
        flush=True,
    )

    loki = loki_mod.LokiClient.from_config(config, credentials)
    grafana = grafana_mod.GrafanaClient.from_config(config, credentials)

    result = run_speedtest(loki=loki, triggered_by=triggered_by)

    if not result.get("success"):
        print("Speedtest FAILED — see logs.", file=sys.stderr)
        _flush(loki)
        return 1

    dl = result["download_mbps"]
    ul = result["upload_mbps"]
    ts = int(time.time())
    line = format_speedtest_line(
        ts,
        download_mbps=dl,
        upload_mbps=ul,
        triggered_by=triggered_by,
    )
    pushed = grafana.push_metrics([line])
    _flush(loki)

    print(f"Download: {dl} Mbps")
    print(f"Upload:   {ul} Mbps")
    print(f"Location: {location}")
    if not pushed:
        print("WARNING: result printed but metric push failed.", file=sys.stderr)
        return 1
    return 0


def _flush(loki) -> None:
    try:
        loki.flush()
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
