# Towerwatch ops runbook

Symptom-indexed reference for the remote-deployment phase. Designed to be read cold at 2am.

**Access:** SSH via Tailscale — `ssh <user>@<tailscale-ip>`. Grafana: `https://towerwatch.grafana.net`.

---

## Symptom index

| Symptom | Jump to |
|---|---|
| No data in Grafana for >2h | [Silent Pi](#silent-pi-no-data-in-grafana) |
| Metrics gap but logs present | [Metrics push failing](#metrics-push-failing) |
| Logs gap but metrics present | [Loki push failing](#loki-push-failing) |
| Outage annotation appeared | [Diagnosing an outage annotation](#diagnosing-an-outage-annotation) |
| Service crash / restart loop | [Service crashing](#service-crashing) |
| All probes failing, gateway probe OK | [WAN outage](#wan-outage) |
| Only one probe target failing | [Single target down](#single-target-down) |
| DNS metrics absent, others OK | [DNS outage](#dns-outage) |
| RTT suddenly very high, no loss | [High latency, no outage](#high-latency-no-outage) |
| Signal metrics absent | [M6 signal probe failing](#m6-signal-probe-failing) |
| Clock-related annotation anomaly | [Clock skew / annotation anomaly](#clock-skew--annotation-anomaly) |
| Pi accessible but data partition errors | [Data partition issues](#data-partition-issues) |
| Deploying an update remotely | [Remote deploy](#remote-deploy) |

---

## Silent Pi — no data in Grafana

**Check 1: Is the Pi reachable?**
```bash
tailscale ping <tailscale-ip>
ssh <user>@<tailscale-ip>
```
If unreachable: power cycle may be required at the remote site. Check the Grafana alert for last-seen timestamp to estimate outage start.

**Check 2: Is the service running?**
```bash
sudo systemctl status towerwatch
journalctl -u towerwatch -n 50 --no-pager
```

**Check 3: Is the data partition mounted?**
```bash
mountpoint /opt/towerwatch/data
ls /opt/towerwatch/data/
```
If not mounted: `sudo mount /dev/mmcblk0p3 /opt/towerwatch/data` (check `fstab` first).

**Check 4: Is the network up?**
```bash
ping -c 3 8.8.8.8
curl -s https://towerwatch.grafana.net/api/health
```

**Recovery:** If service was stopped, `sudo systemctl start towerwatch`. Buffered data will flush on first successful push.

---

## Metrics push failing

**Symptom:** Loki shows `metrics_push_failed` events; Prom has no new data.

**Check:**
```bash
journalctl -u towerwatch -n 100 | grep push
curl -v -u "<GRAFANA_INSTANCE_ID>:<GRAFANA_API_KEY>" \
  https://prometheus-prod-67-prod-us-west-0.grafana.net/api/v1/push/influx/write
```

**Common causes:**
- Grafana API key expired → regenerate in Access Policies, update `/opt/towerwatch/secrets.py`, restart.
- Network blocks port 443 → check iptables: `iptables -L OUTPUT -n`.
- Prometheus endpoint changed → update `GRAFANA_PUSH_URL` in `config.py`, redeploy.

---

## Loki push failing

**Symptom:** Prom has metrics but Loki has no recent log events. Note: non-2xx Loki responses are currently swallowed silently (known gap — see [bench test 4](bench-tests.md)).

**Check:**
```bash
journalctl -u towerwatch -n 100 | grep -i loki
```
Also check buffer file size: if `loki.jsonl` is growing unbounded, push is failing.
```bash
ls -lh /opt/towerwatch/data/buffer/loki.jsonl
```

**Recovery:** Verify `LOKI_URL`, `LOKI_USER`, `LOKI_TOKEN` in `/opt/towerwatch/secrets.py`. Restart service after correction.

---

## Diagnosing an outage annotation

An outage annotation appears when the service detects a push gap ≥ 10 min on recovery.

**Check the annotation details:**
```bash
# From Grafana Cloud: Dashboards → Annotations, or via API:
curl -H "Authorization: Bearer <GRAFANA_ANNOTATION_TOKEN>" \
  "https://towerwatch.grafana.net/api/annotations?tags=towerwatch,outage,auto&limit=5"
```

**Check what the Pi saw:**
```bash
journalctl -u towerwatch --since "2h ago" | grep -E "outage|down|reconnect"
```

**If annotation looks spurious (e.g., duration mismatch):** Check for clock skew — see [Clock skew](#clock-skew--annotation-anomaly).

---

## Service crashing

**Symptom:** `systemctl status towerwatch` shows `failed` or rapid restart loop.

**Check:**
```bash
journalctl -u towerwatch -n 100 --no-pager
```

**Common causes:**
- Import error after bad deploy → `python3 -c "import towerwatch"` in `/opt/towerwatch/`.
- `secrets.py` missing or malformed → check file exists and has all required keys.
- Data partition full → `df -h /opt/towerwatch/data`.
- Permissions issue after overlayroot reboot → check `/opt/towerwatch/*.py` ownership.

**Recovery:**
```bash
sudo systemctl reset-failed towerwatch
sudo systemctl start towerwatch
```

---

## WAN outage

**Symptom:** All probes failing (google, cloudflare, 8.8.8.8) but gateway (192.168.1.1) ping OK.

This is expected ISP/carrier outage behavior. Logs buffer on disk and flush on reconnect (256 KB cap). Metrics during the outage are lost by design — expect a gap in Prom, not a backfill.

**Monitor:**
```bash
# From dev machine, poll Grafana until towerwatch_connected resumes
watch -n 60 'curl -s -H "Authorization: Bearer <KEY>" \
  "https://towerwatch.grafana.net/api/datasources/proxy/uid/<uid>/api/v1/query?query=towerwatch_connected"'
```

---

## Single target down

**Symptom:** One of `google`, `cloudflare`, or `gateway` probe metrics absent; others OK.

- `google` or `cloudflare` absent: rare; likely a routing anomaly. Monitor for recovery.
- `gateway` (192.168.1.1) absent: M6 router may have changed IP or DHCP reassigned. Check `PROBE_TARGETS` in `config.py` and update if needed. Keep the label `"gateway"` — dashboards query by field name.

---

## DNS outage

**Symptom:** `dns_failed` Loki events; `towerwatch_dns_ms_*` absent; TCP/ping metrics present.

**Check:**
```bash
python3 -c "import dns.resolver; r=dns.resolver.Resolver(); r.nameservers=['8.8.8.8']; print(r.resolve('example.com'))"
```

**Common cause:** ISP-level DNS blocking. TCP/ICMP still working means the connection is up; only DNS resolution is affected. Document as evidence for ISP dispute.

---

## High latency, no outage

**Symptom:** RTT metrics elevated (>200ms), jitter high, no connection_down event.

This is normal degraded-service evidence — exactly what Towerwatch is designed to capture. No intervention needed. Check:
```bash
journalctl -u towerwatch | grep -E "rtt|jitter"
```
If latency persists >1h, useful to note timestamp for ISP dispute documentation.

---

## M6 signal probe failing

**Symptom:** `m6_auth_expired` Loki events, or signal metrics (`rsrp`, `rsrq`, `sinr`) absent.

**Check:**
```bash
curl http://192.168.1.1/api/model.json  # Should return JSON without auth if unauthenticated
```

**Common causes:**
- Router admin password changed → update `M6_ADMIN_PASSWORD` in `/opt/towerwatch/secrets.py`, restart.
- Router rebooted and IP changed → update `M6_ADMIN_URL` in `config.py`, redeploy.
- Router offline → signal probe fails gracefully; other probes unaffected.

---

## Clock skew / annotation anomaly

**Symptom:** Annotation with impossible time range (end < start), or annotation for a gap that didn't happen.

**Check:**
```bash
timedatectl show
chronyc tracking  # or ntpq -p
```

**Recovery:**
```bash
timedatectl set-ntp true
sleep 10
timedatectl show   # NTPSynchronized should flip to yes
```

If NTP can't sync (carrier blocks NTP): `fake-hwclock` will at least keep time sane across reboots. Check `/etc/fake-hwclock.data`.

**Known gap:** Negative push-gap (clock stepped backward) produces a bogus annotation ([bench test 8](bench-tests.md), expected-failure). Delete the bogus annotation manually via Grafana UI or:
```bash
curl -X DELETE -H "Authorization: Bearer <GRAFANA_ANNOTATION_TOKEN>" \
  "https://towerwatch.grafana.net/api/annotations/<id>"
```

---

## Data partition issues

**Symptom:** `partition_not_detected` events, buffer writes failing, or service crashing with OSError.

**Check:**
```bash
mountpoint /opt/towerwatch/data
df -h /opt/towerwatch/data
dmesg | tail -20  # Look for filesystem errors
```

**Recovery:**
```bash
sudo fsck /dev/mmcblk0p3   # Unmount first if needed
sudo mount /dev/mmcblk0p3 /opt/towerwatch/data
sudo systemctl restart towerwatch
```

If the partition is full (unlikely at 1 GB with 256 KB log buffer cap): check for unexpected files in `/opt/towerwatch/data/` and clear bench snapshots/reports if present.

---

## Remote deploy

From your dev machine:
```bash
./ci.sh full              # Stamps version.txt, runs 30s smoke
./cd.sh <user>@<tailscale-ip>
```

`cd.sh` will:
1. SSH in, `git pull --ff-only`
2. Copy `pi/*.py`, `pi/probes/*.py`, `pi/version.txt` → `/opt/towerwatch/`
3. `systemctl restart towerwatch`

**Verify post-deploy:**
```bash
ssh <user>@<tailscale-ip> 'journalctl -u towerwatch -n 20 --no-pager'
```
Look for `service_restarted` WARN with the new `BUILD_VERSION`.

**If deploy fails (dirty tree, stale version.txt):**
```bash
./ci.sh       # Re-stamp
git status    # Commit or stash dirty files first
```

---

## Emergency: wipe and re-flash

Only if the Pi is unresponsive and physical access is available:

1. Flash new SD card with Raspberry Pi OS Lite (64-bit).
2. Re-create third partition (twdata, ext4, ~1 GB) — see README Quick Start.
3. `git clone` and `sudo bash pi/install.sh`.
4. Copy `secrets.py` from your secure backup to `/opt/towerwatch/secrets.py`.
5. Previous buffer data on the old card may be recoverable by mounting it on another machine.
