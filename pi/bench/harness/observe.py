"""Grafana Cloud read helpers for harness assertions.

All polling loops account for the full pipeline latency:
  outage duration + push batch window (~2 min) + Grafana ingestion (~1 min)
Annotation tests need the most slack: use timeout_s >= 1200 (20 min) when
the outage itself is 10-12 min.

Tokens are never written to reports, logs, or exception text.
"""

import re
import time
from typing import Optional

import requests


def _scrub_auth(text: str) -> str:
    return re.sub(r'Bearer\s+\S+', 'Bearer [REDACTED]', text, flags=re.IGNORECASE)


class ObserveError(Exception):
    pass


class BenchSkip(Exception):
    """Raise from inject() to cleanly skip the test (e.g. preflight unsafe)."""
    pass


class GrafanaObserver:
    """Wraps Grafana Cloud read APIs.  All methods are blocking pollers."""

    def __init__(
        self,
        stack_base_url: str,     # e.g. "https://towerwatch.grafana.net"
        api_key: str,            # GRAFANA_API_KEY — metrics:read + logs:read
        annotation_token: str,   # GRAFANA_ANNOTATION_TOKEN — annotations:read
    ):
        self._base = stack_base_url.rstrip("/")
        self._api_key = api_key
        self._ann_token = annotation_token
        self._prom_uid: Optional[str] = None
        self._loki_uid: Optional[str] = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, url: str, token: str, params: dict = None) -> dict:
        try:
            r = requests.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                params=params or {},
                timeout=15,
            )
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as e:
            raise ObserveError(f"HTTP {e.response.status_code} from {url}") from None
        except requests.RequestException as e:
            raise ObserveError(_scrub_auth(str(e))) from None

    def _resolve_ds_uids(self) -> None:
        """Resolve datasource UIDs once at run start."""
        if self._prom_uid and self._loki_uid:
            return
        ds_list = self._get(f"{self._base}/api/datasources", self._api_key)
        for ds in ds_list:
            name = ds.get("name", "").lower()
            if "prom" in name and self._prom_uid is None:
                self._prom_uid = ds["uid"]
            elif "log" in name and self._loki_uid is None:
                self._loki_uid = ds["uid"]
        if not self._prom_uid:
            raise ObserveError("Could not resolve Prometheus datasource UID")
        if not self._loki_uid:
            raise ObserveError("Could not resolve Loki datasource UID")

    # ------------------------------------------------------------------
    # Loki queries
    # ------------------------------------------------------------------

    def loki_query_range(self, logql: str, start_ns: int, end_ns: int) -> list[dict]:
        self._resolve_ds_uids()
        url = f"{self._base}/api/datasources/proxy/uid/{self._loki_uid}/loki/api/v1/query_range"
        data = self._get(url, self._api_key, params={
            "query": logql, "start": start_ns, "end": end_ns, "limit": 500,
        })
        streams = data.get("data", {}).get("result", [])
        entries = []
        for stream in streams:
            for ts, line in stream.get("values", []):
                entries.append({"ts_ns": int(ts), "line": line, "labels": stream.get("stream", {})})
        return entries

    def poll_loki_event(
        self,
        event_name: str,
        start_ns: int,
        timeout_s: int = 600,
        poll_interval_s: int = 30,
        job: str = "towerwatch",
    ) -> dict:
        """Poll until a Loki event appears or timeout.

        Returns the matching entry dict, raises ObserveError on timeout.
        Polling every 30s by default; set poll_interval_s=60 for slow events.
        For annotation-dependent tests use timeout_s=1200 (20 min).
        """
        logql = f'{{job="{job}"}} | json | event="{event_name}"'
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            end_ns = int(time.time() * 1e9)
            entries = self.loki_query_range(logql, start_ns, end_ns)
            if entries:
                return entries[-1]
            remaining = deadline - time.time()
            sleep = min(poll_interval_s, max(5, remaining))
            time.sleep(sleep)
        raise ObserveError(
            f"Event '{event_name}' not seen in Loki within {timeout_s}s"
        )

    def assert_loki_event_absent(
        self,
        event_name: str,
        start_ns: int,
        end_ns: int,
        job: str = "towerwatch",
    ) -> None:
        logql = f'{{job="{job}"}} | json | event="{event_name}"'
        entries = self.loki_query_range(logql, start_ns, end_ns)
        if entries:
            raise ObserveError(
                f"Event '{event_name}' unexpectedly present ({len(entries)} entries)"
            )

    # ------------------------------------------------------------------
    # Prometheus / PromQL
    # ------------------------------------------------------------------

    def prom_query_range(self, promql: str, start_s: int, end_s: int, step: str = "60s") -> list:
        self._resolve_ds_uids()
        url = (
            f"{self._base}/api/datasources/proxy/uid/{self._prom_uid}"
            f"/api/v1/query_range"
        )
        data = self._get(url, self._api_key, params={
            "query": promql, "start": start_s, "end": end_s, "step": step,
        })
        return data.get("data", {}).get("result", [])

    def poll_prom_metric_present(
        self,
        promql: str,
        start_s: int,
        timeout_s: int = 600,
        poll_interval_s: int = 30,
    ) -> list:
        """Poll until PromQL returns at least one data point."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            result = self.prom_query_range(promql, start_s, int(time.time()), step="60s")
            if result:
                return result
            remaining = deadline - time.time()
            time.sleep(min(poll_interval_s, max(5, remaining)))
        raise ObserveError(f"PromQL '{promql}' returned no data within {timeout_s}s")

    def assert_prom_metric_absent(self, promql: str, start_s: int, end_s: int) -> None:
        result = self.prom_query_range(promql, start_s, end_s)
        if result:
            raise ObserveError(f"PromQL '{promql}' unexpectedly has data")

    # ------------------------------------------------------------------
    # Annotations
    # ------------------------------------------------------------------

    def get_annotations(self, from_ms: int, to_ms: int, tags: list[str] = None) -> list[dict]:
        """Fetch Grafana annotations in a time window.

        Uses GRAFANA_ANNOTATION_TOKEN (service-account, annotations:read).
        """
        tags = tags or ["towerwatch", "outage", "auto"]
        params = {"from": from_ms, "to": to_ms}
        for tag in tags:
            params.setdefault("tags", [])
            if isinstance(params["tags"], list):
                params["tags"].append(tag)
        url = f"{self._base}/api/annotations"
        try:
            r = requests.get(
                url,
                headers={"Authorization": f"Bearer {self._ann_token}"},
                params={"from": from_ms, "to": to_ms, "tags": tags},
                timeout=15,
            )
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as e:
            raise ObserveError(f"HTTP {e.response.status_code} fetching annotations") from None
        except requests.RequestException as e:
            raise ObserveError(_scrub_auth(str(e))) from None

    def poll_annotation(
        self,
        inject_start_ms: int,
        inject_end_ms: int,
        timeout_s: int = 1200,
        poll_interval_s: int = 60,
        min_duration_s: int = 600,
    ) -> dict:
        """Poll until a region annotation overlapping the injection window appears.

        timeout_s defaults to 1200 (20 min) to allow:
          - outage duration (~12 min)
          - push batch window (2 min)
          - Grafana ingestion + annotation POST (1-2 min)
          - polling slack

        min_duration_s: annotation must span at least this many seconds.
        """
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            now_ms = int(time.time() * 1000)
            anns = self.get_annotations(inject_start_ms - 60_000, now_ms)
            for ann in anns:
                ann_start = ann.get("time", 0)
                ann_end   = ann.get("timeEnd", ann_start)
                overlaps  = ann_start <= inject_end_ms and ann_end >= inject_start_ms
                long_enough = (ann_end - ann_start) >= min_duration_s * 1000
                if overlaps and long_enough:
                    return ann
            remaining = deadline - time.time()
            time.sleep(min(poll_interval_s, max(10, remaining)))
        raise ObserveError(
            f"No outage annotation found overlapping injection window within {timeout_s}s"
        )
