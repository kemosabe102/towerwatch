# Phone-vs-hotspot comparison

Measure your **phone's** cellular link the same way towerwatch measures the **M6
hotspot**, and land both in the same Grafana so one dashboard compares them. The
question this answers: *is the hotspot being deprioritized on Verizon, or do the
phone and hotspot degrade together under congestion?*

- **Phone holds up materially better during congestion** → the hotspot's data is
  deprioritized. Tethering to the phone or a higher-priority plan is a real fix.
- **Both degrade together** → raw capacity exhaustion at the tower. No plan change
  helps; the Verizon escalation becomes a documented long-shot.

The phone is a Pixel 9 Pro on Xfinity Mobile (a Verizon MVNO). It's driven from a
Mac over USB/ADB — `adb shell` runs the measurement *on the phone, over its
cellular radio*; USB is only the control channel. You kick it off and walk away;
results appear in Grafana.

> **One caveat to keep in mind when reading the result:** the phone is *also* an
> MVNO, so a phone advantage could be plan-priority **or** the phone's better
> internal antenna/band access. Either way it points to an actionable workaround
> (tether or upgrade), so the test is worth running — just don't over-read a small
> gap as definitely "deprioritization."

---

## One-time setup

### 1. Install ADB on the Mac

```bash
brew install android-platform-tools
adb version    # confirm it runs
```

### 2. Enable USB debugging on the phone

- Settings → About phone → tap **Build number** 7× to unlock Developer options.
- Settings → System → Developer options → enable **USB debugging**.
- Connect the phone to the Mac by USB. Run `adb devices`. The phone shows a
  **"Allow USB debugging? / Trust this computer?"** dialog — tick *Always allow*
  and approve. Re-run `adb devices`; it should list the device as `device` (not
  `unauthorized`).

### 3. Get a static `curl` for the phone

The Pixel's shell has no `curl`, so the script pushes a static **arm64** curl
binary to the phone. Download a trustworthy prebuilt one and place it at
`scripts/curl-android-arm64` (gitignored — vet the source yourself):

- A common source is the **static-curl** project (prebuilt `curl-aarch64` for
  Linux/Android). Download the `aarch64` asset, then:

  ```bash
  mv ~/Downloads/curl-aarch64 scripts/curl-android-arm64
  chmod +x scripts/curl-android-arm64
  ```

The script pushes it to `/data/local/tmp/curl` on the phone once (idempotent on
re-runs). If you'd rather not push a binary, you can instead install **Termux**
on the phone (`pkg install curl`) and adapt `REMOTE_CURL` in the script — but the
pushed static binary keeps the whole flow Mac-driven with nothing to configure on
the phone.

---

## Running a comparison

Run during a window your **hotspot telemetry confirms is congested** (open the
main Grafana dashboard for `standstill` and watch latency/loss spiking live).
That's the whole point — comparing during quiet windows tells you little.

```bash
cd /path/to/towerwatch
python scripts/phone_compare.py --duration 600 --interval 60
```

- `--duration 600` — run for 10 minutes.
- `--interval 60` — one sample per minute.
- Defaults: 25 MB download + 10 MB upload per sample, pushed as
  `host=standstill-phone`.

The script will:

1. Confirm the phone is connected and authorized.
2. **Disable Wi-Fi** on the phone (so it measures *cellular*) and verify a
   `rmnet*`/`ccmni*` interface is up. It re-enables Wi-Fi when it finishes.
3. Push the static curl binary (first run only).
4. Loop: Cloudflare download + upload + a 20-ping burst, each sample, over
   cellular; capture the serving cell; push to Grafana.

Per-sample console output:

```
[1] dl=  84.5 ul= 11.2 Mbps | rtt=  46ms jit=  3 loss=0% | pci=433 band=66 ci=359184 | push=OK
[2] dl=  76.3 ul= 10.8 Mbps | rtt=  48ms jit=  4 loss=0% | pci=433 band=66 ci=359184 | push=OK
...
Done — 10 samples pushed as host=standstill-phone. Compare in dashboard-compare.json:
location_a=standstill, location_b=standstill-phone.
```

