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
        "INFO",
        "Service restarted",
        {
            "event": config.LOG_EVENT_SERVICE_RESTARTED,
            "version": version,
            "build_date": build_date,
            "platform": platform,
        },
    )


def service_started(loki, *, log_level: str, platform: str, gateway_ip: str) -> None:
    loki.push(
        "INFO",
        "Service started",
        {
            "event": config.LOG_EVENT_SERVICE_STARTED,
            "log_level": log_level,
            "platform": platform,
            "gateway_ip": gateway_ip,
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


def service_heartbeat(loki, *, uptime_h: float, version: str, build_date: str) -> None:
    loki.push(
        "INFO",
        "Service heartbeat",
        {
            "event": config.LOG_EVENT_HEARTBEAT,
            "uptime_h": uptime_h,
            "version": version,
            "build_date": build_date,
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


def gateway_ip_changed(loki, *, old_ip: str, new_ip: str) -> None:
    """Fires when periodic re-resolution picks up a new gateway IP.

    Per-state-change cadence (a DHCP renewal / router reboot is rare), so
    loki.push is within the data budget. WARN because a gateway change usually
    means something notable happened to the link — and because the prior frozen
    value silently produced false 100%-loss + a dead M6 scrape, so an operator
    should see this transition in the log.
    """
    loki.push(
        "WARN",
        f"Gateway IP changed {old_ip} -> {new_ip}",
        {
            "event": config.LOG_EVENT_GATEWAY_IP_CHANGED,
            "old_ip": old_ip,
            "new_ip": new_ip,
        },
    )


def egress_ip_changed(loki, *, old_ip: str, new_ip: str, cgnat: int, colo: str) -> None:
    """Fires when a scheduled egress check sees a new public IP.

    On a near-static cable line, an egress-IP change is the primary LTE-failover
    signal (see docs/ullrich-gateway-ubc1340.md). Per-state-change cadence, so
    loki.push is within budget. WARN because a public-IP flip is notable.

    The message says "possible LTE failover; confirm via ASN" and does NOT claim
    "CGNAT = LTE": Cloudflare reports the post-NAT public IP, so the cgnat flag
    rarely fires on a real failover — attribution needs an ASN lookup (v2). The
    cgnat + colo fields ride along as context.
    """
    loki.push(
        "WARN",
        f"Egress IP changed {old_ip} -> {new_ip} (possible LTE failover; confirm via ASN)",
        {
            "event": config.LOG_EVENT_EGRESS_IP_CHANGED,
            "old_ip": old_ip,
            "new_ip": new_ip,
            "cgnat": cgnat,
            "colo": colo,
        },
    )


def log_buffer_flushed(loki, *, count: int) -> None:
    loki.push(
        "INFO",
        f"Log buffer flushed: {count} entries",
        {
            "event": config.LOG_EVENT_LOG_BUFFER_FLUSHED,
            "count": count,
        },
    )
