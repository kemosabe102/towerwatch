"""Towerwatch configuration constants."""

import sys

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
METRIC_INTERVAL_S = 30       # Main loop: ping, TCP, DNS, push
HTTP_DOWNLOAD_INTERVAL_S = 300   # 5 minutes
SPEEDTEST_INTERVAL_S = 21600     # 6 hours

# --- HTTP Download Timing (mid-frequency throughput proxy) ---
HTTP_DOWNLOAD_URL = "https://speed.cloudflare.com/__down?bytes=500000"
HTTP_DOWNLOAD_TIMEOUT_S = 30

# --- Speedtest (platform-aware binary path) ---
if sys.platform == "win32":
    SPEEDTEST_BINARY = "./speedtest_bin/speedtest.exe"
else:
    SPEEDTEST_BINARY = "/usr/bin/speedtest"
SPEEDTEST_TIMEOUT_S = 120   # Kill hung speedtest after 2 min
# Pin to a specific server ID if needed (None = auto-select)
SPEEDTEST_SERVER_ID = None

# --- M6 Signal Metrics ---
M6_ADMIN_URL = "http://192.168.1.1/api/model.json"
M6_WWAN_URL = "http://192.168.1.1/api/wwanadv.json"
M6_TIMEOUT_S = 5

# --- Grafana Cloud (metrics use _ms suffix throughout, not Prometheus-standard seconds) ---
GRAFANA_PUSH_URL = (
    "https://prometheus-prod-67-prod-us-west-0.grafana.net"
    "/api/v1/push/influx/write?precision=s"
)
GRAFANA_PUSH_TIMEOUT_S = 10
INFLUX_MEASUREMENT = "towerwatch"
INFLUX_HOST_TAG = "towerwatch"

# --- Local Buffering (platform-aware paths) ---
if sys.platform == "win32":
    DATA_DIR = "./data"
    BUFFER_FILE = "./data/buffer/metrics.csv"
    BUFFER_TMP = "./data/buffer/metrics.csv.tmp"
else:
    DATA_DIR = "/opt/towerwatch/data"
    BUFFER_FILE = "/opt/towerwatch/data/buffer/metrics.csv"
    BUFFER_TMP = "/opt/towerwatch/data/buffer/metrics.csv.tmp"

# --- Logging ---
LOG_LEVEL = "INFO"  # DEBUG for verbose output

# --- Loki (Structured Log Shipping) ---
LOKI_PUSH_TIMEOUT_S = 5
# Minimum level to push to Loki: "DEBUG", "INFO", "WARN", "ERROR"
# Use DEBUG at home for testing, WARN in production to minimize volume
LOKI_PUSH_LEVEL = "INFO"

# --- Log Event Identifiers (stable machine-readable keys for LogQL filtering) ---
# Usage: push_log("WARN", "message", {"event": config.LOG_EVENT_PING_FAILED, ...})
# Query: {job="towerwatch"} | json | event="ping_failed"
LOG_EVENT_SERVICE_STARTED    = "service_started"
LOG_EVENT_CONN_DOWN          = "connection_down"
LOG_EVENT_CONN_RESTORED      = "connection_restored"
LOG_EVENT_PING_FAILED        = "ping_failed"
LOG_EVENT_DNS_FAILED         = "dns_failed"
LOG_EVENT_SPEEDTEST_OK       = "speedtest_complete"
LOG_EVENT_SPEEDTEST_TIMEOUT  = "speedtest_timeout"
LOG_EVENT_SPEEDTEST_FAILED   = "speedtest_failed"
LOG_EVENT_M6_AUTH_EXPIRED    = "m6_auth_expired"
LOG_EVENT_METRICS_PUSH_FAIL  = "metrics_push_failed"
LOG_EVENT_METRICS_BUFFERED   = "metrics_buffered"
LOG_EVENT_BUFFER_FLUSHED     = "buffer_flushed"
LOG_EVENT_PARTITION_MISSING  = "partition_not_detected"