> **Data cost:** ~35 MB per sample (25 down + 10 up). A 10-minute run at 1/min ≈
> **350 MB**. On a metered window, lower it with `--download-bytes 10000000
> --upload-bytes 5000000` or `--interval 120`.

---

## Reading the result

The phone data uses the **same metric names** as the hotspot, just under a
different `host`, so the existing compare dashboard works with no edits:

1. Open **`grafana/dashboard-compare.json`** in Grafana (Dashboards → Towerwatch
   side-by-side comparison).
2. Set **`location_a = standstill`** (hotspot) and **`location_b =
   standstill-phone`** (phone). `standstill-phone` appears in the dropdown once the
   first sample lands.
3. Compare the **RTT Avg**, **Packet Loss**, **HTTP Throughput**, and **Speedtest
   History** panels side by side for the test window.

### Confirm same cell (important)

A fair comparison needs both devices on the **same tower/cell** — otherwise a
cell difference masquerades as a device difference. The script logs the phone's
serving `pci`/`band`/`ci` each sample. Cross-check against the hotspot's
`m6_pcc_pci` / `m6_band` (visible on the main dashboard's "M6 Current Cell" and
"M6 Handover History" panels) for the same minutes. If the PCI/band match, you're
on the same cell. If they differ, note it — the comparison for that sample is
confounded.

---

## Matched-window protocol (for a clean result)

From the on-site troubleshooter — follow this so the data is interpretable:

- **Congested window only.** Run when the hotspot's latency/loss are visibly
  elevated (a busy holiday weekend at the lake house: **Fri June 19**, **Sat July
  4**). A quiet-window comparison can't show deprioritization.
- **Tight alternation / matched time-of-day.** The hotspot samples continuously;
  run the phone in the same 10-minute window so both see the same congestion
  state. Repeat across the busy stretch (e.g. a few runs over the afternoon/evening
  peak).
- **Hold the cell constant.** Keep `pci`/`band`/`enb` the same across the
  comparison (check the logged values). A handover mid-test invalidates that
  sample.
- **Lead with this test; hold the Verizon escalation.** The result frames the
  escalation — deprioritization is a lever; capacity exhaustion makes it a
  long-shot. Don't draft the escalation until you've run this.

---

## Cleanup & troubleshooting

- The script **re-enables Wi-Fi** on exit (including Ctrl-C). Pass
  `--keep-wifi-off` to skip. If a run died hard, just toggle Wi-Fi back on in
  Settings, or `adb shell svc wifi enable`.
- **`adb` not found** → `brew install android-platform-tools`.
- **device `unauthorized`** → approve the trust dialog on the phone, re-run.
- **`failed to push curl binary`** → check `scripts/curl-android-arm64` exists and
  is an arm64 build.
- **WARNING: wlan0 still has an IP** → Wi-Fi didn't fully drop; toggle it off on
  the phone and re-run, or the samples will measure Wi-Fi, not cellular.
- **pushes show `FAIL`** → check `GRAFANA_INSTANCE_ID` / `GRAFANA_API_KEY` in
  `src/towerwatch/credentials.py` (the push-only key is correct here).
- **Wireless ADB** (phone untethered): you can `adb pair` / `adb connect` over
  Wi-Fi for the *control* channel, but then you must still ensure the phone's
  *test traffic* uses cellular — which means Wi-Fi must be off, which kills the
  ADB-over-Wi-Fi link. So for this test, **USB is the reliable choice.**

---

## Why this is a fair comparison

- **Same method:** the phone hits the exact Cloudflare endpoints
  (`speed.cloudflare.com/__down`, `/__up`) the hotspot probe uses. The hotspot
  uses an adaptive ramp; the phone uses a fixed 25 MB/10 MB transfer — "same
  method, fixed size," close enough for apples-to-apples during a congested window.
- **Same tower, same minutes:** confirmed via the serving-cell check above.
- **Same sink:** both land in one Grafana with identical metric names, so the
  compare dashboard plots them directly.
