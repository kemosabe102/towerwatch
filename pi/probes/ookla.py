"""Ookla Speedtest probe — manual only, not scheduled in main loop."""

import json
import logging
import subprocess

import config
from loki import push_log, log_and_push
from probes.base import Probe, ProbeResult

log = logging.getLogger("towerwatch")


# MANUAL-ONLY: invoked via REPL, not scheduled in _collect_probes.
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
        if result.returncode != 0:
            log.error("Speedtest failed with returncode %d", result.returncode)
            push_log("WARN", f"Speedtest failed (rc={result.returncode})",
                     {"event": config.LOG_EVENT_SPEEDTEST_FAILED,
                      "returncode": result.returncode})
            return {"download_mbps": 0, "upload_mbps": 0, "success": 0}
        data = json.loads(result.stdout)
        dl = round(data["download"]["bandwidth"] * 8 / 1_000_000, 2)
        ul = round(data["upload"]["bandwidth"] * 8 / 1_000_000, 2)
        log_and_push("INFO", f"Speedtest: {dl} Mbps down, {ul} Mbps up",
                     event=config.LOG_EVENT_SPEEDTEST_OK, download_mbps=dl, upload_mbps=ul)
        return {"download_mbps": dl, "upload_mbps": ul, "success": 1}
    except subprocess.TimeoutExpired:
        log.error("Speedtest timed out after %ds", config.SPEEDTEST_TIMEOUT_S)
        push_log("WARN", f"Speedtest timed out after {config.SPEEDTEST_TIMEOUT_S}s",
                 {"event": config.LOG_EVENT_SPEEDTEST_TIMEOUT,
                  "timeout_s": config.SPEEDTEST_TIMEOUT_S})
        return {"download_mbps": 0, "upload_mbps": 0, "success": 0}
    except Exception as e:
        log.error("Speedtest failed: %s", e)
        push_log("WARN", f"Speedtest failed: {e}",
                 {"event": config.LOG_EVENT_SPEEDTEST_FAILED, "error": str(e)})
        return {"download_mbps": 0, "upload_mbps": 0, "success": 0}


class OoklaProbe:
    name = "ookla"

    def run(self) -> ProbeResult:
        f = run_speedtest()
        return ProbeResult(fields=f, ok=f["success"] == 1)
