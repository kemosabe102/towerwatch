# Towerwatch bench-tests catalog

Per-test research: injection mechanisms considered, chosen approach, and pass criteria.
Run with `python pi/bench/run.py --list` to see all tests and timeouts.

---

## 1. `full_network_loss`

**Intent:** Drop all egress for 12 min; prove buffering, reconnect flush, and outage annotation all work end-to-end.

**Mechanism options considered:**
- `iptables -I OUTPUT -j REJECT` — immediate, reversible with `iptables-restore`, affects all egress. **Chosen.**
- `iptables -j DROP` — same but connection hangs silently (timeout-heavy). REJECT is faster for test turnover.
- Unplug Ethernet physically — not scriptable; excluded.

**Pass criteria (all three required):**
1. `connection_down` Loki event appears after inject.
2. `log_buffer_flushed` event appears after network restore (buffered lines drained).
3. Grafana annotation posted with region duration ≥ 10 min and overlapping injection window.

**Timing:** 12 min outage + up to 20 min annotation polling = up to 32 min total. Annotation timeout is 1200 s to allow push batch (2 min) + Grafana ingestion (1–2 min) + polling slack.

---

## 2. `partial_network`

**Intent:** Block port 443 only; confirm ICMP probes still run, push fails, batch dropped.

**Mechanism options considered:**
- `iptables -p tcp --dport 443 -j REJECT` — precise; ICMP unaffected. **Chosen.**
- Block by destination IP (Grafana push endpoint) — fragile if IP rotates; port is the real invariant.

**Pass criteria:** `metrics_push_failed` Loki event; ping metrics still present in Prom during block window.

---

## 3. `prom_5xx`

**Intent:** Redirect Prom push host to a local 503 responder via systemd drop-in `Environment=`.

**Mechanism options considered:**
- systemd drop-in `Environment=GRAFANA_PUSH_URL_OVERRIDE=...` — clean, no iptables, reversible with drop-in removal. **Chosen.**
- `/etc/hosts` redirect — persists across reboots if not cleaned; riskier.
- iptables REDIRECT — requires kernel NAT; overkill.

**Pass criteria:** `metrics_push_failed` event with 503 context; service does not crash; batch continues to drop each push cycle.

---

## 4. `loki_429` *(expected-failure)*

**Intent:** Redirect Loki push to a local 429 responder; document that non-2xx Loki responses are silently swallowed.

**Mechanism:** Same drop-in pattern as test 3, targeting `LOKI_URL`.

**Expected-failure logic:** Test PASSES while the bug is present (no `log_push_failed` event emitted). Once a PR adds non-2xx Loki error logging, this test FAILS, acting as a regression gate.

**Pass criteria (while bug exists):** No `log_push_failed` event within the injection window; no crash.

---

## 5. `buffer_cap_and_corrupt`

**Intent:** Fill the Loki JSONL buffer beyond 256 KB, inject a corrupt line mid-file; verify 10% trim and safe skip.

**Mechanism options considered:**
- Write synthetic entries directly to the buffer file while service is stopped. **Chosen** — deterministic, no race.
- Generate real events fast enough to overflow — too slow and depends on probe cadence.

**Pass criteria:** `log_buffer_flushed` event after service restart; service remains `active`; subsequent pushes succeed.

---

## 6. `readonly_data_partition`

**Intent:** `mount -o remount,ro /opt/towerwatch/data` for 5 min; service must survive and emit write-fail events.

**Mechanism options considered:**
- `mount -o remount,ro` — reversible with `remount,rw`; no filesystem damage. **Chosen.**
- Remove write permission on the directory — leaves the mount writable; doesn't test the real partition scenario.

**Pass criteria:** `partition_not_detected` WARN event; `systemctl is-active towerwatch` == `active` throughout; clean recovery on `remount,rw`.

---

## 7. `clock_skew_forward`

**Intent:** Step clock +2h; verify the annotation guardrail prevents a spurious outage annotation.

**Mechanism options considered:**
- `timedatectl set-ntp false` + `date -s "+N seconds"` — clean, reversible with `set-ntp true`. **Chosen.**
- Fake the system clock at the process level (LD_PRELOAD libfaketime) — more precise but complex to install on Pi.

