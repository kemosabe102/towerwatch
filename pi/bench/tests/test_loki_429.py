"""Test 4: loki_429 — redirect Loki push to a local 429 responder.

EXPECTED FAILURE: towerwatch silently swallows non-2xx Loki responses.
This test PASSES while the bug is present (no log_push_failed event emitted).
It FAILS once a PR adds non-2xx Loki error logging.
"""

import subprocess
import threading
import time

from ..harness.snapshot import write_dropin, remove_dropin
from ..harness.observe import ObserveError
from .base import BenchTest

DROPIN_NAME = "loki429"
LOCAL_PORT = 19998
INJECT_DURATION_S = 180


def _serve_429(stop_event):
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
                        b"HTTP/1.1 429 Too Many Requests\r\n"
                        b"Content-Length: 0\r\nConnection: close\r\n\r\n"
                    )
            except OSError:
                pass


class Test(BenchTest):
    name = "loki_429"
    description = "Redirect Loki push to local 429; expected-failure: silent swallow (no error event)"
    expected_failure = True   # Bug present → test passes; bug fixed → test fails (flip to FAIL)
    timeout_s = 600

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._stop_event = threading.Event()

    def inject(self) -> None:
        threading.Thread(target=_serve_429, args=(self._stop_event,), daemon=True).start()
        override = f"http://127.0.0.1:{LOCAL_PORT}/loki/api/v1/push"
        write_dropin(DROPIN_NAME, f"[Service]\nEnvironment=LOKI_URL_OVERRIDE={override}\n")
        subprocess.run(["systemctl", "restart", "towerwatch"], check=True)
        time.sleep(INJECT_DURATION_S)

    def observe(self) -> dict:
        # Expect the event to be ABSENT — silent swallow is the current behaviour
        end_ns = int(time.time() * 1e9)
        try:
            self.obs.assert_loki_event_absent(
                event_name="log_push_failed",
                start_ns=self._inject_start_ns,
                end_ns=end_ns,
            )
        except ObserveError:
            # Event WAS present — bug is fixed. Re-raise so base class flips to fail.
            raise
        return {"silent_swallow_confirmed": True}

    def restore(self) -> None:
        self._stop_event.set()
        remove_dropin(DROPIN_NAME)
        subprocess.run(["systemctl", "restart", "towerwatch"], check=False)
