"""Towerwatch configuration constants."""

import os
import subprocess
import sys
from pathlib import Path


# --- Build version (stamped by ci.sh into version.txt; git fallback for dev) -----
# Authoritative at deploy time: ci.sh writes "<short-hash> <iso-date>" into pi/version.txt
# before cd.sh ships the tree. On the Pi the file lives at /opt/towerwatch/version.txt.
# If the file is missing we try `git rev-parse` for local dev; if that fails too, we
# mark the build as "dev"/"unknown" rather than crash.
def _load_build_version() -> tuple[str, str]:
    candidates = [
        Path(__file__).parent / "version.txt",            # repo-local (Windows dev)
        Path("/opt/towerwatch/version.txt"),              # Pi install path
    ]
    for p in candidates:
        try:
            if p.is_file():
                raw = p.read_text(encoding="utf-8").strip()
                if raw:
                    parts = raw.split(None, 1)
                    version = parts[0]
                    build_date = parts[1] if len(parts) > 1 else "unknown"
                    return version, build_date
        except OSError:
            continue
    # Fallback: ask git directly (works in-repo when version.txt hasn't been written yet).
    # Set TOWERWATCH_SKIP_GIT_VERSION=1 to suppress this subprocess call (e.g. in tests).
    if os.environ.get("TOWERWATCH_SKIP_GIT_VERSION") == "1":
        return "dev", "unknown"
    try:
        repo_root = Path(__file__).resolve().parents[1]
        version = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root, stderr=subprocess.DEVNULL, timeout=2,
        ).decode().strip()
        build_date = subprocess.check_output(
            ["git", "log", "-1", "--format=%cI"],
            cwd=repo_root, stderr=subprocess.DEVNULL, timeout=2,
        ).decode().strip()
        return version or "dev", build_date or "unknown"
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return "dev", "unknown"


BUILD_VERSION, BUILD_DATE = _load_build_version()

# --- Probe Targets (multi-target for evidence isolation) ---
# Each tuple: (ip, label). Labels become Prometheus tag values — must be stable strings.
# If the carrier gateway IP changes, update the IP here but keep the label "gateway".
PROBE_TARGETS = [
    ("8.8.8.8",       "google"),
    ("1.1.1.1",       "cloudflare"),
    ("192.168.1.1",   "gateway"),  # M6 router / carrier gateway
]

PING_COUNT = 10          # Probes per burst
PING_TIMEOUT_S = 10      # Total timeout for ping command

# --- TCP Probe ---
TCP_TARGET_HOST = "8.8.8.8"
TCP_TARGET_PORT = 443
TCP_TIMEOUT_S = 5

# --- DNS Resolution ---
DNS_TARGETS = ["8.8.8.8", "1.1.1.1"]
DNS_QUERY_DOMAIN = "example.com"
DNS_TIMEOUT_S = 5

# --- Intervals ---
METRIC_INTERVAL_S = 60       # Main loop: ping, TCP, DNS (was 30 — halved for data cap)

# --- HTTP Latency Probe (frequent, small file) ---
HTTP_LATENCY_URL = "https://speed.cloudflare.com/__down?bytes=10000"  # 10 KB
HTTP_LATENCY_INTERVAL_S = 300   # 5 minutes
HTTP_LATENCY_TIMEOUT_S = 30

# --- HTTP Throughput Sample (random schedule, replaces Ookla for routine use) ---
HTTP_THROUGHPUT_URL = "https://speed.cloudflare.com/__down?bytes=1000000"  # 1 MB
HTTP_THROUGHPUT_TESTS_PER_DAY = 4  # Spread randomly across 24 hours
HTTP_THROUGHPUT_TIMEOUT_S = 60

# --- Speedtest (manual only — each test uses ~400 MB at 5G speeds) ---
if sys.platform == "win32":
    SPEEDTEST_BINARY = "./speedtest_bin/speedtest.exe"
else:
    SPEEDTEST_BINARY = "/usr/bin/speedtest"
SPEEDTEST_TIMEOUT_S = 120
SPEEDTEST_SERVER_ID = None

# --- Startup grace period (let network settle before first probe) ---
STARTUP_GRACE_S = 15  # seconds to wait after startup before first probe cycle

# --- Gateway Probe (vendor-agnostic baseline) ---
GATEWAY_IP        = "192.168.1.1"
GATEWAY_TCP_PORT  = 80
GATEWAY_TIMEOUT_S = 5
GATEWAY_VENDOR    = "m6"  # "m6" | "orbi" | "" (baseline only)

# --- M6 Signal Metrics ---
M6_ADMIN_URL = "http://192.168.1.1/api/model.json"
M6_WWAN_URL = "http://192.168.1.1/api/wwanadv.json"
M6_TIMEOUT_S = 5

