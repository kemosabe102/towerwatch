#!/usr/bin/env python3
"""
Towerwatch — 5G Cell Tower Network Quality Monitor

Continuously monitors latency, jitter, packet loss, DNS resolution,
TCP connection time, throughput, and M6 signal quality. Pushes metrics
to Grafana Cloud over HTTPS. Pushes structured logs to Loki.
Buffers logs locally during outages for delivery on reconnect.

Cross-platform: runs on Raspberry Pi (production) and Windows (testing).
"""

import base64
import gzip
import json
import logging
import os
import random
import re
import signal
import socket
import subprocess
import statistics
import sys
import time
from pathlib import Path

import dns.resolver
import requests

import config

try:
    import secrets
except ImportError:
    print("ERROR: secrets.py not found. Copy secrets.py.example to secrets.py and fill in values.")
    raise SystemExit(1)

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("towerwatch")

IS_WINDOWS = sys.platform == "win32"


# ---------------------------------------------------------------------------
# Connection state tracking
# ---------------------------------------------------------------------------
_connected = True
_outage_start = 0
_outage_count = 0
_total_outage_s = 0
_start_ts = time.monotonic()  # monotonic: uptime math must not regress when fake-hwclock jumps
_last_heartbeat_ts = 0
_last_successful_push_ts = time.time()  # wall clock: compared against buffered-sample timestamps


def update_connection_state(connected: bool, timestamp: int):
    global _connected, _outage_start, _outage_count, _total_outage_s
    if connected and not _connected:
        if _outage_start:
            duration = timestamp - _outage_start
            _total_outage_s += duration
            log.info("Connection UP (was down %ds)", duration)
            push_log("INFO", f"Connection restored after {duration}s",
                     {"event": config.LOG_EVENT_CONN_RESTORED, "down_duration_s": duration})
        _outage_start = 0
    elif not connected and _connected:
        _outage_start = timestamp
        _outage_count += 1
        log.warning("Connection DOWN")
        push_log("ERROR", "All targets unreachable",
                 {"event": config.LOG_EVENT_CONN_DOWN})
    _connected = connected


# ---------------------------------------------------------------------------
# ICMP Ping (cross-platform)
# ---------------------------------------------------------------------------
def _build_ping_cmd(target: str) -> list[str]:
    """Build platform-specific ping command."""
    if IS_WINDOWS:
        return ["ping", "-n", str(config.PING_COUNT),
                "-w", str(config.PING_TIMEOUT_S * 1000), target]
    else:
        return ["ping", "-c", str(config.PING_COUNT),
                "-W", str(config.PING_TIMEOUT_S), target]


def _parse_rtt_stats(stdout: str) -> tuple[int, int, int, float]:
    """Parse platform-specific RTT summary. Returns (min, avg, max, mdev)."""
    if IS_WINDOWS:
        m = re.search(
            r"Minimum\s*=\s*(\d+)ms.*Maximum\s*=\s*(\d+)ms.*Average\s*=\s*(\d+)ms",
            stdout, re.DOTALL,
        )
        if m:
            return int(m.group(1)), int(m.group(3)), int(m.group(2)), 0.0
        return 0, 0, 0, 0.0
    m = re.search(
        r"rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)",
        stdout,
    )
    if m:
        return (round(float(m.group(1))), round(float(m.group(2))),
                round(float(m.group(3))), float(m.group(4)))
    return 0, 0, 0, 0.0


def _calc_jitter(rtts: list[float], mdev: float) -> int:
    """RFC 3550 jitter from individual RTTs, falling back to mdev."""
    if len(rtts) >= 2:
        diffs = [abs(rtts[i] - rtts[i - 1]) for i in range(1, len(rtts))]
        return round(statistics.mean(diffs))
    return round(mdev)


def _parse_ping_output(stdout: str) -> dict:
    """Parse ping output into {rtt_avg, rtt_min, rtt_max, jitter, pkt_loss, connected}."""
    loss_match = re.search(r"(\d+)%\s*(?:packet )?loss", stdout)
    pkt_loss = int(loss_match.group(1)) if loss_match else 100

    rtt_min, rtt_avg, rtt_max, mdev = _parse_rtt_stats(stdout)

    if IS_WINDOWS:
        rtts = [float(m) for m in re.findall(r"time[=<](\d+)ms", stdout)]
    else:
        rtts = [float(m) for m in re.findall(r"time=([\d.]+)", stdout)]

    return {
        "rtt_avg": rtt_avg, "rtt_min": rtt_min, "rtt_max": rtt_max,
        "jitter": _calc_jitter(rtts, mdev), "pkt_loss": pkt_loss,
        "connected": pkt_loss < 100,
    }



