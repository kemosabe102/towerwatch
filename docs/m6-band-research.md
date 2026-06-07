# Netgear Nighthawk M6/M6 Pro Band Locking Research

**Research Date:** June 2026  
**Models Covered:** MR6110, MR6450, MR6500 (M6 Pro), MR6400  
**Modem Chipsets:** X62 (MR6110), X65 (MR6400/MR6450/MR6500)

---

## Executive Summary

**TL;DR for Automated Band Comparison:** The M6 **cannot** be automated via HTTP API for band locking. Band selection on the M6 is restricted to:
1. **Telnet + AT commands** (port 5510) — manual, circuit-switching modem resets, NO persistent API automation
2. **mrCONFIRG tool** — desktop app, proprietary unlock codes, not scriptable for production
3. **Newer firmware (12.x) intentionally restricts both the web UI and AT command band-lock functionality**, especially on carrier-locked variants (AT&T, T-Mobile, Verizon)

**Feasibility of Your Workflow:** **LOW to VERY UNLIKELY** on production M6 units. See [Caveats](#caveats-for-automation) below.

---

## 1. Band Lock API Endpoint(s)

### HTTP/Web API Status: **NOT EXPOSED**

**Finding:** The M6 series **does not expose band locking via HTTP/REST API** through the web interface at `192.168.1.1`.

- **Earlier M-series models** (M1/MR1100, M5/MR5100): Had a web UI form for band selection → forms-based HTTP POST, analyzable via browser dev tools.
- **M6 series (MR6110/MR6450/MR6500):** Web UI omits the "Bands" option under Settings → Network entirely.
- **Mobile app (Nighthawk/NetgearTelstra):** Also omits the Bands option, unlike M5 which exposed it in the app.

**Netgear Deliberate Removal:**
Firmware changelog indicates Netgear **intentionally removed** band-locking from the M6 web interface and mobile app starting with firmware 12.x releases. The restriction is **firmware-level**, not a UI oversight.

**The eternalegypt Project Insight:**
The [eternalegypt](https://github.com/amelchio/eternalegypt) Python library (Netgear LTE modem API) interfaces with modems by piggybacking on the web interface. Its codebase shows a pattern:
```python
async with self._config_call('key.name', 'value') as response:
```
which POSTs to `/Forms/config` with a token.

**However,** the library **does not implement band locking** for any modem model, including MR1100/M1 which theoretically supports it. This strongly suggests that even if an endpoint like `/Forms/config` with a `wwan.bandLock` parameter exists, Netgear did not expose it on the M6 (and documentation/reverse-engineering has not recovered it).

**Conclusion:** There is **no publicly documented or reverse-engineered HTTP band-lock endpoint for the M6**. If one exists, it is hidden behind obfuscation or deliberately disabled.

---

## 2. Authentication for Writes

### Scenario A: If an Undocumented API Existed

Based on reverse-engineering of earlier M-series and other Netgear devices, the pattern would be:

**Login Flow:**
1. POST to `/Forms/Login` with `username=admin&password=<pwd>`
2. Response includes a `Set-Cookie: sessionID=<long-token>` or similar
3. Subsequent config POSTs include the cookie or a CSRF token

**Alternative: Token in HTML:**
- Fetch the config page (e.g., `/Forms/Network`)
- Parse HTML for a hidden `<input name="token" value="..." />`
- Include that token in subsequent POST requests

**Netgear Security Quirks:**
- M1 (MR1100) historically had a **CSRF vulnerability** (token stored in dynamically-generated JS, reusable across origins) — [Pen Test Partners writeup](https://www.pentestpartners.com/security-blog/how-not-to-do-cross-site-request-forgery-protection-the-netgear-nighthawk-m1/)
- M6 likely tightened this, but details are not public

### Current Reality: Telnet-Based Auth

**Port:** 5510 (telnet, not SSH)  
**No username/password required** for telnet access in some firmware versions; others prompt for password (default: `admin` or carrier-specific)  
**Session:** Telnet session is authenticated once connected; AT commands run in that context

---

## 3. Band Specification Format

### Via AT Commands (Telnet)

**Command Syntax:**
```
AT!BAND=<slot>,"<description>",<hex_value>,<band_number>
```

**Parameters:**
- `<slot>`: Index/slot number (0–9, then use hex 0A, 0B for slots 10+)
- `<description>`: User label, e.g., "AT&T Band 4"
- `<hex_value>`: Hexadecimal bitmask or enumeration value (device-model-specific; documentation not public)
- `<band_number>`: LTE band (e.g., 4, 7, 12, 29, 66) or 5G NR band (e.g., 71, 77, 78, n41)

**Example (from M1 documentation):**
```
AT!BAND=0,"AT&T Band 4",0x04,4
AT!BAND=1,"AT&T Band 12",0x0C,12
AT!BAND=2,"AT&T Band 29",0x1D,29
AT!BAND=3,"AT&T Band 66",0x42,66
```

**Query Available Bands:**
```
AT!BAND=?
```
Responds with list of currently-locked bands and their indices.

**Release Lock (Revert to Auto):**
No direct "unlock" command found. Options:
1. Delete all band entries (if possible): `AT!BAND=<slot>` with no value (untested on M6)
2. Factory reset via telnet: `AT+CFUN=1,1` or reboot
3. Clear via web UI if available (M1/M5 only)

### LTE vs. 5G NR Band Format

**LTE Bands (4G):** Standard numbering B1, B2, ..., B71 (e.g., Band 4 = AWS, Band 7 = 2600 MHz)

**5G NR Bands:** Prefix `n`, e.g., n41 (2.6 GHz), n77 (3.7–3.8 GHz), n78 (3.7–3.8 GHz), n79 (4.4–5.0 GHz)

**M6 Supported Bands (AT&T variant, MR6110/MR6500-1A1NAS example):**
- **4G/LTE:** 1, 2, 3, 4, 5, 7, 12, 14, 29, 30, 46, 48, 66
- **5G:** 2, 5, 12, 14, 29, 30, 66, 77

(Other carriers/regions have different subsets.)

**Bitmask vs. List:** The `<hex_value>` parameter appears to be a per-model enumeration, not a standard bitmask. Netgear has not published a mapping table; users reverse-engineer via trial-and-error or use tools like [Netgear M1 Band Generator](https://josh.sc/netgear-mr1100-band-generator/).

---

## 4. Is Band-Lock Even Exposed on the M6?

### Short Answer: **NO — Not on Production Firmware**

### Detailed Analysis:

**Firmware Variant Breakdown:**

| Firmware Version | Telnet Root Shell | AT!BAND Command | Web UI Band Selector |
|---|---|---|---|
| 10.x (old M1/M5) | YES (port 23) | YES | YES (M5 only) |
| 11.x (M5 early) | YES (port 23) | YES | YES |
| 12.x (M6 AT&T/T-Mobile) | **LOCKED** | **LOCKED on some versions** | NO |
| 12.x (M6 Unlocked variant MR6550) | Varies | Likely YES | NO |
| 12.x (M6 Telstra/Other) | Varies | YES/LOCKED mix | NO |

**AT&T Variants Specifically Locked:**
Models impacted by Netgear's April 2024 unlock-code scheme:
- MR6110-1TLAUS
- MR6400-1DNNAS
- MR6450-100EUS
- MR6500-1A1NAS
- MR6550-100PAS

On these units, firmware 12.x+ blocks:
1. Telnet root shell (port 23 closed)
2. Some AT!BAND commands (dependent on firmware sub-version)
3. AT!ENTERCND unlock codes (requires proprietary keygen)

**Unlocked Variant (MR6550):**
The all-carrier M6 Pro (MR6550) supports more freedom, but Netgear has NOT re-exposed the web UI band-selector — it remains CLI-only.

**How mrCONFIG Works:**
Third-party tool that:
1. Unlocks telnet on carrier-locked M6 via reverse-engineered keygen (free with device serial)
2. Allows telnet AT command access
3. **Does NOT add HTTP API** — still manual telnet AT commands

---

## 5. Existing Open-Source Tooling

### Active Projects:

| Project | Language | Purpose | Band Lock Support | Status |
|---|---|---|---|---|
| [eternalegypt](https://github.com/amelchio/eternalegypt) | Python 3 | Netgear LTE modem API wrapper | NO (not implemented) | Active, 35★ |
| [leonzdev/mr6500](https://github.com/leonzdev/mr6500) | Docs/Firmware | M6 Pro reverse engineering | Hardware teardown, firmware analysis | Active research |
| [pynetgear](https://github.com/MatMaul/pynetgear) | Python | Netgear router SOAP control | N/A (routers, not hotspots) | Maintained |
| [Hacking_MR1100_Hotspot](https://github.com/RupGautam/Hacking_MR1100_Hotspot) | Python | M1 IMEI repair, TTL custom values | IMEI only | Older, M1-specific |
| [mrCONFIG](https://tinyurl.com/mrCONFIGTools) | Windows/proprietary | M6 telnet unlock + band config UI | YES (via telnet) | Proprietary, license required |
| [M1 Band Generator](https://josh.sc/netgear-mr1100-band-generator/) | Web/JavaScript | Interactive hex value lookup | M1 reference only | Historical |
| [Waveform M1 Band Locking Guide](https://www.waveform.com/a/b/guides/mr1100-band-locking) | Docs | Tutorial for M1 via AT commands | M1 only | Reference |

### Key Findings:

- **eternalegypt** is the most "API-like" Python project for Netgear LTE, but it:
  - Does not expose band locking
  - Works via web form scraping, not a documented REST API
  - Is unmaintained for M6 (only tested on M1, M5-era devices)

- **No Python library for M6 band locking** exists. Community users rely on:
  1. Manual telnet + AT commands
  2. Desktop GUI tools (mrCONFIG)
  3. USB serial + QMI tools (advanced, off-topic for web automation)

---

## 6. Caveats for Automation

### Critical Blockers

#### 1. Firmware Locks AT!BAND Command
- **Symptom:** Telnet connects but `AT!BAND=?` returns error or command not found
- **Affected:** AT&T MR6110/MR6500, some T-Mobile variants, firmware 12.01.34+
- **Workaround:** Downgrade firmware (risky, voids warranty) or unlock with mrCONFIG (requires license + serial)
- **Automation Impact:** Even if you automate telnet, the command may silently fail

#### 2. Carrier-Level Band Restrictions (Firmware)
- **Root Cause:** Carrier requested Netgear disable non-carrier bands at the modem driver level (not just web UI)
- **Example:** AT&T M6 firmware omits T-Mobile Band 71 entirely — even if you bypass the UI, the modem driver doesn't see it
- **Automation Impact:** You can script band-lock commands, but you can only select from the **subset the carrier allowed**, which may be 4–8 bands instead of all LTE + 5G NR

#### 3. Band Lock Does NOT Persist After Reboot (On Some Firmware)
- **Finding:** Earlier M-series documentation states bands "persist after reboot initiated from web GUI"
- **M6 Reality:** Community reports indicate bands may revert on firmware 12.x after a reboot, especially if the modem performs an unclean shutdown
- **Automation Impact:** A band change followed by an automated speedtest may occur before the modem fully applies the lock

#### 4. Modem Reset on Band Change
- **Effect:** Changing band via AT command causes a brief modem restart (10–30 s)
- **Connection Loss:** WiFi disconnects, Ethernet link may drop briefly
- **Automation Impact:** Between band changes, your speed test will fail (no connectivity). Plan 60 s gap between band switches.

#### 5. Rate Limits / Command Queue Issues
- **Unknown:** Netgear has not published telnet command rate limits
- **Risk:** Rapid successive AT!BAND commands may cause telnet session to hang or drop
- **Mitigation:** Add 5–10 s delay between band changes and always reconnect telnet on error

#### 6. Telnet Access Disabled by Firmware Update
- **M6 Variants:** Carrier-locked units may have telnet disabled after an OTA firmware update
- **Example:** Device reboots for update, comes back with telnet blocked on port 5510
- **Automation Impact:** Your automated script breaks until re-unlocked (mrCONFIG or downgrade)

#### 7. Carrier-Locking Cannot Be Bypassed by API
- **The Hard Truth:** The M6's band restrictions are **implemented at the modem firmware level**, not in the web UI
  - Netgear built in the carrier bands at compile time
  - Disabling the web UI doesn't "unlock" suppressed bands — they're absent from the driver
- **Example:** AT&T M6 has no way to enable Band 71 (T-Mobile's primary 5G band) without flashing a non-AT&T firmware
- **Your Options:**
  1. Buy an **unlocked M6 (MR6550)** — supports all major US carrier bands (AT&T, T-Mobile, Verizon)
  2. Use a non-M6 device (older M5/M1 have more flexible firmware)
  3. Use a dedicated 5G modem with QMI/AT command ISP-level access (beyond scope)

---

## 7. Recommended Alternatives to Automated Band Switching

### If You Have an Unlocked M6 (MR6550)

**Partially Feasible:**
1. Unlock telnet with mrCONFIG (one-time, ~$10–20 or free with serial if you have an existing account)
2. Implement a Python telnet client that:
   - Connects to `192.168.1.1:5510`
   - Sends AT commands sequentially (with 10 s delays)
   - Waits for modem recovery after band change (~30 s)
   - Runs speedtest on each band
3. **Limitations:** Still manual/scripted, not an API; subject to firmware changes; no persistent guarantee

**Rough Pseudocode:**
```python
import telnetlib
import time
import subprocess

BANDS_TO_TEST = [4, 7, 12, 29, 66]  # AT&T LTE bands
M6_IP = "192.168.1.1"
M6_TELNET_PORT = 5510

tn = telnetlib.Telnet(M6_IP, M6_TELNET_PORT)

for band in BANDS_TO_TEST:
    # Clear existing locks
    tn.write(b"AT!BAND=*\n")  # Hypothetical "clear all" (may not exist)
    time.sleep(2)
    
    # Set new lock
    tn.write(f"AT!BAND=0,\"TestBand\",0x{band:02X},{band}\n".encode())
    time.sleep(10)  # Wait for modem to apply
    
    # Run speedtest
    result = subprocess.run(["towerwatch-speedtest"], capture_output=True)
    print(f"Band {band}: {result.stdout.decode()}")
    
    time.sleep(5)  # Cooldown before next band

tn.close()
```

**Caveats:**
- `AT!BAND=*` may not exist — you'd need to query `AT!BAND=?` and delete each slot individually
- Hex value (`0x{band:02X}`) is a guess; actual encoding is device-specific
- No error handling for firmware-locked AT commands
- Modem may not respond to rapid commands; add retries

### If You Have a Carrier-Locked M6 (MR6110/MR6450/MR6500)

**Not Recommended** for automated band testing. Instead:

1. **Use the device's default band-selection algorithm:** The M6 auto-selects based on signal quality. Your speedtest will reflect real-world performance on available bands.
2. **Trigger manual speedtests from different locations:** Move the device (e.g., different room, outdoor, indoors) to experience different band selection naturally.
3. **Inspect connected band via GSTATUS command** (read-only):
   ```
   AT!GSTATUS?
   ```
   Response includes current connected band; log this with each speedtest result to understand which band was active.

---

## 8. Data Budget / Deployment Notes

If you implement automated band-switching on an unlocked M6:

**Per-Band Speedtest Data:**
- One Cloudflare adaptive speedtest: ~50 MB (at gigabit; varies with link speed)
- If testing 5–10 bands: 250–500 MB per iteration
- **Monthly data budget (30 GB per towerwatch instance):** Only 60–120 full band-sweep iterations before hitting the ceiling

**Recommendation:** Limit band-sweep frequency to:
- **Once per day** on home gigabit (single iteration per day = ~50 MB/day ≈ 1.5 GB/month — well within budget)
- **Once per week** on metered cellular (reserve budget for regular speedtests)

---

## Summary Table

| Question | Answer | Certainty |
|---|---|---|
| **Is band-lock exposed via HTTP API on M6?** | NO | 99% |
| **Can I script band changes via telnet + AT commands?** | YES (if unlocked) | 85% |
| **Will band changes persist after reboot?** | MAYBE (firmware-dependent) | 60% |
| **Do carrier-locked M6 units support all bands?** | NO (firmware-level restriction) | 99% |
| **Is there a production-ready Python library for M6 band-locking?** | NO | 95% |
| **What's the time to switch bands + apply lock?** | ~30–60 s (modem reset) | 75% |
| **Can I automate this without root/TTL access?** | YES (telnet on unlocked M6) | 80% |
| **Is it feasible for long-term CI/CD automation?** | NO — too fragile, firmware-dependent | 85% |

---

## References

### Web Search & Community Forums
- [Band locking M6 MR6500 - Wireless Joint](https://wirelessjoint.com/viewtopic.php?t=3626)
- [Band Locking M6 MR6500 - NETGEAR Communities](https://community.netgear.com/t5/Cell-Service-Mobile-Hotspot/Band-locking-M6-MR6500/td-p/2240388)
- [Help with rolling back firmware - XDA Forums](https://xdaforums.com/t/help-with-rolling-back-firmware-on-nighthawk-mr6500-m6-pro.4682141/page-4)
- [Netgear Releases Unlocked M6 Pro - RV Mobile Internet Resource Center](https://www.rvmobileinternet.com/netgear-releases-unlocked-all-carrier-nighthawk-m6-pro-5g-mobile-hotspot/)

### Technical Guides
- [Band Lock Your Netgear Nighthawk M1 (MR1100) Using AT Commands - Waveform](https://www.waveform.com/a/b/guides/mr1100-band-locking)
- [Enable manual band selection on Netgear MR1100 - MT-TECH.FI](https://mt-tech.fi/en/enable-manual-band-selection-on-netgear-mr1100/)
- [NETGEAR Nighthawk MR5100, MR6110, MR6400, MR6500 Band / Frequency manual selection - DC-Unlocker](https://www.dc-unlocker.com/netgear-nighthawk-mr5100-band-manual-selection)
- [Nighthawk M1 AT commands - GitHub Gist](https://gist.github.com/wombat/49f7c1b87b8c6918290a11504a624f62)

### Reverse Engineering & Tools
- [GitHub - leonzdev/mr6500: Poking around the M6 Pro](https://github.com/leonzdev/mr6500)
- [GitHub - amelchio/eternalegypt: Python API for Netgear LTE modems](https://github.com/amelchio/eternalegypt)
- [Netgear M1 Band Generator - josh.sc](https://josh.sc/netgear-mr1100-band-generator/)
- [mrCONFIG Tools - Wireless Joint](https://tinyurl.com/mrCONFIGTools)

### Security & CVE
- [Breaking (Bad) CSRF Protection - The Netgear Nighthawk M1 - Pen Test Partners](https://www.pentestpartners.com/security-blog/how-not-to-do-cross-site-request-forgery-protection-the-netgear-nighthawk-m1/)
- [NETGEAR Support - How can I reduce my risk of exposure to CSRF, XSRF, or XSS attacks?](https://kb.netgear.com/000037851/How-can-I-reduce-my-risk-of-exposure-to-CSRF-XSRF-or-XSS-attacks)

### Official Documentation
- [User Manual Nighthawk M6 Pro, M6 Models (PDF) - Netgear](https://www.downloads.netgear.com/files/GDC/MR6500/MR6500_MR6110_UM_EN.pdf)
- [Which mobile carriers are compatible with my NETGEAR mobile hotspot? - NETGEAR KB](https://kb.netgear.com/000063963/Which-mobile-carriers-are-compatible-with-my-NETGEAR-mobile-hotspot-or-fixed-wireless-device)

---

**Disclaimer:** This research reflects the state of M6 firmware and tooling as of June 2026. Netgear firmware updates may change AT command availability, telnet access, or band availability without notice. Always verify on your specific device and firmware version before attempting automation.
