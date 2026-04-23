"""Ookla Speedtest probe — manual only, not scheduled in main loop."""

import json
import logging
import subprocess

import config
from probes.base import Probe, ProbeResult

log = logging.getLogger("towerwatch")


class _ModuleLokiSink:
    """Lazy façade: uses module-level loki push/log_and_push."""

    def log_and_push(self, level, message, **fields):
        from loki import log_and_push
        log_and_push(level, message, **fields)

    def push(self, level, message, extra=None):
        from loki import push_log
        push_log(level, message, extra)


def run_speedtest(
    *,
    binary: str | None = None,
    server_id: int | None = None,
    timeout_s: int | None = None,
    subprocess_run=subprocess.run,
    loki=None,
) -> dict:
    """Run Ookla speedtest CLI. Returns {download_mbps, upload_mbps, success}."""
    if binary is None:
        binary = config.SPEEDTEST_BINARY
    if server_id is None:
        server_id = config.SPEEDTEST_SERVER_ID
    if timeout_s is None:
        timeout_s = config.SPEEDTEST_TIMEOUT_S
    if loki is None:
        loki = _ModuleLokiSink()

    cmd = [binary, "--format=json", "--accept-license"]
    if server_id:
        cmd += ["--server-id", str(server_id)]
    try:
        result = subprocess_run(
            cmd, capture_output=True, text=True, timeout=timeout_s,
        )
        if result.returncode != 0:
            log.error("Speedtest failed with returncode %d", result.returncode)
            loki.push("WARN", f"Speedtest failed (rc={result.returncode})",
                      {"event": config.LOG_EVENT_SPEEDTEST_FAILED,
                       "returncode": result.returncode})
            return {"download_mbps": 0, "upload_mbps": 0, "success": 0}
        data = json.loads(result.stdout)
        dl = round(data["download"]["bandwidth"] * 8 / 1_000_000, 2)
        ul = round(data["upload"]["bandwidth"] * 8 / 1_000_000, 2)
        loki.log_and_push("INFO", f"Speedtest: {dl} Mbps down, {ul} Mbps up",
                          event=config.LOG_EVENT_SPEEDTEST_OK,
                          download_mbps=dl, upload_mbps=ul)
        return {"download_mbps": dl, "upload_mbps": ul, "success": 1}
    except subprocess.TimeoutExpired:
        log.error("Speedtest timed out after %ds", timeout_s)
        loki.push("WARN", f"Speedtest timed out after {timeout_s}s",
                  {"event": config.LOG_EVENT_SPEEDTEST_TIMEOUT,
                   "timeout_s": timeout_s})
        return {"download_mbps": 0, "upload_mbps": 0, "success": 0}
    except Exception as e:
        log.error("Speedtest failed: %s", e)
        loki.push("WARN", f"Speedtest failed: {e}",
                  {"event": config.LOG_EVENT_SPEEDTEST_FAILED, "error": str(e)})
        return {"download_mbps": 0, "upload_mbps": 0, "success": 0}


class OoklaProbe:
    name = "ookla"

    def __init__(
        self,
        binary: str | None = None,
        server_id: int | None = None,
        timeout_s: int | None = None,
        subprocess_run=subprocess.run,
        loki=None,
    ):
        self._binary = binary
        self._server_id = server_id
        self._timeout_s = timeout_s
        self._subprocess_run = subprocess_run
        self._loki = loki

    def run(self) -> ProbeResult:
        f = run_speedtest(
            binary=self._binary,
            server_id=self._server_id,
            timeout_s=self._timeout_s,
            subprocess_run=self._subprocess_run,
            loki=self._loki,
        )
        return ProbeResult(fields=f, ok=f["success"] == 1)
