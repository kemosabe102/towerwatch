"""Tee logger: writes to stdout, a local log file, and Loki job="towerwatch-bench".

Token scrubbing: Authorization headers are never written to any output.
"""

import json
import sys
import time
from pathlib import Path

import requests

from .state import BENCH_DIR


def _scrub(text: str) -> str:
    import re
    return re.sub(r'(Authorization:\s*Bearer\s*)\S+', r'\1[REDACTED]', text, flags=re.IGNORECASE)


class BenchLogger:
    def __init__(self, run_id: str, loki_url: str | None, loki_user: str | None, loki_token: str | None):
        self.run_id = run_id
        self.loki_url = loki_url
        self.loki_user = loki_user
        self.loki_token = loki_token
        BENCH_DIR.mkdir(parents=True, exist_ok=True)
        self._log_path = BENCH_DIR / f"run_{run_id}.log"
        self._fh = self._log_path.open("a", encoding="utf-8")

    def _write_local(self, level: str, msg: str, **extra) -> None:
        entry = json.dumps({"ts": time.time(), "level": level, "msg": _scrub(msg), **extra})
        print(entry, flush=True)
        self._fh.write(entry + "\n")
        self._fh.flush()

    def _push_loki(self, level: str, event: str, msg: str, **extra) -> None:
        if not self.loki_url:
            return
        ns = str(int(time.time() * 1e9))
        payload = {
            "streams": [{
                "stream": {"job": "towerwatch-bench", "run_id": self.run_id, "level": level},
                "values": [[ns, json.dumps({"event": event, "msg": _scrub(msg), **extra})]],
            }]
        }
        try:
            requests.post(
                self.loki_url,
                json=payload,
                auth=(self.loki_user, self.loki_token) if self.loki_user else None,
                timeout=5,
            )
        except Exception:
            pass  # Loki push is fire-and-forget; don't crash the harness

    def info(self, msg: str, event: str = "bench_info", **extra) -> None:
        self._write_local("INFO", msg, event=event, **extra)
        self._push_loki("INFO", event, msg, **extra)

    def warn(self, msg: str, event: str = "bench_warn", **extra) -> None:
        self._write_local("WARN", msg, event=event, **extra)
        self._push_loki("WARN", event, msg, **extra)

    def error(self, msg: str, event: str = "bench_error", **extra) -> None:
        self._write_local("ERROR", msg, event=event, **extra)
        self._push_loki("ERROR", event, msg, **extra)

    def close(self) -> None:
        self._fh.close()

    @property
    def log_path(self) -> Path:
        return self._log_path
