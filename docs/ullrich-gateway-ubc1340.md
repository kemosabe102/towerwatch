# ullrich gateway — Ubee UBC1340 (Altice/Optimum) monitoring assessment

Assessment of what monitoring is feasible for the ullrich site's router, from a
label photo (2026-06-29) + live probing of the device from the ullrich Pi (2026-07-06).

## The device

| Field | Value |
|---|---|
| Model | **Ubee UBC1340** (FCC ID XCNUBC1340) |
| Type | DOCSIS cable gateway + **LTE failover** (FCC label lists "LTE") |
| Provider | **Altice / Optimum** (SSID `MyOptimum 126731`, Suddenlink portal) |
| Roles | eCM (cable modem) + eMTA (voice) + eRouter (router) — combined gateway |
| LAN gateway IP | `192.168.1.1` (matches ullrich's auto-discovered gateway) |

## Local pollability — NONE (cloud-locked)

Unlike the M6 (`/api/model.json`) and Orbi (`/api/DEV_INFO`), the UBC1340 exposes
**no usable local telemetry API.** Probed from the ullrich Pi:

| Endpoint | Result |
|---|---|
| `http://192.168.1.1/` (+ 8 common Ubee/DOCSIS status paths) | **302 → `https://account.suddenlink.net/router-portal/login.html`** |
| `https://192.168.1.1/` | **401** (auth surface exists, but login is cloud-brokered via the Altice portal — not scriptable with local creds) |
| `http://192.168.100.1/` (standard DOCSIS diagnostic IP) + variants | **unreachable (HTTP 000)** — diagnostic surface locked in gateway mode |
| `account.suddenlink.net` portal (from Pi) | unreachable (HTTP 000) |
| Ports on `192.168.1.1` | 80 open, 443 open, 8080 closed |

**Conclusion:** the gateway is a black box that only talks to Altice's cloud. There is
no `model.json`-equivalent to scrape, so a vendor-specific probe (à la `probes/m6.py`)
is not feasible with the current access. The real DOCSIS channel/LTE-failover stats live
in the Altice cloud account, not on the device.

## What monitoring ullrich ALREADY has (baseline, working)

The vendor-agnostic gateway probe (`probes/gateway.py`) already runs here every tick and
is healthy (verified 2026-07-06):

- `gateway_tcp_ms` — TCP-connect time to the gateway (≈1 ms)
- `gateway_http_ms` — HTTP response time (near-0; the 302 redirect answers instantly)
- `pkt_loss_gateway`, `rtt_avg_gateway`, `connected_gateway` — ICMP loss / RTT / up-down
- `gateway_ip=192.168.1.1` on `build_info` (from the runtime resolver fix, build `5d81af3`)

This is genuine local-link monitoring: it catches the gateway going unreachable, LAN-side
loss, and responsiveness degradation — independent of the WAN. For a cloud-locked device
that's the honest ceiling of what's collectible locally.

## What we built instead: failure classification (implemented)

Vendor telemetry was never the mission. The real need is **failure classification for the
"devices lose access until router restart" symptom** — when the internet dies, *which layer*
died. That needs no vendor cooperation; it lives in the differential between probe layers.

| Symptom pattern | Diagnosis | Fix |
|---|---|---|
| gateway ping/TCP/HTTP dead | router hung | reboot/replace the Ubee |
| gateway fine + WAN (google/cloudflare) dead | cable plant / DOCSIS | call Optimum |
| WAN fine + public DNS fine + **gateway DNS dead** | gateway resolver hung | point clients at 1.1.1.1 |
| WAN fine + **egress IP changed** (possible LTE failover; confirm via ASN) | failed over to LTE | check cable line |

Rows 3–4 were the gaps; both are now closed (all-sites, config-gated):
- **`dns_resolve_ms_gateway`** — probes the gateway's *own* resolver every tick (a hung
  gateway resolver is invisible if we only probe public resolvers).
- **Egress-IP failover detector** — `EgressProbe` fetches the public IP + colo every ~15 min;
  the IP rides on `build_info` (label updating on change) and a `egress_ip_changed` Loki
  event fires on a flip. `egress_cgnat` (0/1) is a plottable field. **The change-event is the
  detector, not the CGNAT check** — Cloudflare reports the post-NAT public IP, so `is_cgnat`
  rarely fires on a real failover; unambiguous carrier attribution is a v2 ASN-on-change.
- Label correction: `CONNECTION_TYPE="docsis_cable"`, `CARRIER="altice"`.

### Reading the gateway-DNS signal — never-worked vs stopped-working
`dns_resolve_ms_gateway` runs at every site. A gateway that simply **never serves DNS** shows
a *permanently absent* metric — which looks identical to "resolver hung" if read naively. The
diagnostic signal is a metric that **was flowing and stopped**, not one that never existed.
When triaging, check history: a flat gap from day one = this gateway doesn't do DNS (not a
fault); a series that was populated and went absent = the resolver-hung failure mode.

## Rejected options (timebox zero — confirmed dead-ends)

1. **Altice cloud account API.** The real DOCSIS/LTE stats live in the Optimum portal.
   Polling it needs portal auth + reverse-engineering an undocumented cloud API tied to the
   account login — fragile, ToS-adjacent, breaks whenever Altice changes the portal.
2. **Bridge mode / SNMP / TR-069.** Might open a local DOCSIS surface, but requires
   physical/portal config changes at the site and is not confirmed on this locked firmware
   (the 401 HTTPS surface is cloud-brokered).