def run_ping(target: str) -> dict:
    """Run ICMP ping burst, return {rtt_avg, rtt_min, rtt_max, jitter, pkt_loss, connected}."""
    try:
        result = subprocess.run(
            _build_ping_cmd(target),
            capture_output=True, text=True,
            timeout=config.PING_TIMEOUT_S * config.PING_COUNT + 5,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        log.warning("Ping %s failed: %s", target, e)
        push_log("WARN", f"Ping {target} failed",
                 {"event": config.LOG_EVENT_PING_FAILED, "target": target, "error": str(e)})
        return {"rtt_avg": 0, "rtt_min": 0, "rtt_max": 0,
                "jitter": 0, "pkt_loss": 100, "connected": False}

    return _parse_ping_output(result.stdout)


# ---------------------------------------------------------------------------
# TCP Connection Time
# ---------------------------------------------------------------------------
def measure_tcp_connect() -> float:
    """Measure TCP handshake time in ms. Returns 0 on failure."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(config.TCP_TIMEOUT_S)
        start = time.perf_counter()
        sock.connect((config.TCP_TARGET_HOST, config.TCP_TARGET_PORT))
        elapsed = (time.perf_counter() - start) * 1000
        sock.close()
        return round(elapsed)
    except (OSError, socket.timeout) as e:
        log.warning("TCP connect failed: %s", e)
        return 0


# ---------------------------------------------------------------------------
# DNS Resolution Time
# ---------------------------------------------------------------------------
def measure_dns(nameserver: str) -> float:
    """Measure DNS resolution time in ms using explicit nameserver."""
    resolver = dns.resolver.Resolver()
    resolver.nameservers = [nameserver]
    resolver.lifetime = config.DNS_TIMEOUT_S
    try:
        start = time.perf_counter()
        resolver.resolve(config.DNS_QUERY_DOMAIN, "A")
        return round((time.perf_counter() - start) * 1000)
    except Exception as e:
        log.warning("DNS %s failed: %s", nameserver, e)
        push_log("WARN", f"DNS {nameserver} failed",
                 {"event": config.LOG_EVENT_DNS_FAILED, "nameserver": nameserver, "error": str(e)})
        return 0


# ---------------------------------------------------------------------------
# HTTP Latency Probe (10 KB, frequent — replaces old 500 KB download)
# ---------------------------------------------------------------------------
def measure_http_latency() -> float:
    """Timed download of ~10KB CDN asset for latency proxy. Returns elapsed ms, 0 on failure."""
    try:
        start = time.perf_counter()
        resp = requests.get(
            config.HTTP_LATENCY_URL,
            timeout=config.HTTP_LATENCY_TIMEOUT_S,
        )
        resp.raise_for_status()
        _ = resp.content  # Ensure body is fully received
        return round((time.perf_counter() - start) * 1000)
    except Exception as e:
        log.warning("HTTP latency probe failed: %s", e)
        return 0


# ---------------------------------------------------------------------------
# HTTP Throughput Sample (1 MB, random schedule — replaces scheduled Ookla)
# ---------------------------------------------------------------------------
def measure_http_throughput() -> dict:
    """Timed download of ~1MB CDN asset for throughput estimation.
    Returns {http_throughput_ms, http_throughput_mbps}, zeros on failure."""
    try:
        start = time.perf_counter()
        resp = requests.get(
            config.HTTP_THROUGHPUT_URL,
            timeout=config.HTTP_THROUGHPUT_TIMEOUT_S,
        )
        resp.raise_for_status()
        size_bytes = len(resp.content)
        elapsed_s = time.perf_counter() - start
        throughput_mbps = round((size_bytes * 8) / elapsed_s / 1_000_000, 2)
        elapsed_ms = round(elapsed_s * 1000)
        log.info("HTTP throughput: %.1f Mbps (%d bytes in %dms)",
                 throughput_mbps, size_bytes, elapsed_ms)
        push_log("INFO", f"Throughput: {throughput_mbps} Mbps ({elapsed_ms}ms)",
                 {"event": config.LOG_EVENT_HTTP_THROUGHPUT_OK,
                  "throughput_mbps": throughput_mbps, "elapsed_ms": elapsed_ms})
        return {"http_throughput_ms": elapsed_ms, "http_throughput_mbps": throughput_mbps}
    except Exception as e:
        log.warning("HTTP throughput test failed: %s", e)
        push_log("WARN", f"HTTP throughput test failed: {e}",
                 {"event": config.LOG_EVENT_HTTP_THROUGHPUT_FAILED, "error": str(e)})
        return {"http_throughput_ms": 0, "http_throughput_mbps": 0}


# ---------------------------------------------------------------------------
# Speedtest (Ookla official CLI — manual only, not scheduled)
# ---------------------------------------------------------------------------
def run_speedtest() -> dict:
    """Run Ookla speedtest CLI. Returns {download_mbps, upload_mbps, success}."""
    cmd = [config.SPEEDTEST_BINARY, "--format=json", "--accept-license"]
    if config.SPEEDTEST_SERVER_ID:
        cmd += ["--server-id", str(config.SPEEDTEST_SERVER_ID)]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=config.SPEEDTEST_TIMEOUT_S,
        )
        data = json.loads(result.stdout)
        dl = round(data["download"]["bandwidth"] * 8 / 1_000_000, 2)
        ul = round(data["upload"]["bandwidth"] * 8 / 1_000_000, 2)
        log.info("Speedtest: %.1f Mbps down, %.1f Mbps up", dl, ul)
        push_log("INFO", f"Speedtest: {dl} Mbps down, {ul} Mbps up",
                 {"event": config.LOG_EVENT_SPEEDTEST_OK, "download_mbps": dl, "upload_mbps": ul})
        return {"download_mbps": dl, "upload_mbps": ul, "success": 1}
    except subprocess.TimeoutExpired:
        log.error("Speedtest timed out after %ds", config.SPEEDTEST_TIMEOUT_S)
        push_log("WARN", f"Speedtest timed out after {config.SPEEDTEST_TIMEOUT_S}s",
                 {"event": config.LOG_EVENT_SPEEDTEST_TIMEOUT, "timeout_s": config.SPEEDTEST_TIMEOUT_S})
        return {"download_mbps": 0, "upload_mbps": 0, "success": 0}
    except Exception as e:
        log.error("Speedtest failed: %s", e)
        push_log("WARN", f"Speedtest failed: {e}",
                 {"event": config.LOG_EVENT_SPEEDTEST_FAILED, "error": str(e)})
        return {"download_mbps": 0, "upload_mbps": 0, "success": 0}


# ---------------------------------------------------------------------------
# M6 Signal Metrics
# ---------------------------------------------------------------------------
_m6_session = None

_M6_FIELD_MAP = [
    ('m6_rsrp', ('RSRP', 'rsrp'), int),
    ('m6_rsrq', ('RSRQ', 'rsrq'), int),
    ('m6_sinr', ('SINR', 'sinr'), int),
    ('m6_band', ('curBand', 'band'), lambda v: int(v) if str(v).isdigit() else 0),
]


def _ensure_m6_session() -> requests.Session:
    """Lazily create the M6 admin session."""
    global _m6_session
    if _m6_session is None:
        _m6_session = requests.Session()
        _m6_session.auth = ('admin', secrets.M6_ADMIN_PASSWORD)
    return _m6_session


def _extract_m6_fields(data: dict) -> dict:
    """Extract signal metrics from M6 JSON using field map."""
    result = {}
    for metric, keys, convert in _M6_FIELD_MAP:
        val = next((data[k] for k in keys if k in data), None)
        if val is not None:
            result[metric] = convert(val)
    return result


def poll_m6_signal() -> dict:
    """Poll Nighthawk M6 for signal metrics. Returns dict or empty on failure."""
    global _m6_session
    try:
        session = _ensure_m6_session()
        resp = session.get(config.M6_WWAN_URL, timeout=config.M6_TIMEOUT_S)
        if resp.status_code == 401:
            _m6_session = None
            log.warning('M6 auth expired, will retry next cycle')
            push_log('WARN', 'M6 auth expired',
                     {'event': config.LOG_EVENT_M6_AUTH_EXPIRED})
            return {}
        resp.raise_for_status()
        return _extract_m6_fields(resp.json())
    except Exception as e:
        log.debug('M6 poll failed: %s', e)
        return {}


# ---------------------------------------------------------------------------
# Grafana Push (Influx Line Protocol over HTTPS, batched + gzip)
# ---------------------------------------------------------------------------
def _build_auth_header() -> str:
    creds = f"{secrets.GRAFANA_INSTANCE_ID}:{secrets.GRAFANA_API_KEY}"
    return "Basic " + base64.b64encode(creds.encode()).decode()


_grafana_session = None


def _get_grafana_session() -> requests.Session:
    global _grafana_session
    if _grafana_session is None:
        _grafana_session = requests.Session()
        _grafana_session.headers.update({
            "Authorization": _build_auth_header(),
            "Content-Type": "text/plain",
        })
    return _grafana_session


def format_influx_line(fields: dict, timestamp: int) -> str:
    """Format a single Influx line protocol string."""
    parts = [f"{k}={v}" for k, v in fields.items() if v is not None]
    return (
        f"{config.INFLUX_MEASUREMENT},host={config.INFLUX_HOST_TAG} "
        + ",".join(parts)
        + f" {timestamp}"
    )


def push_metrics(lines: list[str]) -> bool:
    """Push Influx line protocol lines to Grafana Cloud. Returns True on success."""
    global _grafana_session
    body_raw = "\n".join(lines).encode("utf-8")
    headers = {}
    if config.PUSH_COMPRESS:
        body = gzip.compress(body_raw)
        headers["Content-Encoding"] = "gzip"
    else:
        body = body_raw
    try:
        session = _get_grafana_session()
        resp = session.post(
            config.GRAFANA_PUSH_URL,
            data=body,
            headers=headers,
            timeout=config.GRAFANA_PUSH_TIMEOUT_S,
        )
        if resp.status_code < 300:
            return True
        log.warning("Grafana push HTTP %d: %s", resp.status_code, resp.text[:200])
        push_log("WARN", f"Metric push HTTP {resp.status_code}",
                 {"event": config.LOG_EVENT_METRICS_PUSH_FAIL, "http_status": resp.status_code})
        if resp.status_code in (401, 403):
            _grafana_session = None
        return False
    except Exception as e:
        log.warning("Grafana push failed: %s", e)
        push_log("WARN", f"Metric push error: {e}",
                 {"event": config.LOG_EVENT_METRICS_PUSH_FAIL, "error": str(e)})
        _grafana_session = None
        return False


# ---------------------------------------------------------------------------
# Loki Log Shipping (direct HTTP push, no sidecar)
# ---------------------------------------------------------------------------
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


def push_log(level: str, message: str, extra: dict = None):
    """Push a structured log entry to Grafana Cloud Loki. Buffers to disk on failure."""
    if _LOG_LEVELS.get(level, 0) < _LOG_LEVELS.get(config.LOKI_PUSH_LEVEL, 1):
        return
    if not getattr(secrets, "LOKI_URL", None):
        return
    payload = _build_loki_payload(level, message, extra)
    try:
        requests.post(
            secrets.LOKI_URL,
            json=payload,
            auth=(secrets.LOKI_USER, secrets.LOKI_TOKEN),
            timeout=config.LOKI_PUSH_TIMEOUT_S,
        )
    except Exception:
        try:
            _buffer_log_entry(payload)
        except Exception:
            pass  # Buffer write failure must never crash the monitor


def _flush_log_buffer():
    """Flush buffered Loki entries after connectivity returns."""
    buf = Path(config.LOKI_BUFFER_FILE)
    if not buf.exists() or buf.stat().st_size == 0:
        return
    lines = [l.strip() for l in buf.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lines:
        buf.unlink()
        return
    delivered = 0
    for line in lines:
        try:
            payload = json.loads(line)
            requests.post(
                secrets.LOKI_URL,
                json=payload,
                auth=(secrets.LOKI_USER, secrets.LOKI_TOKEN),
                timeout=config.LOKI_PUSH_TIMEOUT_S,
            )
            delivered += 1
        except Exception:
            break  # Network went away again — keep remaining entries
    if delivered == len(lines):
        buf.unlink()
        log.info("Log buffer flushed: %d entries delivered", delivered)
        push_log("WARN", f"Log buffer flushed: {delivered} entries",
                 {"event": config.LOG_EVENT_LOG_BUFFER_FLUSHED, "count": delivered})
    elif delivered > 0:
        remaining = lines[delivered:]
        buf.write_text("\n".join(remaining) + "\n", encoding="utf-8")


def push_annotation(time_ms: int, time_end_ms: int, text: str,
                    reason: str | None = None, version: str | None = None):
    """POST a region annotation to Grafana's annotations API.

    Fire-and-forget: failures log to Loki but never block the monitor.
    Requires GRAFANA_ANNOTATION_TOKEN in secrets.py (service account with
    annotations:write permission).

    `reason` (e.g. "network_unreachable", "process_restart") is appended to
    tags so Grafana can filter/color by it. `version` is appended to the
    text so deploy boundaries are visible at a glance.
    """
    token = getattr(secrets, "GRAFANA_ANNOTATION_TOKEN", "")
    if not token:
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
            config.GRAFANA_ANNOTATIONS_URL,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=config.GRAFANA_ANNOTATIONS_TIMEOUT_S,
        )
        if r.status_code >= 300:
            push_log("WARN", f"Annotation POST failed: HTTP {r.status_code}",
                     {"event": config.LOG_EVENT_ANNOTATION_FAILED,
                      "status": r.status_code})
    except Exception as e:
        push_log("WARN", f"Annotation POST exception: {e}",
                 {"event": config.LOG_EVENT_ANNOTATION_FAILED,
                  "error": str(e)})





# ---------------------------------------------------------------------------
# Startup guard: wait for data partition
# ---------------------------------------------------------------------------
def wait_for_data_partition(timeout: int = 30):
    """Block until the data partition is mounted or timeout. Skips on Windows."""
    data = Path(config.DATA_DIR)
    if IS_WINDOWS:
        data.mkdir(parents=True, exist_ok=True)
        log.info("Windows: using local data dir %s", data)
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        if data.is_dir():
            try:
                result = subprocess.run(
                    ["mountpoint", "-q", str(data)],
                    capture_output=True, timeout=5,
                )
                if result.returncode == 0:
                    log.info("Data partition mounted at %s", data)
                    return
            except Exception:
                pass
        time.sleep(1)
    log.warning("Data partition not detected at %s — buffering to local dir", data)
    try:
        _buffer_log_entry(_build_loki_payload(
            "WARN", f"Data partition not detected at {data}",
            {"event": config.LOG_EVENT_PARTITION_MISSING, "path": str(data)}))
    except Exception:
        pass
    data.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Random throughput schedule
# ---------------------------------------------------------------------------
def _build_daily_throughput_schedule() -> list[float]:
    """Generate random timestamps for today's throughput tests.
    Divides 24 hours into equal slots, picks a random second in each.
    Skips slots whose time has already passed."""
    n = config.HTTP_THROUGHPUT_TESTS_PER_DAY
    now = time.time()
    # Start of today (local time)
    local = time.localtime(now)
    midnight = time.mktime(time.struct_time((
        local.tm_year, local.tm_mon, local.tm_mday,
        0, 0, 0, 0, 0, local.tm_isdst,
    )))
    slot_size = 86400 / n
    schedule = []
    for i in range(n):
        slot_start = midnight + i * slot_size
        slot_end = slot_start + slot_size
        t = random.uniform(slot_start, slot_end)
        if t > now:
            schedule.append(t)
    schedule.sort()
    log.info("Throughput schedule: %s",
             [time.strftime("%H:%M", time.localtime(t)) for t in schedule])
    return schedule


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------
_shutdown_requested = False


def _handle_sigterm(signum, frame):
    global _shutdown_requested
    log.info("SIGTERM received — shutting down gracefully")
    _shutdown_requested = True


if not IS_WINDOWS:
    signal.signal(signal.SIGTERM, _handle_sigterm)


# ---------------------------------------------------------------------------
# Main loop helpers
# ---------------------------------------------------------------------------
def _collect_probes(last_http_latency, throughput_schedule, last_schedule_day):
    """Run all probe sections. Returns (fields, any_connected, last_http_latency, throughput_schedule, last_schedule_day)."""
    fields = {}
    now = time.time()

    # ICMP Ping (multi-target)
    any_connected = False
    for target_ip, target_label in config.PROBE_TARGETS:
        ping = run_ping(target_ip)
        any_connected = any_connected or ping["connected"]
        for metric, value in ping.items():
            if metric == "connected":
                fields[f"connected_{target_label}"] = 1 if value else 0
            else:
                fields[f"{metric}_{target_label}"] = value
    fields["connected"] = 1 if any_connected else 0
    fields["metric_interval_s"] = config.METRIC_INTERVAL_S

    # TCP + DNS
    fields["tcp_connect_ms"] = measure_tcp_connect()
    for ns in config.DNS_TARGETS:
        ns_label = ns.replace(".", "_")
        fields[f"dns_resolve_ms_{ns_label}"] = measure_dns(ns)

    # M6 Signal
    fields.update(poll_m6_signal())

    # HTTP Latency (10KB, every 5 min)
    if now - last_http_latency >= config.HTTP_LATENCY_INTERVAL_S:
        fields["http_latency_ms"] = measure_http_latency()
        last_http_latency = now

    # HTTP Throughput (1MB, random schedule)
    today_yday = time.localtime().tm_yday
    if today_yday != last_schedule_day:
        throughput_schedule = _build_daily_throughput_schedule()
        last_schedule_day = today_yday
    if throughput_schedule and now >= throughput_schedule[0]:
        throughput_schedule.pop(0)
        fields.update(measure_http_throughput())

    return fields, any_connected, last_http_latency, throughput_schedule, last_schedule_day


def _log_cycle(fields, timestamp):
    """Log cycle summary to console and Loki."""
    duration = fields["collection_duration_ms"]
    log.info("Cycle t=%d connected=%s rtt_avg_google=%s duration=%dms",
             timestamp, fields.get("connected"),
             fields.get("rtt_avg_google"), duration)
    push_log("DEBUG", f"Cycle complete in {duration}ms",
             {"event": "cycle_complete", "duration_ms": duration,
              "connected": fields.get("connected")})


def _persist_last_push_ts(ts: float):
    """Write the last-successful-push timestamp atomically to the data partition.

    Read on startup to distinguish process-restart gaps from network-outage gaps.
    Best-effort — IO failures are swallowed because this is a hint, not critical data.
    """
    try:
        marker = Path(config.LAST_PUSH_MARKER_FILE)
        marker.parent.mkdir(parents=True, exist_ok=True)
        tmp = marker.with_suffix(marker.suffix + ".tmp")
        tmp.write_text(f"{ts:.0f}\n", encoding="utf-8")
        os.replace(tmp, marker)
    except OSError:
        pass


def _load_last_push_ts() -> float | None:
    """Read the persisted last-push timestamp. Returns None if missing/unreadable."""
    try:
        raw = Path(config.LAST_PUSH_MARKER_FILE).read_text(encoding="utf-8").strip()
        return float(raw) if raw else None
    except (OSError, ValueError):
        return None


def _touch_last_alive():
    """Update the last-alive marker every cycle. Best-effort, no fsync (advisory)."""
    try:
        p = Path(config.LAST_ALIVE_MARKER_FILE)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"{time.time():.0f}\n", encoding="utf-8")
    except OSError:
        pass


def _load_last_alive_ts() -> float | None:
    """Read the last-alive timestamp. Returns None if missing/unreadable."""
    try:
        raw = Path(config.LAST_ALIVE_MARKER_FILE).read_text(encoding="utf-8").strip()
        return float(raw) if raw else None
    except (OSError, ValueError):
        return None


def _record_outage_annotation(start_ts: float, end_ts: float, reason: str):
    """POST a region annotation for an outage gap and log it."""
    gap_s = int(end_ts - start_ts)
    gap_min = int(gap_s / 60)
    text = (f"Outage: {gap_min} min — {reason} "
            f"(v {config.BUILD_VERSION})")
    push_annotation(int(start_ts * 1000), int(end_ts * 1000),
                    text, reason=reason, version=config.BUILD_VERSION)
    push_log("WARN", f"Outage recorded: {gap_s}s ({gap_min} min)",
             {"event": config.LOG_EVENT_OUTAGE_RECORDED,
              "gap_seconds": gap_s, "reason": reason,
              "version": config.BUILD_VERSION})


_metric_batch: list[str] = []


def _batch_and_push(line: str, any_connected: bool):
    """Accumulate metrics in memory; push when batch is full. Drop on failure."""
    global _last_successful_push_ts
    _metric_batch.append(line)
    if len(_metric_batch) < config.PUSH_BATCH_SIZE:
        return
    batch = _metric_batch[:]
    _metric_batch.clear()
    if not any_connected:
        return
    if not push_metrics(batch):
        log.warning("Metric push failed — batch dropped (%d lines)", len(batch))
        return
    log.info("Pushed %d lines", len(batch))
    now = time.time()
    gap = now - _last_successful_push_ts
    if gap >= config.OUTAGE_GAP_THRESHOLD_S:
        _record_outage_annotation(_last_successful_push_ts, now, "network_unreachable")
    _last_successful_push_ts = now
    _persist_last_push_ts(now)
    _flush_log_buffer()



def _maybe_heartbeat():
    """Emit a periodic heartbeat log to Loki so the Event Log panel stays populated."""
    global _last_heartbeat_ts
    now = time.time()
    if now - _last_heartbeat_ts >= config.HEARTBEAT_INTERVAL_S:
        uptime_h = round((time.monotonic() - _start_ts) / 3600, 1)
        push_log("WARN", "Service heartbeat",
                 {"event": config.LOG_EVENT_HEARTBEAT, "uptime_h": uptime_h})
        _last_heartbeat_ts = now


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    global _last_successful_push_ts
    log.info("=== Towerwatch %s ===", "(Windows)" if IS_WINDOWS else "(Raspberry Pi)")
    wait_for_data_partition()

    # Emit service_restarted at WARN so it survives LOKI_PUSH_LEVEL=WARN and shows
    # up as a deploy marker in the event-log panel. Keep service_started too for
    # backwards compat with any saved Grafana queries.
    push_log("WARN", "Service restarted",
             {"event": config.LOG_EVENT_SERVICE_RESTARTED,
              "version": config.BUILD_VERSION,
              "build_date": config.BUILD_DATE,
              "platform": sys.platform})
    push_log("INFO", "Service started",
             {"event": config.LOG_EVENT_SERVICE_STARTED, "log_level": config.LOKI_PUSH_LEVEL,
              "platform": sys.platform})

    # One-shot restart metric — will be pushed with the first batch.
    _metric_batch.append(format_influx_line({"service_restart": 1}, int(time.time())))

    # Load persisted markers and check for a cross-restart gap. Use last_alive_ts
    # to distinguish: process was running (network outage) vs process was dead (restart).
    loaded_last_push = _load_last_push_ts()
    loaded_last_alive = _load_last_alive_ts()
    if loaded_last_push is not None:
        _last_successful_push_ts = loaded_last_push
        startup_now = time.time()
        startup_gap = startup_now - loaded_last_push
        if startup_gap >= config.OUTAGE_GAP_THRESHOLD_S:
            if loaded_last_alive and (startup_now - loaded_last_alive) < config.OUTAGE_GAP_THRESHOLD_S:
                reason = "network_unreachable"
            else:
                reason = "process_restart"
            _record_outage_annotation(loaded_last_push, startup_now, reason)

    _flush_log_buffer()

    last_http_latency = 0
    throughput_schedule = _build_daily_throughput_schedule()
    last_schedule_day = time.localtime().tm_yday

    while not _shutdown_requested:
        cycle_start = time.perf_counter()
        timestamp = int(time.time())

        fields, any_connected, last_http_latency, throughput_schedule, last_schedule_day = \
            _collect_probes(last_http_latency, throughput_schedule, last_schedule_day)

        update_connection_state(any_connected, timestamp)
        fields["collection_duration_ms"] = round(
            (time.perf_counter() - cycle_start) * 1000
        )

        _log_cycle(fields, timestamp)
        _touch_last_alive()
        _batch_and_push(format_influx_line(fields, timestamp), any_connected)

        _maybe_heartbeat()

        elapsed = time.perf_counter() - cycle_start
        time.sleep(max(0, config.METRIC_INTERVAL_S - elapsed))

    log.info("Shutdown complete")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Stopped by user")
    except Exception as e:
        log.critical("Fatal error: %s", e, exc_info=True)
        push_log("ERROR", f"Fatal: {e}", {"event": "fatal_error", "error": str(e)})
        raise
