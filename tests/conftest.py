"""Shared pytest setup for towerwatch unit tests.

1. Puts src/ on sys.path so `from towerwatch import ...` resolves.
2. Materializes a test credentials file at src/towerwatch/credentials.py
   if none is present (CI and fresh checkouts). When a real credentials.py
   exists locally, it's left alone.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parents[1] / "src"
_CREDS = _SRC_DIR / "towerwatch" / "credentials.py"
_CREDS_STUB_CONTENT = (Path(__file__).resolve().parent / "stubs" / "credentials.py").read_text(
    encoding="utf-8"
)


def _ensure_paths() -> None:
    if str(_SRC_DIR) not in sys.path:
        sys.path.insert(0, str(_SRC_DIR))


def _ensure_credentials() -> None:
    if not _CREDS.exists():
        _CREDS.write_text(_CREDS_STUB_CONTENT, encoding="utf-8")


_ensure_paths()
_ensure_credentials()
