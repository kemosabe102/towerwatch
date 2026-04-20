"""
Loki log shipping — direct HTTP push, fire-and-forget with local buffering on failure.
"""

import json
import logging
import os
import time
from pathlib import Path

import requests

import config

try:
    import secrets
except ImportError:
    raise ImportError("secrets.py not found. Copy secrets.py.example to secrets.py and fill in values.")

log = logging.getLogger("towerwatch")

_LOG_LEVELS = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}


def _build_loki_payload(level: str, message: str, extra: dict = None) -> dict:
    """Build a Loki push payload dict."""
    return {
        "streams": [{
            "stream": {
                "job": "towerwatch",
                "host": config.INFLUX_HOST_TAG,
                "level": level.lower(),
            },
            "values": [[
                str(int(time.time() * 1e9)),
                json.dumps({"msg": message, **(extra or {})}),
            ]],
        }]
    }


def _buffer_log_entry(payload: dict):
    """Append a Loki payload (as JSON line) to the log buffer file. fsync'd."""
    buf = Path(config.LOKI_BUFFER_FILE)
    buf.parent.mkdir(parents=True, exist_ok=True)
    if buf.exists() and buf.stat().st_size >= config.LOKI_BUFFER_MAX_BYTES:
        lines = buf.read_text(encoding="utf-8").splitlines()
        keep = lines[max(1, len(lines) // 10):]
        buf.write_text("\n".join(keep) + "\n", encoding="utf-8")
    with open(buf, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _post_loki(payload: dict) -> None:
    """POST a single Loki payload. Raises on network/HTTP error."""
    requests.post(
        secrets.LOKI_URL,
        json=payload,
        auth=(secrets.LOKI_USER, secrets.LOKI_TOKEN),
        timeout=config.LOKI_PUSH_TIMEOUT_S,
    )


def push_log(level: str, message: str, extra: dict = None):
    """Push a structured log entry to Grafana Cloud Loki. Buffers to disk on failure."""
    if _LOG_LEVELS.get(level, 0) < _LOG_LEVELS.get(config.LOKI_PUSH_LEVEL, 1):
        return
    if not getattr(secrets, "LOKI_URL", None):
        return
    payload = _build_loki_payload(level, message, extra)
    try:
        _post_loki(payload)
    except Exception:
        try:
            _buffer_log_entry(payload)
        except Exception:
            pass  # Buffer write failure must never crash the monitor


def log_and_push(level: str, message: str, **fields) -> None:
    """Log locally and push to Loki in one call. level is the Loki level (INFO/WARN/ERROR)."""
    _LOG_FN = {"INFO": log.info, "WARN": log.warning, "ERROR": log.error}
    _LOG_FN.get(level, log.warning)(message)
    push_log(level, message, fields if fields else None)
