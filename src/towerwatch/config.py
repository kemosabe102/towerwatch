"""Towerwatch configuration constants."""

import os
import subprocess
import sys
from pathlib import Path


# --- Build version (stamped by ci.sh into _version.txt; git fallback for dev) -----
# Authoritative at deploy time: ci.sh writes "<short-hash> <iso-date>" into
# src/towerwatch/_version.txt before deploy.sh ships the tree. On the Pi the file
# ships inside the installed package at
# /opt/towerwatch/.venv/lib/pythonX.Y/site-packages/towerwatch/_version.txt.
# If the file is missing we try `git rev-parse` for local dev; if that fails too, we
# mark the build as "dev"/"unknown" rather than crash.
def _load_build_version(
    *,
    candidates=None,
    env=None,
    check_output=subprocess.check_output,
) -> tuple[str, str]:
    """Load (BUILD_VERSION, BUILD_DATE) from version.txt or git.

    All I/O is injectable for tests:
      - `candidates`: list of Paths to try for version.txt
      - `env`: dict-like, consulted for TOWERWATCH_SKIP_GIT_VERSION
      - `check_output`: stand-in for subprocess.check_output
    """
    if candidates is None:
        candidates = [
            Path(__file__).parent / "_version.txt",  # shipped inside the package
            Path("/opt/towerwatch/_version.txt"),  # legacy Pi install path
        ]
    if env is None:
        env = os.environ

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
    if env.get("TOWERWATCH_SKIP_GIT_VERSION") == "1":
        return "dev", "unknown"
    try:
        # src/towerwatch/config.py → parents[0]=towerwatch, [1]=src, [2]=repo root
        repo_root = Path(__file__).resolve().parents[2]
        version = (
            check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=repo_root,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
            .decode()
            .strip()
        )
        build_date = (
            check_output(
                ["git", "log", "-1", "--format=%cI"],
                cwd=repo_root,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
            .decode()
            .strip()
        )
        return version or "dev", build_date or "unknown"
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return "dev", "unknown"


BUILD_VERSION, BUILD_DATE = _load_build_version()

# --- Gateway IP (override from credentials, else auto-discover, else fallback) ---
# Resolved before PROBE_TARGETS so the gateway target tracks the live value.
# The "gateway" label stays stable — only the IP varies per site.
from towerwatch.net import discover_default_gateway  # noqa: E402


def _resolve_gateway_ip() -> str:
    try:
        from towerwatch import credentials

        override = getattr(credentials, "GATEWAY_IP_OVERRIDE", None)
        if override:
            return override
    except ImportError:
        pass
    return discover_default_gateway(fallback="192.168.1.1")


GATEWAY_IP = _resolve_gateway_ip()


def _load_credential(field: str, fallback: str) -> str:
    """Read a string credential with a safe fallback when the file or attribute
    is missing. Used for Prometheus label values that must be stable strings.
    """
    try:
        from towerwatch import credentials

        return getattr(credentials, field, fallback) or fallback
    except ImportError:
        return fallback


def _load_int_credential(field: str, fallback: int) -> int:
    """Read an integer credential with a safe fallback. Treats None and missing
    attributes identically (returns fallback) so credentials.py.example's
    `FIELD = None` placeholders don't crash the daemon. Sites override by
    setting an explicit int.
    """
    try:
        from towerwatch import credentials

        value = getattr(credentials, field, None)
        return int(value) if value is not None else fallback
    except ImportError:
        return fallback


def _load_windows_credential(
    field: str, fallback: list[tuple[int, int]] | None
) -> list[tuple[int, int]] | None:
    """Read a list-of-(start_hour, end_hour) tuples credential. Returns None
    when the credential is missing or explicitly None, so the scheduler falls
    back to its equal-slot default. Hours are 24-hour local time, end exclusive.
    """
    try:
        from towerwatch import credentials

        value = getattr(credentials, field, None)
        if value is None:
            return fallback
        return [(int(s), int(e)) for s, e in value]
    except ImportError:
        return fallback


# --- Probe Targets (multi-target for evidence isolation) ---
# Each tuple: (ip, label). Labels become Prometheus tag values — must be stable strings.
PROBE_TARGETS = [
    ("8.8.8.8", "google"),
    ("1.1.1.1", "cloudflare"),
    (GATEWAY_IP, "gateway"),  # carrier gateway (auto-discovered)
]

PING_COUNT = 10  # Probes per burst
PING_TIMEOUT_S = 10  # Total timeout for ping command

# --- TCP Probe ---
TCP_TARGET_HOST = "8.8.8.8"
TCP_TARGET_PORT = 443
TCP_TIMEOUT_S = 5

# --- DNS Resolution ---
# Google + Cloudflare public resolvers, plus Verizon's resolver for an A/B
# comparison — if the carrier resolver is the source of multi-second DNS spikes,
# the per-resolver series (dns_resolve_ms_198_224_166_135) will show it.
DNS_TARGETS = ["8.8.8.8", "1.1.1.1", "198.224.166.135"]
DNS_QUERY_DOMAIN = "example.com"
DNS_TIMEOUT_S = 5

# --- Intervals ---
METRIC_INTERVAL_S = 60  # Main loop: ping, TCP, DNS (was 30 — halved for data cap)

# --- HTTP Latency Probe (frequent, small file) ---
HTTP_LATENCY_URL = "https://speed.cloudflare.com/__down?bytes=10000"  # 10 KB
HTTP_LATENCY_INTERVAL_S = 300  # 5 minutes
HTTP_LATENCY_TIMEOUT_S = 30

# --- Cloudflare Adaptive Throughput Probe (replaces single-stream HTTP + Ookla) ---
# Multi-stream adaptive probe against speed.cloudflare.com, faithful to the
# protocol speed.cloudflare.com uses in-browser. 4 parallel TCP streams, ramp
# 25 MB → 100 MB until target_s is reached, discard the first warmup_discard_s
# of bytes from the rate calc to skip TCP slow-start.
#
# Data budget: each test costs up to MAX_TOTAL_BYTES per direction (download
# + upload). At 2 tests/day with 400 MB down + 150 MB up caps that's ~33 GB/mo
# worst case. Per-site override via credentials.CLOUDFLARE_THROUGHPUT_MAX_TOTAL_BYTES_OVERRIDE
# lets metered sites trade accuracy for data savings.
CLOUDFLARE_THROUGHPUT_DL_URL = "https://speed.cloudflare.com/__down"
CLOUDFLARE_THROUGHPUT_UL_URL = "https://speed.cloudflare.com/__up"
CLOUDFLARE_THROUGHPUT_STREAMS = 4
CLOUDFLARE_THROUGHPUT_RAMP_BYTES = (25_000_000, 100_000_000)
CLOUDFLARE_THROUGHPUT_MAX_TOTAL_BYTES = _load_int_credential(
    "CLOUDFLARE_THROUGHPUT_MAX_TOTAL_BYTES_OVERRIDE", 400_000_000
)
CLOUDFLARE_THROUGHPUT_TARGET_S = 5.0
CLOUDFLARE_THROUGHPUT_WARMUP_DISCARD_S = 1.5
CLOUDFLARE_THROUGHPUT_TIMEOUT_S = 90
# Tests per day. Per-site override via CLOUDFLARE_THROUGHPUT_TESTS_PER_DAY_OVERRIDE.
# Common settings: home (gigabit, low-interest variation) = 1; standstill (cellular,
# diurnal patterns) = 3 paired with WINDOWS for morning/midday/evening sampling.
CLOUDFLARE_THROUGHPUT_TESTS_PER_DAY = _load_int_credential(
    "CLOUDFLARE_THROUGHPUT_TESTS_PER_DAY_OVERRIDE", 2
)
# Optional named time windows (24-hour local, end exclusive). When set, the
# scheduler picks one random time within each window per day, instead of the
# default equal-slot subdivision. Length must equal TESTS_PER_DAY when both are
# set. Example for diurnal cellular sampling: [(6, 10), (11, 14), (17, 21)].
CLOUDFLARE_THROUGHPUT_WINDOWS: list[tuple[int, int]] | None = _load_windows_credential(
    "CLOUDFLARE_THROUGHPUT_WINDOWS_OVERRIDE", None
)

# --- Bufferbloat / latency-under-load ---
# Piggybacks the scheduled Cloudflare throughput run: a background thread pings
# BUFFERBLOAT_TARGET while the download (then upload) saturates the link, and we
# compare the loaded RTT against an idle baseline taken just before. Zero extra
# data cost beyond the ICMP traffic, which is negligible. Fires at the throughput
# cadence (~2/day), so it adds nothing meaningful to the data budget.
BUFFERBLOAT_TARGET = "8.8.8.8"
BUFFERBLOAT_PING_INTERVAL_S = 0.25  # spacing between in-load ping samples
BUFFERBLOAT_BASELINE_COUNT = 5  # pings in the idle baseline burst
BUFFERBLOAT_PING_TIMEOUT_S = 2  # per single-ping timeout

# Upload caps tend to be lower than download on cellular/cable, so we use a
# smaller default total-bytes cap and (optionally) fewer streams to avoid
# wasting upstream while still saturating the link.
CLOUDFLARE_UPLOAD_STREAMS = 4
CLOUDFLARE_UPLOAD_RAMP_BYTES = (10_000_000, 50_000_000)
CLOUDFLARE_UPLOAD_MAX_TOTAL_BYTES = _load_int_credential(
    "CLOUDFLARE_UPLOAD_MAX_TOTAL_BYTES_OVERRIDE", 150_000_000
)

# --- Link calibration (per-site, baked into build_info tags for the dashboard) ---
# These declare the rough expected capacity of the link so dashboards can scale
# gauges and compute Saturation = current_throughput / LINK_MAX. Values come
# from credentials.EXPECTED_DOWNLINK_MBPS / EXPECTED_UPLINK_MBPS. Defaults
# correspond to the standstill profile (200 Mbps cellular / 30 Mbps upstream).
LINK_MAX_DOWNLOAD_MBPS = _load_int_credential("EXPECTED_DOWNLINK_MBPS", 200)
LINK_MAX_UPLOAD_MBPS = _load_int_credential("EXPECTED_UPLINK_MBPS", 50)

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
# GATEWAY_IP is set above (auto-discovered) before PROBE_TARGETS.
GATEWAY_TCP_PORT = 80
GATEWAY_TIMEOUT_S = 5


def _resolve_gateway_vendor() -> str:
    """Pick the gateway-probe variant from CONNECTION_TYPE.

    Cellular sites get the M6 probe (rich /api/model.json schema). Cable
    sites get the Orbi DEV_INFO probe. Anything else falls back to the
    baseline TCP/HTTP probe. Manual override via GATEWAY_VENDOR_OVERRIDE
    in credentials still wins, for sites with non-standard kit.
    """
    try:
        from towerwatch import credentials

        manual = getattr(credentials, "GATEWAY_VENDOR_OVERRIDE", None)
        if manual is not None:
            return manual
        ct = getattr(credentials, "CONNECTION_TYPE", "").lower()
    except ImportError:
        ct = ""
    if ct in ("5g_cellular", "lte_cellular"):
        return "m6"
    if ct == "cable":
        return "orbi"
    return ""


GATEWAY_VENDOR = _resolve_gateway_vendor()

# --- M6 Signal Metrics ---
# /api/model.json bundles wwan + wwanadv + wwan.ca + wwan.diagInfo in a
# single response — richer than the legacy /api/wwanadv.json endpoint and
# the same HTTP cost. Read access is anonymous on Nighthawk M6 firmware
# 2.0+ (apiVersion field).
M6_ADMIN_URL = f"http://{GATEWAY_IP}/api/model.json"
M6_TIMEOUT_S = 5

# --- Grafana Cloud (metrics use _ms suffix throughout, not Prometheus-standard seconds) ---
GRAFANA_PUSH_URL = os.environ.get(
    "GRAFANA_PUSH_URL_OVERRIDE",
    "https://prometheus-prod-67-prod-us-west-0.grafana.net/api/v1/push/influx/write?precision=s",
)
GRAFANA_PUSH_TIMEOUT_S = 10
INFLUX_MEASUREMENT = "towerwatch"


# LOCATION is the `host` Influx tag and Loki stream label.
INFLUX_HOST_TAG = _load_credential("LOCATION", "towerwatch")


# CARRIER and CONNECTION_TYPE bake into every metric line and Loki stream as
# Prometheus labels — lets dashboards group/filter by carrier without joins.
# Default to "unknown" so historical credentials files predating these fields
# keep working. Slugify spaces just in case (label values must not contain
# whitespace in Influx line protocol).
def _slug(s: str) -> str:
    return s.strip().lower().replace(" ", "_") or "unknown"


INFLUX_CARRIER_TAG = _slug(_load_credential("CARRIER", "unknown"))
INFLUX_CONNECTION_TYPE_TAG = _slug(_load_credential("CONNECTION_TYPE", "unknown"))

# --- Push Optimization (batching + compression) ---
PUSH_BATCH_SIZE = 2  # Accumulate N lines before pushing (at 60s = push every 2 min)
PUSH_COMPRESS = True  # gzip Influx POST body

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
LOKI_PUSH_LEVEL = "INFO"  # Minimum level to push to Loki — keep per-tick logs out of Loki by using DEBUG/local-only for them

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
LOG_EVENT_SERVICE_STARTED = "service_started"
LOG_EVENT_SERVICE_RESTARTED = "service_restarted"
LOG_EVENT_CONN_DOWN = "connection_down"
LOG_EVENT_CONN_RESTORED = "connection_restored"
LOG_EVENT_PING_FAILED = "ping_failed"
LOG_EVENT_DNS_FAILED = "dns_failed"
LOG_EVENT_SPEEDTEST_OK = "speedtest_complete"
LOG_EVENT_SPEEDTEST_TIMEOUT = "speedtest_timeout"
LOG_EVENT_SPEEDTEST_FAILED = "speedtest_failed"
LOG_EVENT_M6_AUTH_EXPIRED = "m6_auth_expired"
LOG_EVENT_METRICS_PUSH_FAIL = "metrics_push_failed"
LOG_EVENT_LOG_BUFFER_FLUSHED = "log_buffer_flushed"
LOG_EVENT_PARTITION_MISSING = "partition_not_detected"
LOG_EVENT_HTTP_THROUGHPUT_OK = "http_throughput_complete"
LOG_EVENT_HTTP_THROUGHPUT_FAILED = "http_throughput_failed"
LOG_EVENT_HTTP_UPLOAD_OK = "http_upload_complete"
LOG_EVENT_HTTP_UPLOAD_FAILED = "http_upload_failed"
LOG_EVENT_HEARTBEAT = "service_heartbeat"
LOG_EVENT_OUTAGE_RECORDED = "outage_recorded"
LOG_EVENT_ANNOTATION_FAILED = "annotation_push_failed"

# --- Heartbeat ---
HEARTBEAT_INTERVAL_S = 3600  # Emit a WARN-level heartbeat to Loki once per hour

# --- Bench harness (read base URL derived from existing public stack hostname) ---
GRAFANA_READ_BASE_URL = "https://towerwatch.grafana.net"
if sys.platform == "win32":
    BENCH_REPORT_DIR = "./data/bench/reports"
else:
    BENCH_REPORT_DIR = "/opt/towerwatch/data/bench/reports"
