"""systemctl helpers for the bench harness."""

import subprocess


def service_active(unit: str = "towerwatch") -> bool:
    r = subprocess.run(
        ["systemctl", "is-active", unit],
        capture_output=True, text=True,
    )
    return r.stdout.strip() == "active"


def service_control(action: str, unit: str = "towerwatch", check: bool = True) -> None:
    subprocess.run(["systemctl", action, unit], check=check)


def daemon_reload(check: bool = True) -> None:
    subprocess.run(["systemctl", "daemon-reload"], check=check)