**Pass criteria:** No Grafana annotation overlapping the injection window; NTP resyncs on restore.

---

## 8. `clock_skew_backward` *(expected-failure)*

**Intent:** Step clock −30 min; document that negative push-gap produces nonsensical annotation math.

**Expected-failure logic:** Passes while the annotation `timeEnd < time` bug exists. Fails once gap-clamping (clamp gap to ≥ 0 before annotation POST) is implemented.

**Pass criteria (while bug exists):** A Grafana annotation with `timeEnd < time` or negative duration exists in the injection window.

---

## 9. `service_lifecycle`

**Intent:** `systemctl stop` → `start` → `restart`; verify `service_started` and `service_restarted` WARN events appear in Loki with correct `BUILD_VERSION`.

**Pass criteria:** Both events present in Loki within 5 min of each action.

---

## 10. `probe_targets_down`

**Intent:** Block one probe target at a time (google/cloudflare/gateway); per-target metric absent, others unaffected.

**Mechanism:** `iptables -I OUTPUT -d <ip> -j DROP` per target, cycling through all three with restore between sub-cases.

**Pass criteria:** `towerwatch_rtt_avg_<label>` absent in Prom during block; other two targets unaffected.

---

## 11. `config_drift`

Three sub-cases injected via systemd drop-in `Environment=` overrides:

| Sub-case | Override | Expected |
|---|---|---|
| 11a *(xfail)* | `LOKI_URL=` (empty) | Service crashes or `_flush_log_buffer` AttributeError — expected-failure until flush path tolerates empty URL |
| 11b | `GRAFANA_ANNOTATION_TOKEN=` (empty) | Annotation skipped gracefully; no crash |
| 11c | `M6_ADMIN_PASSWORD=` (empty) | M6 probe disables cleanly; no crash |

**Pass criteria:** Service remains `active` for 11b/11c; 11a passes while crash is the actual behavior.

---

## 12. `dns_only_outage`

**Intent:** Block UDP+TCP port 53 to both DNS targets; `dns_failed` events, TCP/ICMP unaffected.

**Mechanism:** `iptables -I OUTPUT -d <ip> -p udp/tcp --dport 53 -j DROP` for each DNS target.

**Pass criteria:** `dns_failed` Loki events; `towerwatch_tcp_connect_ms` metric still present.

---

## 13. `latency_injection`

**Intent:** `tc qdisc netem delay 500ms loss 5%` on eth0; RTT/jitter/loss metrics rise; no false outage annotation.

**Mechanism options considered:**
- `tc netem` — kernel-level, precise, reversible with `tc qdisc del`. **Chosen.**
- Rate-limiting via token bucket filter (TBF) — tests throughput, not RTT; wrong scope here.

**Pass criteria:** `towerwatch_rtt_avg_google > 400` in Prom within observation window; no `connection_down` annotation.

---

## 14. `reboot_survival`

**Intent:** Prove the Pi restarts cleanly and all post-boot invariants hold after an unplanned reboot.

**Mechanism:** Arms a self-disabling systemd oneshot (`towerwatch-bench-resume.service`) that re-invokes `run.py --resume` after boot, then issues `systemctl reboot`.

**Pass criteria:**
1. `service_started` WARN in Loki with timestamp > pre-reboot marker.
2. `towerwatch_connected` metric resumes in Prom.
3. `towerwatch-bench-resume.service` is disabled and removed post-resume.

**Run separately:** `python run.py --test reboot_survival` — the Pi will reboot; SSH back in to verify.

---

## Manual tests

### Power cycle

Pull power; restore; confirm:
- `fake-hwclock` restores sane time before NTP sync.
- `service_started` WARN in Loki.
- Buffered pre-power-loss data flushes on reconnect.
- No annotation for a gap < 10 min (typical quick-cycle).

Log result with `python run.py --note "power cycle: <outcome>"`.

### M6 admin lockout

Change the M6 admin password out-of-band; confirm:
- `m6_auth_expired` Loki events emit (not crash).
- Restore password; confirm m6 probe recovers silently on next poll cycle.

Log result with `python run.py --note "m6 lockout: <outcome>"`.
