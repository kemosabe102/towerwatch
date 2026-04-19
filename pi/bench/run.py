#!/usr/bin/env python3
"""Towerwatch bench harness entry point.

Usage:
  python run.py --list
  python run.py --test <name> [--skip <name> ...]
  python run.py --all [--skip <name> ...]
  python run.py --resume
  python run.py --restore
  python run.py --note "free-form text"
"""

import argparse
import importlib
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

# Allow running from repo root or from pi/bench/
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pi.bench.harness.logger import BenchLogger
from pi.bench.harness.observe import GrafanaObserver, ObserveError
from pi.bench.harness.report import Report
from pi.bench.harness.state import (
    arm_sentinel, clear_sentinel, clear_state,
    read_sentinel, read_state, sentinel_present, write_state,
    BENCH_DIR,
)

# ---------------------------------------------------------------------------
# Test registry — import order determines --all run order
# ---------------------------------------------------------------------------
TEST_MODULES = [
    "pi.bench.tests.test_service_lifecycle",         # 9: lowest-risk, smoke first
    "pi.bench.tests.test_latency_injection",         # 13
    "pi.bench.tests.test_probe_targets_down",        # 10
    "pi.bench.tests.test_dns_only_outage",           # 12
    "pi.bench.tests.test_partial_network",           # 2
    "pi.bench.tests.test_prom_5xx",                  # 3
    "pi.bench.tests.test_loki_429",                  # 4 (expected-failure)
    "pi.bench.tests.test_config_drift",              # 11
    "pi.bench.tests.test_buffer_cap_and_corrupt",    # 5
    "pi.bench.tests.test_readonly_data_partition",   # 6
    "pi.bench.tests.test_clock_skew_forward",        # 7
    "pi.bench.tests.test_clock_skew_backward",       # 8 (expected-failure)
    "pi.bench.tests.test_full_network_loss",         # 1 (longest — needs annotation)
    "pi.bench.tests.test_reboot_survival",           # 14 (run separately)
]


def _load_secrets():
    try:
        import pi.secrets as s
        return s
    except ImportError:
        pass
    # Fallback: secrets.py in the pi/ subdirectory (dev: ~/towerwatch/pi/secrets.py)
    for candidate in [
        Path(__file__).resolve().parents[1],   # pi/bench/../ = pi/
        Path("/opt/towerwatch"),               # Pi install path
    ]:
        sys.path.insert(0, str(candidate))
        try:
            import secrets as s
            return s
        except ImportError:
            sys.path.pop(0)
    print("ERROR: secrets.py not found. Copy secrets.py.example → secrets.py and fill values.")
    sys.exit(1)


def _load_tests(skip: list[str] = None) -> list:
    tests = []
    skip = set(skip or [])
    for module_path in TEST_MODULES:
        mod = importlib.import_module(module_path)
        cls = getattr(mod, "Test", None)
        if cls is None:
            print(f"WARNING: {module_path} has no 'Test' class, skipping")
            continue
        if cls.name in skip:
            continue
        tests.append(cls)
    return tests


def _make_observer(secrets) -> GrafanaObserver:
    from pi.config import GRAFANA_ANNOTATIONS_URL
    stack_base = GRAFANA_ANNOTATIONS_URL.replace("/api/annotations", "")
    return GrafanaObserver(
        stack_base_url=stack_base,
        api_key=getattr(secrets, "GRAFANA_ANNOTATION_TOKEN", secrets.GRAFANA_API_KEY),
        annotation_token=getattr(secrets, "GRAFANA_ANNOTATION_TOKEN", ""),
    )


def _preflight(observer: GrafanaObserver) -> None:
    print("Preflight: checking towerwatch service is active...")
    r = subprocess.run(["systemctl", "is-active", "towerwatch"],
                       capture_output=True, text=True)
    if r.stdout.strip() != "active":
        print(f"ERROR: towerwatch service is not active ({r.stdout.strip()})")
        sys.exit(1)

    print("Preflight: verifying Grafana read access...")
    try:
        observer._resolve_ds_uids()
    except ObserveError as e:
        print(f"ERROR: Grafana preflight failed: {e}")
        sys.exit(1)

    print("Preflight: OK")

def cmd_list(args):
    tests = _load_tests()
    print(f"{'Name':<45} {'Timeout':>8}  {'Notes'}")
    print("-" * 70)
    for cls in tests:
        notes = "XFAIL" if cls.expected_failure else ""
        print(f"{cls.name:<45} {cls.timeout_s:>7}s  {notes}")


