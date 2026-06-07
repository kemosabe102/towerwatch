#!/usr/bin/env python3
"""Phone-vs-hotspot comparison — measure a USB-connected Android phone's CELLULAR
link the same way towerwatch measures the M6 hotspot, and push the results to the
same Grafana Cloud under a distinct host tag so `dashboard-compare.json` shows
them side by side.

Why this exists: the open question is whether the M6 hotspot is *deprioritized*
on the Verizon network vs the phone, or whether both degrade equally under
congestion (raw capacity). Running both at the same congested moment, same tower,
answers it. See docs/phone-compare.md.

How it works: ADB/USB is only the control channel — `adb shell <cmd>` runs ON the
phone and egresses over the phone's own radio. With Wi-Fi disabled, that's the
cellular link. We run the SAME Cloudflare endpoints the hotspot probe uses
(speed.cloudflare.com/__down and /__up) plus a ping burst, then push Influx line
protocol tagged `host=<--host-tag>` (default "standstill-phone") with the SAME
metric names the hotspot emits, so the existing compare dashboard just works.

This is a standalone Mac-side tool. It is NOT part of the deployed service and
does not run on the Pi. Run it from the repo root with the phone on USB.

    python scripts/phone_compare.py --duration 600 --interval 60

Reuses: towerwatch.probes.ping._parse_ping_output (ping parsing),
towerwatch.clients.grafana.GrafanaClient (the exact push path + creds).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time

# Repo imports: run from the repo root, or with src/ importable.
try:
    from towerwatch import config
    from towerwatch.clients.grafana import GrafanaClient
    from towerwatch.probes.ping import _parse_ping_output
except ImportError:
    sys.path.insert(0, "src")
    from towerwatch import config
    from towerwatch.clients.grafana import GrafanaClient
    from towerwatch.probes.ping import _parse_ping_output

CF_DOWN = "https://speed.cloudflare.com/__down?bytes={n}"
CF_UP = "https://speed.cloudflare.com/__up"
REMOTE_CURL = "/data/local/tmp/curl"
PING_TARGET = "8.8.8.8"
PING_LABEL = "google"  # mirror the hotspot's rtt_avg_google so panels overlay
MAX_RTT_MS = 60_000  # artifact guard, same intent as the ping probe


# --------------------------------------------------------------------------
# adb helpers
# --------------------------------------------------------------------------


def _adb(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["adb", *args], capture_output=True, text=True, timeout=timeout)


def _adb_shell(cmd: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return _adb("shell", cmd, timeout=timeout)


# --------------------------------------------------------------------------
# Preflight
# --------------------------------------------------------------------------


def preflight(curl_binary: str) -> None:
    """Verify device, force cellular, push static curl. Exits on hard failure."""
    try:
        devices = _adb("devices", timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        sys.exit(
            "ERROR: `adb` not found or not responding. Install platform-tools "
            "(brew install android-platform-tools) and connect the phone."
        )
    lines = [ln for ln in devices.stdout.splitlines()[1:] if ln.strip() and "\t" in ln]
    if not lines:
        sys.exit(
            "ERROR: no device. Connect the phone via USB, enable USB Debugging "
            "(Settings -> Developer options), and approve the 'Trust this "
            "computer?' dialog, then re-run."
        )
    serial, state = lines[0].split("\t", 1)
    if state.strip() == "unauthorized":
        sys.exit(
            "ERROR: device unauthorized. On the phone, approve the 'Trust this "
            "computer?' dialog (tick 'Always allow'), then re-run."
        )
    if len(lines) > 1:
        sys.exit(f"ERROR: {len(lines)} devices connected — connect exactly one.")
    print(f"[preflight] device: {serial} ({state.strip()})")

    # Force cellular: disable Wi-Fi, then confirm a cellular interface is up.
    _adb_shell("svc wifi disable", timeout=15)
    print("[preflight] Wi-Fi disabled (will re-enable on exit)")
    ifaces = _adb_shell("ip -o a", timeout=15).stdout
    cellular = re.findall(r"\d+:\s+(rmnet\w*|ccmni\w*)\b", ifaces)
    wlan_up = re.search(r"\d+:\s+wlan0\s+inet\s", ifaces)
    if not cellular:
        print(
            "[preflight] WARNING: no rmnet*/ccmni* (cellular) interface found. "
            "The phone may have no cellular data. Interfaces:\n" + ifaces.strip()
        )
    else:
        print(f"[preflight] cellular interface up: {sorted(set(cellular))}")
    if wlan_up:
        print(
            "[preflight] WARNING: wlan0 still has an IP — measurements may use "
            "Wi-Fi, not cellular. Toggle Wi-Fi off on the phone and re-run."
        )

    # Push the static curl binary (idempotent).
    present = _adb_shell(f"[ -x {REMOTE_CURL} ] && echo yes || echo no").stdout.strip()
    if present != "yes":
        print(f"[preflight] pushing static curl -> {REMOTE_CURL}")
        push = _adb("push", curl_binary, REMOTE_CURL, timeout=60)
        if push.returncode != 0:
            sys.exit(
                f"ERROR: failed to push curl binary '{curl_binary}'. "
                "See docs/phone-compare.md for where to get an arm64 build.\n" + push.stderr
            )
        _adb_shell(f"chmod 755 {REMOTE_CURL}")
    # Sanity-check curl runs and reaches Cloudflare.
    chk = _adb_shell(f"{REMOTE_CURL} -s -o /dev/null -w '%{{http_code}}' {CF_UP}", timeout=30)
    if chk.stdout.strip() not in (
        "200",
        "405",
        "400",
    ):  # __up rejects GET; any HTTP code = reachable
        print(
            "[preflight] WARNING: curl sanity check returned "
            f"'{chk.stdout.strip()}' (stderr: {chk.stderr.strip()}). "
            "Continuing, but pushes may fail."
        )
    else:
        print("[preflight] curl OK, Cloudflare reachable over the phone's link")


# --------------------------------------------------------------------------
# Measurements (run ON the phone via adb shell, over cellular)
# --------------------------------------------------------------------------


def measure_download(n_bytes: int) -> tuple[float, int]:
    """Return (mbps, bytes). 0,0 on failure."""
    url = CF_DOWN.format(n=n_bytes)
    out = _adb_shell(
        f"{REMOTE_CURL} -o /dev/null -s -w '%{{speed_download}} %{{size_download}}' '{url}'",
        timeout=120,
    ).stdout.strip()
    try:
        speed_bps, size = out.split()
        return round(float(speed_bps) * 8 / 1e6, 2), int(float(size))
    except (ValueError, IndexError):
        return 0.0, 0


def measure_upload(n_bytes: int) -> tuple[float, int]:
    """Return (mbps, bytes). Streams N zero-bytes to /__up via head /dev/zero."""
    # head -c N /dev/zero | curl --data-binary @-  → upload N bytes without a temp file.
    cmd = (
        f"head -c {n_bytes} /dev/zero | {REMOTE_CURL} -o /dev/null -s "
        f"-w '%{{speed_upload}} %{{size_upload}}' --data-binary @- '{CF_UP}'"
    )
    out = _adb_shell(cmd, timeout=120).stdout.strip()
    try:
        speed_bps, size = out.split()
        return round(float(speed_bps) * 8 / 1e6, 2), int(float(size))
    except (ValueError, IndexError):
        return 0.0, 0


def measure_ping(count: int = 20) -> dict:
    out = _adb_shell(f"ping -c {count} -W 5 {PING_TARGET}", timeout=count * 6 + 20).stdout
    return _parse_ping_output(out, is_windows=False, max_rtt_ms=MAX_RTT_MS)


def serving_cell() -> dict:
    """Best-effort serving-cell identity from dumpsys (no app needed)."""
    out = _adb_shell("dumpsys telephony.registry", timeout=20).stdout
    cell = {}
    # mCellIdentity fields vary by Android version; pull what's there.
    for key, pat in (
        ("pci", r"mPci=(\d+)"),
        ("band", r"mBand[s]?=\[?(\d+)"),
        ("ci", r"mCi=(\d+)"),
        ("earfcn", r"mEarfcn=(\d+)"),
        ("tac", r"mTac=(\d+)"),
    ):
        m = re.search(pat, out)
        if m and m.group(1) not in ("2147483647", "-1"):  # CellInfo INT_MAX = unknown
            cell[key] = m.group(1)
    return cell


# --------------------------------------------------------------------------
# Influx line building (host=<host-tag>, same metric names as the hotspot)
# --------------------------------------------------------------------------


def _tags(host_tag: str, carrier: str, conn_type: str) -> str:
    # experiment=none keeps parity with the hotspot's tag set; triggered_by marks
    # the source so it's distinguishable in raw queries.
    return (
        f"host={host_tag},carrier={carrier},connection_type={conn_type},"
        f"experiment=none,triggered_by=phone-compare"
    )


def build_lines(
    *,
    host_tag: str,
    carrier: str,
    conn_type: str,
    ts: int,
    dl_mbps: float,
    dl_bytes: int,
    ul_mbps: float,
    ul_bytes: int,
    ping: dict,
) -> list[str]:
    tags = _tags(host_tag, carrier, conn_type)
    m = config.INFLUX_MEASUREMENT
    speedtest = (
        f"{m},{tags} "
        f"speedtest_download_mbps={dl_mbps},speedtest_upload_mbps={ul_mbps},"
        f"speedtest_download_bytes={dl_bytes}i,speedtest_upload_bytes={ul_bytes}i {ts}"
    )
    rtt = (
        f"{m},{tags} "
        f"rtt_avg_{PING_LABEL}={ping['rtt_avg']},rtt_min_{PING_LABEL}={ping['rtt_min']},"
        f"rtt_max_{PING_LABEL}={ping['rtt_max']},jitter_{PING_LABEL}={ping['jitter']},"
        f"pkt_loss_{PING_LABEL}={ping['pkt_loss']},connected_{PING_LABEL}={1 if ping['connected'] else 0},"
        f"connected={1 if ping['connected'] else 0} {ts}"
    )
    return [speedtest, rtt]


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="phone_compare",
        description="Measure a USB-connected phone's cellular link and push to Grafana.",
    )
    p.add_argument("--duration", type=int, default=600, help="total run seconds (default 600)")
    p.add_argument("--interval", type=int, default=60, help="seconds between samples (default 60)")
    p.add_argument("--download-bytes", type=int, default=25_000_000)
    p.add_argument("--upload-bytes", type=int, default=10_000_000)
    p.add_argument("--host-tag", default="standstill-phone")
    p.add_argument("--carrier", default="verizon")
    p.add_argument("--connection-type", default="5g_cellular")
    p.add_argument(
        "--curl-binary",
        default="scripts/curl-android-arm64",
        help="path to a local static arm64 curl binary to push to the phone",
    )
    p.add_argument("--keep-wifi-off", action="store_true", help="don't re-enable Wi-Fi on exit")
    p.add_argument("--preflight-only", action="store_true", help="run checks and exit")
    args = p.parse_args(argv)

    try:
        from towerwatch import credentials
    except ImportError:
        sys.exit("ERROR: src/towerwatch/credentials.py missing — needed for the Grafana push.")

    preflight(args.curl_binary)
    if args.preflight_only:
        return 0

    grafana = GrafanaClient.from_config(config, credentials)
    deadline = time.monotonic() + args.duration
    n = 0
    try:
        while True:
            n += 1
            ts = int(time.time())
            dl_mbps, dl_bytes = measure_download(args.download_bytes)
            ul_mbps, ul_bytes = measure_upload(args.upload_bytes)
            ping = measure_ping()
            cell = serving_cell()
            lines = build_lines(
                host_tag=args.host_tag,
                carrier=args.carrier,
                conn_type=args.connection_type,
                ts=ts,
                dl_mbps=dl_mbps,
                dl_bytes=dl_bytes,
                ul_mbps=ul_mbps,
                ul_bytes=ul_bytes,
                ping=ping,
            )
            ok = grafana.push_metrics(lines)
            cell_str = " ".join(f"{k}={v}" for k, v in cell.items()) or "cell=?"
            print(
                f"[{n}] dl={dl_mbps:>6.1f} ul={ul_mbps:>5.1f} Mbps | "
                f"rtt={ping['rtt_avg']:>4}ms jit={ping['jitter']:>3} loss={ping['pkt_loss']}% | "
                f"{cell_str} | push={'OK' if ok else 'FAIL'}",
                flush=True,
            )
            if time.monotonic() >= deadline:
                break
            time.sleep(max(0, args.interval - (time.time() - ts)))
    except KeyboardInterrupt:
        print("\n[interrupted]")
    finally:
        if not args.keep_wifi_off:
            _adb_shell("svc wifi enable", timeout=15)
            print("[cleanup] Wi-Fi re-enabled")

    print(
        f"\nDone — {n} samples pushed as host={args.host_tag}. Compare in "
        f"dashboard-compare.json: location_a=standstill, location_b={args.host_tag}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
