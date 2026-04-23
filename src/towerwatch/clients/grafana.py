"""Grafana Cloud transport — metrics push (Influx line protocol) and annotations.

All I/O collaborators (session factory, annotation POST callable, loki sink,
events namespace) are injectable. Production uses sensible defaults.
"""

import base64
import gzip
import logging

import requests

from towerwatch import config

log = logging.getLogger("towerwatch")


class _LazyLokiSink:
    """Lazy bridge to the module-level loki singleton.

    Production callers can leave `loki=None` and the client will resolve
    `loki._get_singleton()` the first time an event needs to be emitted.
    """

    _cached = None

    def _get(self):
        if self._cached is None:
            from towerwatch.clients.loki import _get_singleton
            self._cached = _get_singleton()
        return self._cached

    def push(self, level, message, extra=None):
        try:
            self._get().push(level, message, extra)
        except Exception:
            pass

    def log_and_push(self, level, message, **fields):
        try:
            self._get().log_and_push(level, message, **fields)
        except Exception:
            pass


class GrafanaClient:
    def __init__(
        self,
        push_url: str,
        annotations_url: str,
        instance_id: str,
        api_key: str,
        annotation_token: str = "",
        session_factory=requests.Session,
        annotation_post=requests.post,
        push_timeout: int = 10,
        annotations_timeout: int = 5,
        compress: bool = True,
        loki=None,
        events=None,
    ):
        self._push_url = push_url
        self._annotations_url = annotations_url
        self._instance_id = instance_id
        self._api_key = api_key
        self._annotation_token = annotation_token
        self._session_factory = session_factory
        self._annotation_post = annotation_post
        self._push_timeout = push_timeout
        self._annotations_timeout = annotations_timeout
        self._compress = compress
        self._loki = loki if loki is not None else _LazyLokiSink()
        if events is None:
            from towerwatch import events as _events_mod
            events = _events_mod
        self._events = events
        self._session: requests.Session | None = None

    @classmethod
    def from_config(cls, cfg, creds) -> "GrafanaClient":
        return cls(
            push_url=cfg.GRAFANA_PUSH_URL,
            annotations_url=cfg.GRAFANA_ANNOTATIONS_URL,
            instance_id=creds.GRAFANA_INSTANCE_ID,
            api_key=creds.GRAFANA_API_KEY,
            annotation_token=getattr(creds, "GRAFANA_ANNOTATION_TOKEN", ""),
            push_timeout=cfg.GRAFANA_PUSH_TIMEOUT_S,
            annotations_timeout=cfg.GRAFANA_ANNOTATIONS_TIMEOUT_S,
            compress=cfg.PUSH_COMPRESS,
        )

    def _get_session(self) -> requests.Session:
        if self._session is None:
            s = self._session_factory()
            creds = f"{self._instance_id}:{self._api_key}"
            auth = "Basic " + base64.b64encode(creds.encode()).decode()
            s.headers.update({"Authorization": auth, "Content-Type": "text/plain"})
            self._session = s
        return self._session

    def _invalidate_session(self) -> None:
        self._session = None

    def push_metrics(self, lines: list[str]) -> bool:
        """Push Influx line protocol lines to Grafana Cloud. Returns True on success."""
        body_raw = "\n".join(lines).encode("utf-8")
        headers = {}
        if self._compress:
            body = gzip.compress(body_raw)
            headers["Content-Encoding"] = "gzip"
        else:
            body = body_raw
        try:
            resp = self._get_session().post(
                self._push_url, data=body, headers=headers,
                timeout=self._push_timeout,
            )
            if resp.status_code < 300:
                return True
            self._events.metrics_push_failed(self._loki, http_status=resp.status_code)
            if resp.status_code in (401, 403):
                self._invalidate_session()
            return False
        except Exception as e:
            self._events.metrics_push_failed(self._loki, error=str(e))
            self._invalidate_session()
            return False

    def push_annotation(
        self,
        time_ms: int,
        time_end_ms: int,
        text: str,
        reason: str | None = None,
        version: str | None = None,
    ) -> None:
        """POST a region annotation to Grafana. Fire-and-forget."""
        if not self._annotation_token:
            return
        tags = list(config.OUTAGE_ANNOTATION_TAGS)
        if reason:
            tags.append(f"reason:{reason}")
        if version and version != "dev":
            tags.append(f"version:{version}")
        payload = {
            "time": time_ms,
            "timeEnd": time_end_ms,
            "tags": tags,
            "text": text,
        }
        try:
            r = self._annotation_post(
                self._annotations_url,
                json=payload,
                headers={"Authorization": f"Bearer {self._annotation_token}"},
                timeout=self._annotations_timeout,
            )
            if r.status_code >= 300:
                self._events.annotation_failed(self._loki, http_status=r.status_code)
        except Exception as e:
            self._events.annotation_failed(self._loki, error=str(e))
