"""
Loki log shipping — direct HTTP push, fire-and-forget with local buffering on failure.

The HTTP transport is an injectable `post_fn` callable so tests can drive
failures without patching `requests.post`.
"""

import json
import logging
import os
import time
from pathlib import Path

import requests

from towerwatch import config

try:
    from towerwatch import credentials
except ImportError:
    raise ImportError("credentials.py not found. Copy credentials.py.example to credentials.py and fill in values.")

log = logging.getLogger("towerwatch")

_LOG_LEVELS = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}


class LokiClient:
    def __init__(
        self,
        url: str,
        user: str,
        token: str,
        buffer_path: str,
        buffer_max_bytes: int,
        push_level: str = "WARN",
        session_factory=requests.Session,
        post_fn=requests.post,
        push_timeout: int = 5,
        host_tag: str = "towerwatch",
    ):
        self._url = url
        self._user = user
        self._token = token
        self._buffer_path = Path(buffer_path)
        self._buffer_max_bytes = buffer_max_bytes
        self._push_level = push_level
        self._session_factory = session_factory
        self._post_fn = post_fn
        self._push_timeout = push_timeout
        self._host_tag = host_tag

    @classmethod
    def from_config(cls, cfg, creds) -> "LokiClient":
        return cls(
            url=getattr(creds, "LOKI_URL", ""),
            user=getattr(creds, "LOKI_USER", ""),
            token=getattr(creds, "LOKI_TOKEN", ""),
            buffer_path=cfg.LOKI_BUFFER_FILE,
            buffer_max_bytes=cfg.LOKI_BUFFER_MAX_BYTES,
            push_level=cfg.LOKI_PUSH_LEVEL,
            push_timeout=cfg.LOKI_PUSH_TIMEOUT_S,
            host_tag=cfg.INFLUX_HOST_TAG,
        )

    def _build_payload(self, level: str, message: str, extra: dict = None) -> dict:
        return {
            "streams": [{
                "stream": {
                    "job": "towerwatch",
                    "host": self._host_tag,
                    "level": level.lower(),
                },
                "values": [[
                    str(int(time.time() * 1e9)),
                    json.dumps({"msg": message, **(extra or {})}),
                ]],
            }]
        }

    def _post(self, payload: dict) -> None:
        """POST a single Loki payload. Raises on network or HTTP error."""
        if not self._url:
            return
        resp = self._post_fn(
            self._url,
            json=payload,
            auth=(self._user, self._token),
            timeout=self._push_timeout,
        )
        if resp.status_code >= 300:
            raise requests.HTTPError(
                f"Loki returned {resp.status_code}", response=resp
            )

    def _buffer(self, payload: dict) -> None:
        """Append payload as a JSON line to the buffer file. fsync'd. Evicts oldest on overflow."""
        buf = self._buffer_path
        buf.parent.mkdir(parents=True, exist_ok=True)
        if buf.exists() and buf.stat().st_size >= self._buffer_max_bytes:
            lines = buf.read_text(encoding="utf-8").splitlines()
            keep = lines[max(1, len(lines) // 10):]
            buf.write_text("\n".join(keep) + "\n", encoding="utf-8")
        with open(buf, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def push(self, level: str, message: str, extra: dict = None) -> None:
        """Push a structured log entry. Filters by push_level; buffers to disk on failure."""
        if _LOG_LEVELS.get(level, 0) < _LOG_LEVELS.get(self._push_level, 1):
            return
        if not self._url:
            return
        payload = self._build_payload(level, message, extra)
        try:
            self._post(payload)
        except Exception:
            try:
                self._buffer(payload)
            except Exception:
                pass  # Buffer write failure must never crash the monitor

    def log_and_push(self, level: str, message: str, **fields) -> None:
        """Log locally and push to Loki in one call."""
        _LOG_FN = {"INFO": log.info, "WARN": log.warning, "ERROR": log.error}
        _LOG_FN.get(level, log.warning)(message)
        self.push(level, message, fields if fields else None)

    def flush(self) -> int:
        """Flush buffered entries. Returns count of entries delivered."""
        buf = self._buffer_path
        if not buf.exists() or buf.stat().st_size == 0:
            return 0
        lines = [l.strip() for l in buf.read_text(encoding="utf-8").splitlines() if l.strip()]
        if not lines:
            buf.unlink()
            return 0
        delivered = 0
        consumed = 0
        for line in lines:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                consumed += 1
                continue
            try:
                self._post(payload)
                delivered += 1
                consumed += 1
            except Exception:
                break
        if consumed == len(lines):
            buf.unlink()
            log.info("Log buffer flushed: %d entries delivered", delivered)
            self.push("WARN", f"Log buffer flushed: {delivered} entries",
                      {"event": config.LOG_EVENT_LOG_BUFFER_FLUSHED, "count": delivered})
        elif consumed > 0:
            remaining = lines[consumed:]
            buf.write_text("\n".join(remaining) + "\n", encoding="utf-8")
        return delivered


# ---------------------------------------------------------------------------
# Module-level singleton + back-compat shims
# ---------------------------------------------------------------------------
_singleton: LokiClient | None = None


def _get_singleton() -> LokiClient:
    global _singleton
    if _singleton is None:
        _singleton = LokiClient.from_config(config, credentials)
    return _singleton


def _build_loki_payload(level: str, message: str, extra: dict = None) -> dict:
    return _get_singleton()._build_payload(level, message, extra)


def _buffer_log_entry(payload: dict) -> None:
    _get_singleton()._buffer(payload)


def _post_loki(payload: dict) -> None:
    _get_singleton()._post(payload)


def push_log(level: str, message: str, extra: dict = None) -> None:
    _get_singleton().push(level, message, extra)


def log_and_push(level: str, message: str, **fields) -> None:
    _get_singleton().log_and_push(level, message, **fields)
