"""
Shared pytest setup for towerwatch unit tests.

This conftest does ONE thing: ensures the stubs/ directory and pi/ are on
sys.path so `import credentials` resolves to the stub and `import config`
resolves to the real module. It does NOT monkeypatch config — tests that
need isolated paths should inject `tmp_path` directly via the refactored
seams (FakeClock, tmp_path fixture, explicit marker_file= kwargs, etc.).
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PI_DIR = _REPO_ROOT / "pi"
_STUBS_DIR = Path(__file__).resolve().parent / "stubs"


def _ensure_paths():
    # stubs first so `credentials` stub shadows any real credentials.py
    for p in [str(_STUBS_DIR), str(_PI_DIR)]:
        if p not in sys.path:
            sys.path.insert(0, p)


_ensure_paths()