# --- Grafana Cloud (metrics use _ms suffix throughout, not Prometheus-standard seconds) ---
GRAFANA_PUSH_URL = os.environ.get(
    "GRAFANA_PUSH_URL_OVERRIDE",
    "https://prometheus-prod-67-prod-us-west-0.grafana.net"
    "/api/v1/push/influx/write?precision=s",
)
GRAFANA_PUSH_TIMEOUT_S = 10
INFLUX_MEASUREMENT = "towerwatch"
INFLUX_HOST_TAG = "towerwatch"

# --- Push Optimization (batching + compression) ---
PUSH_BATCH_SIZE = 2      # Accumulate N lines before pushing (at 60s = push every 2 min)
PUSH_COMPRESS = True     # gzip Influx POST body

# --- Local Buffering (platform-aware paths) ---
LOKI_BUFFER_MAX_BYTES = 256 * 1024  # 256 KB — ~500 WARN entries, ~8h of outage
if sys.platform == "win32":
    DATA_DIR = "./data"
    LOKI_BUFFER_FILE = "./data/buffer/loki.jsonl"
    LAST_PUSH_MARKER_FILE = "./data/last_push_ts"
    LAST_ALIVE_MARKER_FILE = "./data/last_alive_ts"
else:
    DATA_DIR = "/opt/towerwatch/data"
    LOKI_BUFFER_FILE = "/opt/towerwatch/data/buffer/loki.jsonl"
    LAST_PUSH_MARKER_FILE = "/opt/towerwatch/data/last_push_ts"
    LAST_ALIVE_MARKER_FILE = "/opt/towerwatch/data/last_alive_ts"

# --- Logging ---
LOG_LEVEL = "INFO"  # DEBUG for verbose output

# --- Loki (Structured Log Shipping) ---
LOKI_PUSH_TIMEOUT_S = 5
LOKI_PUSH_LEVEL = "WARN"  # Minimum level to push to Loki (WARN in production, INFO for testing)

# --- Outage Annotations (sticky region annotations in Grafana) ---
# When a push-gap of >=OUTAGE_GAP_THRESHOLD_S is detected on recovery, POST a region
# annotation to Grafana so the outage renders as a durable band across all panels.
# Your stack hostname is shown in the Grafana Cloud UI top-left; it looks like
# "<stackname>.grafana.net" (NOT the prometheus-prod-*.grafana.net push endpoint above).
GRAFANA_ANNOTATIONS_URL = "https://towerwatch.grafana.net/api/annotations"
GRAFANA_ANNOTATIONS_TIMEOUT_S = 5
OUTAGE_GAP_THRESHOLD_S = 600  # 10 min — shorter gaps are normal batch/push lag
OUTAGE_ANNOTATION_TAGS = ["towerwatch", "outage", "auto"]

# --- Log Event Identifiers (stable machine-readable keys for LogQL filtering) ---
LOG_EVENT_SERVICE_STARTED    = "service_started"
LOG_EVENT_SERVICE_RESTARTED  = "service_restarted"
LOG_EVENT_CONN_DOWN          = "connection_down"
LOG_EVENT_CONN_RESTORED      = "connection_restored"
LOG_EVENT_PING_FAILED        = "ping_failed"
LOG_EVENT_DNS_FAILED         = "dns_failed"
LOG_EVENT_SPEEDTEST_OK       = "speedtest_complete"
LOG_EVENT_SPEEDTEST_TIMEOUT  = "speedtest_timeout"
LOG_EVENT_SPEEDTEST_FAILED   = "speedtest_failed"
LOG_EVENT_M6_AUTH_EXPIRED    = "m6_auth_expired"
LOG_EVENT_METRICS_PUSH_FAIL  = "metrics_push_failed"
LOG_EVENT_LOG_BUFFER_FLUSHED = "log_buffer_flushed"
LOG_EVENT_PARTITION_MISSING  = "partition_not_detected"
LOG_EVENT_HTTP_THROUGHPUT_OK     = "http_throughput_complete"
LOG_EVENT_HTTP_THROUGHPUT_FAILED = "http_throughput_failed"
LOG_EVENT_HEARTBEAT              = "service_heartbeat"
LOG_EVENT_OUTAGE_RECORDED        = "outage_recorded"
LOG_EVENT_ANNOTATION_FAILED      = "annotation_push_failed"

# --- Heartbeat ---
HEARTBEAT_INTERVAL_S = 3600  # Emit a WARN-level heartbeat to Loki once per hour

# --- Bench harness (read base URL derived from existing public stack hostname) ---
GRAFANA_READ_BASE_URL = "https://towerwatch.grafana.net"
if sys.platform == "win32":
    BENCH_REPORT_DIR = "./data/bench/reports"
else:
    BENCH_REPORT_DIR = "/opt/towerwatch/data/bench/reports"
