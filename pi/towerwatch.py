#!/usr/bin/env python3
"""
Towerwatch — 5G Cell Tower Network Quality Monitor

Continuously monitors latency, jitter, packet loss, DNS resolution,
TCP connection time, throughput, and M6 signal quality. Pushes metrics
to Grafana Cloud over HTTPS. Pushes structured logs to Loki.
Buffers metrics locally during outages.

Cross-platform: runs on Raspberry Pi (production) and Windows (testing).
"""

import base64
import json
import logging
import os
import re
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
        # -n count, -w timeout in milliseconds
        return ["ping", "-n", str(config.PING_COUNT),
                "-w", str(config.PING_TIMEOUT_S * 1000), target]
    else:
        # -c count, -W timeout in seconds
        return ["ping", "-c", str(config.PING_COUNT),
                "-W", str(config.PING_TIMEOUT_S), target]


def _parse_ping_output(stdout: str) -> dict:
    """Parse ping output into {rtt_avg, rtt_min, rtt_max, jitter, pkt_loss, connected}."""
    # Parse packet loss — both platforms use "X% loss" or "X% packet loss"
    loss_match = re.search(r"(\d+)%\s*(?:packet )?loss", stdout)
    pkt_loss = int(loss_match.group(1)) if loss_match else 100

    rtt_min = rtt_avg = rtt_max = 0
    mdev = 0.0

    if IS_WINDOWS:
        # Windows: "Minimum = 12ms, Maximum = 45ms, Average = 28ms"
        win_match = re.search(
            r"Minimum\s*=\s*(\d+)ms.*Maximum\s*=\s*(\d+)ms.*Average\s*=\s*(\d+)ms",
            stdout, re.DOTALL,
        )
        if win_match:
            rtt_min = int(win_match.group(1))
            rtt_max = int(win_match.group(2))
            rtt_avg = int(win_match.group(3))
    else:
        # Linux: "rtt min/avg/max/mdev = 12.3/28.1/45.7/8.2 ms"
        rtt_match = re.search(
            r"rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)",
            stdout,
        )
        if rtt_match:
            rtt_min = round(float(rtt_match.group(1)))
            rtt_avg = round(float(rtt_match.group(2)))
            rtt_max = round(float(rtt_match.group(3)))
            mdev = float(rtt_match.group(4))

    # Parse individual RTTs for RFC 3550 jitter
    if IS_WINDOWS:
        # Windows: "Reply from X: bytes=32 time=28ms TTL=117"
        rtts = [float(m) for m in re.findall(r"time[=<](\d+)ms", stdout)]
    else:
        # Linux: "time=28.1 ms"
        rtts = [float(m) for m in re.findall(r"time=([\d.]+)", stdout)]

    if len(rtts) >= 2:
        diffs = [abs(rtts[i] - rtts[i - 1]) for i in range(1, len(rtts))]
        jitter = round(statistics.mean(diffs))
    else:
        jitter = round(mdev)

    return {
        "rtt_avg": rtt_avg, "rtt_min": rtt_min, "rtt_max": rtt_max,
        "jitter": jitter, "pkt_loss": pkt_loss,
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
# HTTP Download Timing
# ---------------------------------------------------------------------------
def measure_http_download() -> float:
    """Timed download of ~500KB CDN asset. Returns elapsed ms, 0 on failure."""
    try:
        start = time.perf_counter()
        resp = requests.get(
            config.HTTP_DOWNLOAD_URL,
            timeout=config.HTTP_DOWNLOAD_TIMEOUT_S,
        )
        resp.raise_for_status()
        return round((time.perf_counter() - start) * 1000)
    except Exception as e:
        log.warning("HTTP download failed: %s", e)
        return 0


# ---------------------------------------------------------------------------
# Speedtest (Ookla official CLI)
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


def poll_m6_signal() -> dict:
    """Poll Nighthawk M6 for signal metrics. Returns dict or empty on failure."""
    global _m6_session
    try:
        if _m6_session is None:
            _m6_session = requests.Session()
            _m6_session.auth = ("admin", secrets.M6_ADMIN_PASSWORD)

        resp = _m6_session.get(config.M6_WWAN_URL, timeout=config.M6_TIMEOUT_S)
        if resp.status_code == 401:
            _m6_session = None
            log.warning("M6 auth expired, will retry next cycle")
            push_log("WARN", "M6 auth expired",
                     {"event": config.LOG_EVENT_M6_AUTH_EXPIRED})
            return {}
        resp.raise_for_status()
        data = resp.json()

        result = {}
        for key in ("RSRP", "rsrp"):
            if key in data:
                result["m6_rsrp"] = int(data[key])
        for key in ("RSRQ", "rsrq"):
            if key in data:
                result["m6_rsrq"] = int(data[key])
        for key in ("SINR", "sinr"):
            if key in data:
                result["m6_sinr"] = int(data[key])
        for key in ("curBand", "band"):
            if key in data:
                result["m6_band"] = int(data[key]) if str(data[key]).isdigit() else 0
        return result
    except Exception as e:
        log.debug("M6 poll failed: %s", e)
        return {}


# ---------------------------------------------------------------------------
# Grafana Push (Influx Line Protocol over HTTPS)
# ---------------------------------------------------------------------------
def _build_auth_header() -> str:
    creds = f"{secrets.GRAFANA_INSTANCE_ID}:{secrets.GRAFANA_API_KEY}"
    return "Basic " + base64.b64encode(creds.encode()).decode()


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
    body = "\n".join(lines)
    try:
        resp = requests.post(
            config.GRAFANA_PUSH_URL,
            data=body,
            headers={
                "Authorization": _build_auth_header(),
                "Content-Type": "text/plain",
            },
            timeout=config.GRAFANA_PUSH_TIMEOUT_S,
        )
        if resp.status_code < 300:
            return True
        log.warning("Grafana push HTTP %d: %s", resp.status_code, resp.text[:200])
        push_log("WARN", f"Metric push HTTP {resp.status_code}",
                 {"event": config.LOG_EVENT_METRICS_PUSH_FAIL, "http_status": resp.status_code})
        return False
    except Exception as e:
        log.warning("Grafana push failed: %s", e)
        push_log("WARN", f"Metric push error: {e}",
                 {"event": config.LOG_EVENT_METRICS_PUSH_FAIL, "error": str(e)})
        return False


# ---------------------------------------------------------------------------
# Loki Log Shipping (direct HTTP push, no sidecar)
# ---------------------------------------------------------------------------
_LOG_LEVELS = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}
_deferred_warnings = []  # Warnings from before network is up


