# Netgear M6 cellular signal probe

`pi/probes/m6.py` polls the admin API of a **Netgear Nighthawk M6** 5G/LTE hotspot for RSRP, RSRQ, SINR, and current band. The probe disables itself cleanly if the router isn't reachable.

## Setup (M6 specifically)

1. Connect to the M6's Wi-Fi.
2. Visit `http://192.168.1.1` → Advanced Settings → enable Ethernet port and Plugged-In Mode.
3. Set the admin password in `pi/secrets.py` (`M6_ADMIN_PASSWORD`).
4. Confirm `M6_ADMIN_URL` in `pi/config.py` points at your router's IP.

## Disable the probe

Clear `M6_ADMIN_URL` in `pi/config.py`. The probe checks for a non-empty URL on startup and skips all polling if it's missing.

## Port to a different router

Write a sibling module in `pi/probes/` that exposes the same metric shape — a dict with keys `rsrp`, `rsrq`, `sinr`, `band` (or a subset). Return `None` to signal unavailability; the main loop handles `None` gracefully.
