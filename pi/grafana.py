"""Grafana Cloud transport — metrics push (Influx line protocol) and annotations."""

import base64
import gzip
import logging

import requests

import config

log = logging.getLogger("towerwatch")


class GrafanaClient:
    def __init__(
        self,
        push_url: str,
        annotations_url: str,
        instance_id: str,
        api_key: str,
        annotation_token: str = "",
        session_factory=requests.Session,
        push_timeout: int = 10,
        annotations_timeout: int = 5,
        compress: bool = True,
    ):
        self._push_url = push_url
        self._annotations_url = annotations_url
        self._instance_id = instance_id
        self._api_key = api_key
        self._annotation_token = annotation_token
        self._session_factory = session_factory
        self._push_timeout = push_timeout
        self._annotations_timeout = annotations_timeout
        self._compress = compress
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
        from loki import log_and_push
        body_raw = "\n".join(lines).encode("utf-8")
        headers = {}
        if self._compress:
            body = gzip.compress(body_raw)
            headers["Content-Encoding"] = "gzip"
        else:
            body = body_raw
        try:
            resp = self._get_session().post(
                self._push_url,
                data=body,
                headers=headers,
                timeout=self._push_timeout,
            )
            if resp.status_code < 300:
                return True
            log_and_push("WARN", f"Metric push HTTP {resp.status_code}",
                         event=config.LOG_EVENT_METRICS_PUSH_FAIL,
                         http_status=resp.status_code)
            if resp.status_code in (401, 403):
                self._invalidate_session()
            return False
        except Exception as e:
            log_and_push("WARN", f"Metric push error: {e}",
                         event=config.LOG_EVENT_METRICS_PUSH_FAIL, error=str(e))
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
        from loki import push_log
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
            r = requests.post(
                self._annotations_url,
                json=payload,
                headers={"Authorization": f"Bearer {self._annotation_token}"},
                timeout=self._annotations_timeout,
            )
            if r.status_code >= 300:
                push_log("WARN", f"Annotation POST failed: HTTP {r.status_code}",
                         {"event": config.LOG_EVENT_ANNOTATION_FAILED,
                          "status": r.status_code})
        except Exception as e:
            push_log("WARN", f"Annotation POST exception: {e}",
                     {"event": config.LOG_EVENT_ANNOTATION_FAILED, "error": str(e)})
