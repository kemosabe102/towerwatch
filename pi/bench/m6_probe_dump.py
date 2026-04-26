"""Throwaway: discover the working auth + endpoint for the live M6.

Runs through ONE password (passed as argv[1] or read from credentials.py),
trying Basic, Digest, and Netgear-style form login on /Forms/config in order.
On the first 200 + JSON response, pretty-prints the body and the chain of
fields it found.

This script is intentionally narrow: a single credential, multiple auth
methods, multiple v1 endpoints. The credential sweep version got us
locked out by the M6's rate-limiter; this one stays well under that.

Run on the Pi:
    python3 pi/bench/m6_probe_dump.py [password]
"""

from __future__ import annotations

import json
import sys
from typing import Any

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

GATEWAY = "192.168.1.1"
USER = "admin"
V1_PATHS = [
    "/v1/api/wwanadv",
    "/v1/api/wwan",
    "/v1/api/model",
    "/v1/api/cellinfo",
    "/v1/api/diagnostics",
]
LEGACY_PATHS = [
    "/api/model.json",
    "/api/wwanadv.json",
]


def _looks_like_json(text: str) -> bool:
    s = text.lstrip()
    return s.startswith("{") or s.startswith("[")


def _try_paths(session_or_kwargs: Any, label: str, paths: list[str]) -> tuple | None:
    for path in paths:
        url = f"http://{GATEWAY}{path}"
        try:
            if isinstance(session_or_kwargs, requests.Session):
                r = session_or_kwargs.get(url, timeout=4)
            else:
                r = requests.get(url, timeout=4, **session_or_kwargs)
        except Exception as e:
            print(f"  {label} {path:30s} -> ERR {type(e).__name__}: {e}")
            return None
        tag = "JSON" if _looks_like_json(r.text) else "html/other"
        print(f"  {label} {path:30s} -> {r.status_code} [{tag}] {len(r.text)}B")
        if r.status_code == 200 and _looks_like_json(r.text):
            return (label, path, r.text)
    return None


def try_basic(password: str):
    print(f"\n=== Basic auth (admin/{password}) ===")
    return _try_paths({"auth": HTTPBasicAuth(USER, password)}, "BASIC", V1_PATHS)


def try_digest(password: str):
    print(f"\n=== Digest auth (admin/{password}) ===")
    return _try_paths({"auth": HTTPDigestAuth(USER, password)}, "DIGEST", V1_PATHS)


def try_form_login(password: str):
    print(f"\n=== Form login at /Forms/config (admin/{password}) ===")
    s = requests.Session()
    try:
        s.get(f"http://{GATEWAY}/index.html", timeout=4)
    except Exception as e:
        print(f"  init failed: {e}")
        return None
    xsrf = s.cookies.get("XSRF_TOKEN")
    print(f"  XSRF_TOKEN={xsrf}")
    if not xsrf:
        return None

    # Standard Netgear M-series form login. Field name varies by firmware:
    # M1 uses session.password, some M6 firmwares use just "password".
    for body in [
        {"session.password": password, "token": xsrf},
        {"password": password, "token": xsrf},
        {"session.password": password, "session.token": xsrf},
    ]:
        print(f"  POST /Forms/config body={list(body.keys())}")
        try:
            r = s.post(
                f"http://{GATEWAY}/Forms/config",
                data=body,
                timeout=4,
                allow_redirects=False,
            )
        except Exception as e:
            print(f"    -> ERR {e}")
            continue
        print(
            f"    -> {r.status_code} cookies={list(s.cookies.keys())} "
            f"location={r.headers.get('Location')}"
        )
        # Probe v1 endpoints with the session cookies
        hit = _try_paths(s, "FORM", V1_PATHS)
        if hit:
            return hit
    return None


def main():
    if len(sys.argv) >= 2:
        password = sys.argv[1]
    else:
        try:
            from towerwatch import credentials  # type: ignore[import]

            password = credentials.M6_ADMIN_PASSWORD
        except Exception as e:
            print(f"FATAL: pass password as argv[1] or import from credentials: {e}")
            sys.exit(2)

    for method in (try_basic, try_digest, try_form_login):
        hit = method(password)
        if hit:
            label, path, body = hit
            print(f"\n*** SUCCESS: {label} on {path} ***")
            try:
                parsed = json.loads(body)
                print(json.dumps(parsed, indent=2)[:12000])
                # Save full payload for fixture extraction
                with open(f"/tmp/m6_dump_{label.lower()}.json", "w") as f:
                    json.dump(parsed, f, indent=2)
                print(f"\nFull JSON saved to /tmp/m6_dump_{label.lower()}.json")
            except Exception:
                print(body[:8000])
            return 0

    print("\n*** No working auth combination found ***")
    return 1


if __name__ == "__main__":
    sys.exit(main())
