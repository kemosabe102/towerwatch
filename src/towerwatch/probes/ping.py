"""ICMP Ping probe — cross-platform latency, jitter, packet loss.

Class-based with an injected `subprocess_run` callable. Parsing helpers
(`_parse_rtt_stats`, `_parse_ping_output`, `_calc_jitter`) remain pure
module-level functions — they take all relevant inputs as arguments.
"""

import logging
import re
import statistics
import subprocess
import sys

from towerwatch import config
from towerwatch.probes.base import Probe, ProbeResult

log = logging.getLogger("towerwatch")

IS_WINDOWS = sys.platform == "win32"


class _ModuleLokiSink:
    def log_and_push(self, level, message, **fields):
        from towerwatch.clients.loki import log_and_push
        log_and_push(level, message, **fields)


def _build_ping_cmd(target: str, count: int, timeout_s: int,
                    is_windows: bool) -> list[str]:
    if is_windows:
        return ["ping", "-n", str(count),
                "-w", str(timeout_s * 1000), target]
    return ["ping", "-c", str(count),
            "-W", str(timeout_s), target]


def _parse_rtt_stats(stdout: str, is_windows: bool) -> tuple[int, int, int, float]:
    """Parse platform-specific RTT summary line. Returns (min, avg, max, mdev)."""
    if is_windows:
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


def _parse_ping_output(stdout: str, is_windows: bool = None) -> dict:
    """Parse ping output into {rtt_avg, rtt_min, rtt_max, jitter, pkt_loss, connected}."""
    if is_windows is None:
        is_windows = IS_WINDOWS
    loss_match = re.search(r"(\d+)%\s*(?:packet )?loss", stdout)
    pkt_loss = int(loss_match.group(1)) if loss_match else 100

    rtt_min, rtt_avg, rtt_max, mdev = _parse_rtt_stats(stdout, is_windows)

    if is_windows:
        rtts = [float(m) for m in re.findall(r"time=([\d.]+)ms", stdout)]
        rtts += [0.5 for _ in re.findall(r"time<1ms", stdout)]
        if rtts and rtt_avg == 0 and all(r < 1 for r in rtts):
            rtt_min = rtt_avg = rtt_max = 1
    else:
        rtts = [float(m) for m in re.findall(r"time=([\d.]+)", stdout)]

    return {
        "rtt_avg": rtt_avg, "rtt_min": rtt_min, "rtt_max": rtt_max,
        "jitter": _calc_jitter(rtts, mdev), "pkt_loss": pkt_loss,
        "connected": pkt_loss < 100,
    }


def _zero_result() -> dict:
    return {"rtt_avg": 0, "rtt_min": 0, "rtt_max": 0,
            "jitter": 0, "pkt_loss": 100, "connected": False}


class PingProbe:
    """Run ICMP ping burst and return labelled metrics."""

    def __init__(
        self,
        target_ip: str,
        label: str,
        subprocess_run=subprocess.run,
        loki=None,
        count: int | None = None,
        timeout_s: int | None = None,
        is_windows: bool | None = None,
    ):
        self.target_ip = target_ip
        self.label = label
        self.name = f"ping_{label}"
        self._subprocess_run = subprocess_run
        self._loki = loki if loki is not None else _ModuleLokiSink()
        self._count = count if count is not None else config.PING_COUNT
        self._timeout_s = timeout_s if timeout_s is not None else config.PING_TIMEOUT_S
        self._is_windows = is_windows if is_windows is not None else IS_WINDOWS

    def run_ping(self) -> dict:
        """Run one ping burst. Returns the parsed field dict."""
        try:
            result = self._subprocess_run(
                _build_ping_cmd(self.target_ip, self._count, self._timeout_s,
                                self._is_windows),
                capture_output=True, text=True,
                timeout=self._timeout_s * self._count + 5,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            self._loki.log_and_push(
                "WARN", f"Ping {self.target_ip} failed",
                event=config.LOG_EVENT_PING_FAILED,
                target=self.target_ip, error=str(e),
            )
            return _zero_result()
        return _parse_ping_output(result.stdout, is_windows=self._is_windows)

    def run(self) -> ProbeResult:
        fields_raw = self.run_ping()
        labeled = {f"{k}_{self.label}": v for k, v in fields_raw.items()
                   if k != "connected"}
        labeled[f"connected_{self.label}"] = 1 if fields_raw["connected"] else 0
        return ProbeResult(fields=labeled, ok=fields_raw["connected"])


# ---------------------------------------------------------------------------
# Back-compat module-level function
# ---------------------------------------------------------------------------
def run_ping(target: str) -> dict:
    """Legacy API. Prefer `PingProbe(target, label).run_ping()`."""
    return PingProbe(target, label="legacy").run_ping()