def cmd_restore(args):
    if not sentinel_present():
        print("No sentinel present — nothing to restore.")
        return
    info = read_sentinel()
    print(f"Sentinel found: {info}")
    print("Running restore-all sequence...")
    # Try to restore iptables from any snapshot we find
    snap_dir = BENCH_DIR / "snapshots"
    if snap_dir.exists():
        for rules_file in snap_dir.glob("*.rules"):
            print(f"  Restoring iptables from {rules_file.name}")
            try:
                with rules_file.open() as fh:
                    subprocess.run(["iptables-restore"], stdin=fh, check=True)
                break
            except Exception as e:
                print(f"  WARNING: {e}")
    # Remove any bench drop-ins
    dropin_dir = Path("/etc/systemd/system/towerwatch.service.d")
    for f in dropin_dir.glob("bench-*.conf") if dropin_dir.exists() else []:
        print(f"  Removing drop-in: {f.name}")
        f.unlink()
    subprocess.run(["systemctl", "daemon-reload"], check=False)
    # Remove netem qdisc
    subprocess.run(["tc", "qdisc", "del", "dev", "eth0", "root"], check=False,
                   capture_output=True)
    # Re-enable NTP
    subprocess.run(["timedatectl", "set-ntp", "true"], check=False)
    clear_sentinel()
    clear_state()
    print("Restore complete. Sentinel cleared.")


def cmd_run_tests(test_classes, run_id, secrets, args):
    observer = _make_observer(secrets)
    if not getattr(args, "resume", False):
        _preflight(observer)
    logger = BenchLogger(
        run_id=run_id,
        loki_url=getattr(secrets, "LOKI_URL", None),
        loki_user=getattr(secrets, "LOKI_USER", None),
        loki_token=getattr(secrets, "LOKI_TOKEN", None),
    )
    report = Report(run_id)
    arm_sentinel(run_id)
    try:
        for cls in test_classes:
            instance = cls(logger=logger, observer=observer)
            result = instance.run()
            report.add(result)
    finally:
        clear_sentinel()
        clear_state()
        logger.close()
    report.print_table()
    return 0 if not report.has_unexpected_failures else 1

def cmd_note(args, secrets, run_id):
    report_files = sorted((BENCH_DIR / "reports").glob("report_*.json"), key=lambda p: p.stat().st_mtime)
    if not report_files:
        print("No report found to attach note to.")
        return
    latest = report_files[-1]
    import json
    data = json.loads(latest.read_text())
    data.setdefault("results", []).append({
        "name": "__note__", "status": "note",
        "evidence": {"text": args.note}, "started_at": time.time(),
        "duration_s": 0, "expected_failure": False, "error_msg": None,
    })
    latest.write_text(json.dumps(data, indent=2))
    print(f"Note added to {latest.name}")


def main():
    parser = argparse.ArgumentParser(description="Towerwatch bench harness")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list",    action="store_true", help="List available tests")
    group.add_argument("--test",    metavar="NAME",      help="Run a single test by name")
    group.add_argument("--all",     action="store_true", help="Run all tests (except reboot_survival)")
    group.add_argument("--resume",  action="store_true", help="Resume after reboot")
    group.add_argument("--restore", action="store_true", help="Force-clear sentinel and restore state")
    group.add_argument("--note",    metavar="TEXT",      help="Attach a note to the last report")
    parser.add_argument("--skip", metavar="NAME", nargs="+", default=[], help="Skip named tests")
    args = parser.parse_args()

    if args.list:
        cmd_list(args)
        return

    if args.restore:
        cmd_restore(args)
        return

    secrets = _load_secrets()

    if args.note:
        run_id = uuid.uuid4().hex[:8]
        cmd_note(args, secrets, run_id)
        return

    if sentinel_present() and not args.resume:
        info = read_sentinel()
        print(f"ERROR: Sentinel present from run {info}.")
        print("Run '--restore' to clear it, or '--resume' if recovering from reboot.")
        sys.exit(1)

    run_id = uuid.uuid4().hex[:8]

    if args.resume:
        state = read_state()
        if state.get("phase") == "armed" and state.get("test") == "reboot_survival":
            from pi.bench.tests.test_reboot_survival import Test
            sys.exit(cmd_run_tests([Test], run_id, secrets, args))
        else:
            print("No reboot_survival state found for --resume.")
            sys.exit(1)

    skip = list(args.skip)
    if args.all:
        skip.append("reboot_survival")
        test_classes = _load_tests(skip=skip)
    else:
        test_classes = [cls for cls in _load_tests(skip=skip) if cls.name == args.test]
        if not test_classes:
            print(f"ERROR: No test named '{args.test}'. Use --list to see available tests.")
            sys.exit(1)

    sys.exit(cmd_run_tests(test_classes, run_id, secrets, args))


if __name__ == "__main__":
    main()
