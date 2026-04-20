"""Test loading and secrets loading helpers."""

import importlib
import sys
from pathlib import Path


def load_secrets():
    """Load secrets from /opt/towerwatch or pi/ directory.
    
    Priority: /opt/towerwatch (Pi install, authoritative) → pi/ sibling (dev repo)
    pi.secrets (repo import) is NOT tried first — the repo may have a stale copy
    with empty GRAFANA_ANNOTATION_TOKEN from initial setup.
    """
    for candidate in [
        Path("/opt/towerwatch"),               # Pi install path (authoritative)
        Path(__file__).resolve().parents[2],   # pi/bench/../.. = repo root, then pi/
    ]:
        pi_dir = candidate / "pi" if candidate == Path(__file__).resolve().parents[2] else candidate
        if not (pi_dir / "secrets.py").exists():
            continue
        sys.path.insert(0, str(pi_dir))
        try:
            import secrets as s
            return s
        except ImportError:
            sys.path.pop(0)
    print("ERROR: secrets.py not found. Copy secrets.py.example → secrets.py and fill values.")
    sys.exit(1)


def load_tests(test_classes_list: list[type], skip: list[str] = None) -> list[type]:
    """Load test classes, optionally filtering by skip list.
    
    Args:
        test_classes_list: List of test class types (pre-imported, not module paths)
        skip: Optional list of test names to skip
    
    Returns:
        List of test classes, excluding skipped ones
    """
    tests = []
    skip_set = set(skip or [])
    for cls in test_classes_list:
        if cls.name in skip_set:
            continue
        tests.append(cls)
    return tests
