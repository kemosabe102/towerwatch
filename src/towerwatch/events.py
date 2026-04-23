"""
Named event emitters — canonical Loki payloads for every structured log event.

Each function owns the level, message template, and extra-field schema for one
event type. Dashboards and LogQL alerts filter on the stable LOG_EVENT_* keys;
this module is the single point of schema truth.
"""

from typing import Any

from towerwatch import config


def service_restarted(loki, *, version: str, build_date: str, platform: str) -> None:
    loki.push(
        "WARN",
        "Service restarted",
        {
            "event": config.LOG_EVENT_SERVICE_RESTARTED,
            "version": version,
            "build_date": build_date,
            "platform": platform,
        },
    )


def service_started(loki, *, log_level: str, platform: str) -> None:
    loki.push(
        "INFO",
        "Service started",
        {
            "event": config.LOG_EVENT_SERVICE_STARTED,
            "log_level": log_level,
            "platform": platform,
        },
    )


def connection_down(loki) -> None:
    loki.push(
        "ERROR",
        "All targets unreachable",
        {
            "event": config.LOG_EVENT_CONN_DOWN,
        },
    )


def connection_restored(loki, *, down_duration_s: int) -> None:
    loki.log_and_push(
        "INFO",
        f"Connection restored after {down_duration_s}s",
        event=config.LOG_EVENT_CONN_RESTORED,
        down_duration_s=down_duration_s,
    )


def outage_recorded(loki, *, gap_seconds: int, reason: str, version: str) -> None:
    gap_min = int(gap_seconds / 60)
    loki.push(
        "WARN",
        f"Outage recorded: {gap_seconds}s ({gap_min} min)",
        {
            "event": config.LOG_EVENT_OUTAGE_RECORDED,
            "gap_seconds": gap_seconds,
            "reason": reason,
            "version": version,
        },
    )


def service_heartbeat(loki, *, uptime_h: float) -> None:
    loki.push(
        "WARN",
        "Service heartbeat",
        {
            "event": config.LOG_EVENT_HEARTBEAT,
            "uptime_h": uptime_h,
        },
    )


def partition_missing(loki, *, path: str) -> None:
    loki.push(
        "WARN",
        f"Data partition not detected at {path}",
        {
            "event": config.LOG_EVENT_PARTITION_MISSING,
            "path": path,
        },
    )


def metrics_push_failed(loki, *, http_status: int | None = None, error: str | None = None) -> None:
    extra: dict[str, Any] = {"event": config.LOG_EVENT_METRICS_PUSH_FAIL}
    if http_status is not None:
        extra["http_status"] = http_status
    if error is not None:
        extra["error"] = error
    loki.push("WARN", "Metric push failed", extra)


def annotation_failed(loki, *, http_status: int | None = None, error: str | None = None) -> None:
    extra: dict[str, Any] = {"event": config.LOG_EVENT_ANNOTATION_FAILED}
    if http_status is not None:
        extra["http_status"] = http_status
    if error is not None:
        extra["error"] = error
    loki.push("WARN", "Annotation POST failed", extra)


def log_buffer_flushed(loki, *, count: int) -> None:
    loki.push(
        "WARN",
        f"Log buffer flushed: {count} entries",
        {
            "event": config.LOG_EVENT_LOG_BUFFER_FLUSHED,
            "count": count,
        },
    )
