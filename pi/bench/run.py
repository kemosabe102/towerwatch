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
import glob
import importlib
import json
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
from pi.bench.harness.service import daemon_reload, service_active
from pi.bench.harness.state import (
    arm_sentinel, clear_sentinel, clear_state,
    read_sentinel, read_state, sentinel_present, write_state,
    BENCH_DIR,
)
from pi.bench.loader import load_secrets, load_tests
from pi.bench.preflight import preflight_check

# ---------------------------------------------------------------------------
# Test discovery — glob-based with ORDER sorting
# ---------------------------------------------------------------------------

def _discover_tests() -> list[type]:
    """Discover test modules via glob, sorted by ORDER constant.
    
    Returns list of test classes sorted by their module-level ORDER constant.
    """
    tests_dir = Path(__file__).resolve().parent / "tests"
    test_files = sorted(glob.glob(str(tests_dir / "test_*.py")))
    
    test_classes_with_order = []
    for test_file in test_files:
        # Convert path to module name: /path/to/test_foo.py → pi.bench.tests.test_foo
        module_name = f"pi.bench.tests.{Path(test_file).stem}"
        try:
            mod = importlib.import_module(module_name)
            cls = getattr(mod, "Test", None)
            if cls is None:
                print(f"WARNING: {module_name} has no 'Test' class, skipping")
                continue
            order = getattr(mod, "ORDER", 999)
            test_classes_with_order.append((order, cls))
        except ImportError as e:
            print(f"WARNING: Could not import {module_name}: {e}")
            continue
    
    # Sort by ORDER constant (default 999 if missing)
    test_classes_with_order.sort(key=lambda x: x[0])
    return [cls for _, cls in test_classes_with_order]


def _make_observer(secrets) -> GrafanaObserver:
    from pi.config import GRAFANA_ANNOTATIONS_URL
    stack_base = GRAFANA_ANNOTATIONS_URL.replace("/api/annotations", "")
    return GrafanaObserver(
        stack_base_url=stack_base,
        api_key=getattr(secrets, "GRAFANA_ANNOTATION_TOKEN", secrets.GRAFANA_API_KEY),
        annotation_token=getattr(secrets, "GRAFANA_ANNOTATION_TOKEN", ""),
    )


def cmd_list(args, test_classes):
    """List available tests."""
    filtered_tests = load_tests(test_classes, skip=args.skip)
    print(f"{'Name':<45} {'Timeout':>8}  {'Notes'}")
    print("-" * 70)
    for cls in filtered_tests:
        notes = "XFAIL" if cls.expected_failure else ""
        print(f"{cls.name:<45} {cls.timeout_s:>7}s  {notes}")


def cmd_restore(args):
    """Restore iptables, drop-ins, and qdisc from bench state."""
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
    daemon_reload(check=False)
    # Remove netem qdisc
    subprocess.run(["tc", "qdisc", "del", "dev", "eth0", "root"], check=False,
                   capture_output=True)
    # Re-enable NTP
    subprocess.run(["timedatectl", "set-ntp", "true"], check=False)
    clear_sentinel()
    clear_state()
    print("Restore complete. Sentinel cleared.")


def cmd_run_tests(test_classes, run_id, secrets, args):
    """Run tests, respecting skip list and --resume flag."""
    observer = _make_observer(secrets)
    if not getattr(args, "resume", False):
        preflight_check(observer)
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


def cmd_note(args, run_id):
    """Attach a note to the last report file."""
    report_files = sorted((BENCH_DIR / "reports").glob("report_*.json"), key=lambda p: p.stat().st_mtime)
    if not report_files:
        print("No report found to attach note to.")
        return
    latest = report_files[-1]
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

    # Discover tests once at startup
    all_test_classes = _discover_tests()

    if args.list:
        cmd_list(args, all_test_classes)
        return

    if args.restore:
        cmd_restore(args)
        return

    secrets = load_secrets()

    if args.note:
        run_id = uuid.uuid4().hex[:8]
        cmd_note(args, run_id)
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
        test_classes = load_tests(all_test_classes, skip=skip)
    else:
        test_classes = load_tests(all_test_classes, skip=skip)
        test_classes = [cls for cls in test_classes if cls.name == args.test]
        if not test_classes:
            print(f"ERROR: No test named '{args.test}'. Use --list to see available tests.")
            sys.exit(1)

    sys.exit(cmd_run_tests(test_classes, run_id, secrets, args))


if __name__ == "__main__":
    main()
