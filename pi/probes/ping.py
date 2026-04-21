"""ICMP Ping probe — cross-platform latency, jitter, packet loss."""

import logging
import re
import statistics
import subprocess
import sys

import config
from loki import log_and_push
from probes.base import Probe, ProbeResult

log = logging.getLogger("towerwatch")

IS_WINDOWS = sys.platform == "win32"


def _build_ping_cmd(target: str) -> list[str]:
    if IS_WINDOWS:
        return ["ping", "-n", str(config.PING_COUNT),
                "-w", str(config.PING_TIMEOUT_S * 1000), target]
    else:
        return ["ping", "-c", str(config.PING_COUNT),
                "-W", str(config.PING_TIMEOUT_S), target]


def _parse_rtt_stats(stdout: str) -> tuple[int, int, int, float]:
    """Parse platform-specific RTT summary line. Returns (min, avg, max, mdev)."""
    if IS_WINDOWS:
        m = re.search(
            r"Minimum\s*=\s*([\d.]+)ms.*Maximum\s*=\s*([\d.]+)ms.*Average\s*=\s*([\d.]+)ms",
            stdout, re.DOTALL,
        )
        if m:
            return int(float(m.group(1))), int(float(m.group(3))), int(float(m.group(2))), 0.0
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
        # time=Nms for >=1ms replies; time<1ms for sub-ms — treat as 0.5ms
        rtts = [float(m) for m in re.findall(r"time=([\d.]+)ms", stdout)]
        rtts += [0.5 for _ in re.findall(r"time<1ms", stdout)]
        # If the summary line shows 0ms but we have sub-ms replies, use 1 as floor
        if rtts and rtt_avg == 0 and all(r < 1 for r in rtts):
            rtt_min = rtt_avg = rtt_max = 1
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
        log_and_push("WARN", f"Ping {target} failed",
                     event=config.LOG_EVENT_PING_FAILED, target=target, error=str(e))
        return {"rtt_avg": 0, "rtt_min": 0, "rtt_max": 0,
                "jitter": 0, "pkt_loss": 100, "connected": False}

    return _parse_ping_output(result.stdout)


class PingProbe:
    """Class-based ping probe implementing the Probe protocol."""

    def __init__(self, target_ip: str, label: str,
                 count: int = None, timeout_s: int = None):
        self.target_ip = target_ip
        self.label = label
        self._count = count or config.PING_COUNT
        self._timeout_s = timeout_s or config.PING_TIMEOUT_S
        self.name = f"ping_{label}"

    def run(self) -> ProbeResult:
        fields_raw = run_ping(self.target_ip)
        labeled = {f"{k}_{self.label}": v for k, v in fields_raw.items()
                   if k != "connected"}
        labeled[f"connected_{self.label}"] = 1 if fields_raw["connected"] else 0
        return ProbeResult(
            fields=labeled,
            ok=fields_raw["connected"],
        )
