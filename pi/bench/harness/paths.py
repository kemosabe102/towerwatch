"""Centralized path constants for the bench harness.

All paths that appear in more than one file live here.
"""

import sys
from pathlib import Path

if sys.platform == "win32":
    DATA_ROOT = Path("./data")
else:
    DATA_ROOT = Path("/opt/towerwatch/data")

BUFFER_FILE = DATA_ROOT / "buffer" / "loki.jsonl"
DATA_MOUNT = str(DATA_ROOT)
DROPIN_DIR = Path("/etc/systemd/system/towerwatch.service.d")
