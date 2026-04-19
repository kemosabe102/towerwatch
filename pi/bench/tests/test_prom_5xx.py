"""Test 3: prom_5xx — redirect Prom push host to a local 503 responder.

Uses a systemd drop-in to override GRAFANA_PUSH_URL to a local netcat/socat 503 server.
Pass: metrics_push_failed with 5xx indication, no crash.
"""

import subprocess
import threading
import time

from ..harness.snapshot import write_dropin, remove_dropin
from .base import BenchTest

DROPIN_NAME = "prom5xx"
LOCAL_PORT = 19999
INJECT_DURATION_S = 120


def _serve_503(stop_event):
    """Minimal HTTP/1.1 server that always returns 503."""
    import socket
    with socket.socket() as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", LOCAL_PORT))
        srv.listen(5)
        srv.settimeout(1)
        while not stop_event.is_set():
            try:
                conn, _ = srv.accept()
                with conn:
                    conn.recv(4096)
                    conn.sendall(
                        b"HTTP/1.1 503 Service Unavailable\r\n"
                        b"Content-Length: 0\r\nConnection: close\r\n\r\n"
                    )
            except OSError:
                pass


class Test(BenchTest):
    name = "prom_5xx"
    description = "Redirect Prom push to local 503 responder; metrics_push_failed, no crash"
    timeout_s = 420

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._stop_event = threading.Event()
        self._server_thread = None

    def inject(self) -> None:
        self._server_thread = threading.Thread(
            target=_serve_503, args=(self._stop_event,), daemon=True
        )
        self._server_thread.start()
        override_url = f"http://127.0.0.1:{LOCAL_PORT}/api/v1/push/influx/write?precision=s"
        dropin_content = f"[Service]\nEnvironment=GRAFANA_PUSH_URL_OVERRIDE={override_url}\n"
        write_dropin(DROPIN_NAME, dropin_content)
        subprocess.run(["systemctl", "restart", "towerwatch"], check=True)
        time.sleep(INJECT_DURATION_S)

    def observe(self) -> dict:
        entry = self.obs.poll_loki_event(
            event_name="metrics_push_failed",
            start_ns=self._inject_start_ns,
            timeout_s=180,
            poll_interval_s=30,
        )
        return {"push_fail_entry": entry}

    def restore(self) -> None:
        self._stop_event.set()
        remove_dropin(DROPIN_NAME)
        subprocess.run(["systemctl", "restart", "towerwatch"], check=False)