def push_log(level: str, message: str, extra: dict = None):
    """Push a structured log entry to Grafana Cloud Loki."""
    if _LOG_LEVELS.get(level, 0) < _LOG_LEVELS.get(config.LOKI_PUSH_LEVEL, 1):
        return
    if not getattr(secrets, "LOKI_URL", None):
        return  # Loki not configured — skip silently
    payload = {
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
    try:
        requests.post(
            secrets.LOKI_URL,
            json=payload,
            auth=(secrets.LOKI_USER, secrets.LOKI_TOKEN),
            timeout=config.LOKI_PUSH_TIMEOUT_S,
        )
    except Exception:
        pass  # Log push failure must never crash the monitor


def flush_deferred_warnings():
    """Push any warnings that were deferred from before network was available."""
    for level, msg, extra in _deferred_warnings:
        push_log(level, msg, extra)
    _deferred_warnings.clear()


# ---------------------------------------------------------------------------
# CSV Buffer (persistent writable partition, atomic writes)
# ---------------------------------------------------------------------------
def buffer_line(line: str):
    """Append an Influx line to the buffer file using atomic write pattern."""
    buf = Path(config.BUFFER_FILE)
    tmp = Path(config.BUFFER_TMP)
    buf.parent.mkdir(parents=True, exist_ok=True)

    existing = buf.read_text() if buf.exists() else ""
    tmp.write_text(existing + line + "\n")
    os.replace(str(tmp), str(buf))


def read_and_flush_buffer() -> list[str]:
    """Read all buffered lines, delete buffer. Returns list of lines."""
    buf = Path(config.BUFFER_FILE)
    if not buf.exists() or buf.stat().st_size == 0:
        return []
    lines = [l.strip() for l in buf.read_text().splitlines() if l.strip()]
    buf.unlink()
    return lines


# ---------------------------------------------------------------------------
# Startup guard: wait for data partition
# ---------------------------------------------------------------------------
def wait_for_data_partition(timeout: int = 30):
    """Block until the data partition is mounted or timeout. Skips on Windows."""
    data = Path(config.DATA_DIR)
    if IS_WINDOWS:
        # No separate partition on Windows — just ensure local dir exists
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
    # Running in dev/test without a separate partition — continue anyway
    log.warning("Data partition not detected at %s — buffering to local dir", data)
    _deferred_warnings.append(("WARN", f"Data partition not detected at {data}",
                               {"event": config.LOG_EVENT_PARTITION_MISSING, "path": str(data)}))
    data.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    log.info("=== Towerwatch %s ===", "(Windows)" if IS_WINDOWS else "(Raspberry Pi)")
    wait_for_data_partition()
    push_log("INFO", "Service started",
             {"event": config.LOG_EVENT_SERVICE_STARTED, "log_level": config.LOKI_PUSH_LEVEL,
              "platform": sys.platform})

    last_http_download = 0
    last_speedtest = 0
    _deferred_flushed = False

    while True:
        cycle_start = time.perf_counter()
        timestamp = int(time.time())
        fields = {}

        # --- 1. ICMP Ping (multi-target) ---
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

        update_connection_state(any_connected, timestamp)

        # --- 2. TCP Connection Time ---
        fields["tcp_connect_ms"] = measure_tcp_connect()

        # --- 3. DNS Resolution Time ---
        for ns in config.DNS_TARGETS:
            ns_label = ns.replace(".", "_")
            fields[f"dns_resolve_ms_{ns_label}"] = measure_dns(ns)

        # --- 4. M6 Signal Metrics ---
        m6 = poll_m6_signal()
        fields.update(m6)

        # --- 5. HTTP Download Timing (every 5 min) ---
        now = time.time()
        if now - last_http_download >= config.HTTP_DOWNLOAD_INTERVAL_S:
            fields["http_download_ms"] = measure_http_download()
            last_http_download = now

        # --- 6. Speedtest (every 6 hours, isolated subprocess) ---
        if now - last_speedtest >= config.SPEEDTEST_INTERVAL_S:
            st = run_speedtest()
            fields["download_mbps"] = st["download_mbps"]
            fields["upload_mbps"] = st["upload_mbps"]
            fields["speedtest_success"] = st["success"]
            last_speedtest = now

        # --- 7. Collection duration ---
        fields["collection_duration_ms"] = round(
            (time.perf_counter() - cycle_start) * 1000
        )

        # --- 8. Format and push ---
        line = format_influx_line(fields, timestamp)
        duration = fields["collection_duration_ms"]
        log.info("Cycle t=%d connected=%s rtt_avg_google=%s duration=%dms",
                 timestamp, fields.get("connected"),
                 fields.get("rtt_avg_google"), duration)
        push_log("DEBUG", f"Cycle complete in {duration}ms",
                 {"event": "cycle_complete", "duration_ms": duration,
                  "connected": fields.get("connected")})

        # Try to push current line + any buffered lines
        buffered = read_and_flush_buffer()
        all_lines = buffered + [line]

        if any_connected and push_metrics(all_lines):
            log.info("Pushed %d lines", len(all_lines))
            # Flush deferred warnings on first successful push
            if not _deferred_flushed and _deferred_warnings:
                flush_deferred_warnings()
                _deferred_flushed = True
            if len(buffered) > 0:
                push_log("INFO", f"Buffer flushed ({len(buffered)} lines)",
                         {"event": config.LOG_EVENT_BUFFER_FLUSHED, "flushed_count": len(buffered)})
        else:
            # Re-buffer everything
            for l in all_lines:
                buffer_line(l)
            if not any_connected:
                log.info("Offline — buffered (%d lines total)",
                         len(all_lines))
            else:
                log.warning("Push failed — buffered for retry")
                push_log("WARN", f"Metrics buffered ({len(all_lines)} lines)",
                         {"event": config.LOG_EVENT_METRICS_BUFFERED, "buffered_count": len(all_lines)})

        # --- Sleep until next cycle ---
        elapsed = time.perf_counter() - cycle_start
        sleep_time = max(0, config.METRIC_INTERVAL_S - elapsed)
        time.sleep(sleep_time)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Stopped by user")
    except Exception:
        log.exception("Fatal error")
        raise
