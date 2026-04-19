# Towerwatch bench harness

Automated failure-mode test harness for pre-deployment validation. Run on the Pi over SSH.

---

## Quick start

```bash
# On the Pi (or via SSH)
cd /opt/towerwatch

# List all available tests
python3 bench/run.py --list

# Run a single low-risk test first (smoke check)
python3 bench/run.py --test service_lifecycle

# Run all tests except the reboot test
python3 bench/run.py --all --skip reboot_survival

# Run the reboot test separately (Pi will reboot)
python3 bench/run.py --test reboot_survival

# Clear a stuck sentinel after an aborted run
python3 bench/run.py --restore

# Attach a note to the last report (manual tests)
python3 bench/run.py --note "power cycle: clean recovery, flush confirmed"
```

Reports are saved to `/opt/towerwatch/data/bench/reports/report_<run_id>.json`.

---

## Prerequisites

- `secrets.py` present at `/opt/towerwatch/secrets.py` with:
  - `GRAFANA_API_KEY` — needs `metrics:read` + `logs:read` scope
  - `GRAFANA_ANNOTATION_TOKEN` — needs `annotations:read` + `annotations:write`
  - `LOKI_URL`, `LOKI_USER`, `LOKI_TOKEN` — for tee-ing bench events to Loki
- `towerwatch` service must be `active` before running (preflight check enforces this)
- Root / sudo for iptables, tc, mount, systemctl operations

---

## Test timeouts

Most tests complete in 5–15 min. `full_network_loss` can take up to 30 min (12 min outage + 20 min annotation polling). `reboot_survival` adds Pi reboot time (~5 min) plus observation.

The harness enforces per-test wall-clock caps (see `timeout_s` on each test class). A runaway test is SIGTERMed and its restore path runs automatically.

---

## Safety rails

- **Sentinel file** at `/opt/towerwatch/data/.bench-in-progress` blocks concurrent runs.
- Every iptables mutation is preceded by `iptables-save`; restore is `iptables-restore`.
- `tc qdisc del` on restore removes any netem qdiscs.
- Clock steps: `timedatectl set-ntp false` → step → `timedatectl set-ntp true` on restore.
- systemd config overrides use drop-ins at `/etc/systemd/system/towerwatch.service.d/bench-*.conf`, removed on restore.
- No file under `/opt/towerwatch/*.py` is ever modified by the harness.
- `atexit` + `SIGTERM`/`SIGINT` handlers all route to the same restore path.

If a run aborts uncleanly, run `python3 bench/run.py --restore` before starting a new run.

---

## Adding a test

1. Create `pi/bench/tests/test_<name>.py` with a `Test` class subclassing `BenchTest`.
2. Implement `inject()`, `observe()`, `restore()`. Set `name`, `description`, `timeout_s`.
3. Add the module path to `TEST_MODULES` in `run.py`.
4. Add a research section to `docs/bench-tests.md`.

```python
from ..tests.base import BenchTest

class Test(BenchTest):
    name = "my_test"
    description = "What it does and what proves it worked"
    timeout_s = 600

    def inject(self): ...
    def observe(self) -> dict: ...
    def restore(self): ...
```

---

## Expected-failure tests

Three tests assert currently-known bugs:

| Test | Bug | Flips to FAIL when |
|---|---|---|
| `loki_429` | Non-2xx Loki responses silently swallowed | PR adds Loki error logging |
| `clock_skew_backward` | Negative push-gap → bogus annotation math | PR adds gap clamping |
| `config_drift` (11a) | Empty `LOKI_URL` → AttributeError on flush | PR tolerates empty LOKI_URL |

These tests pass deployment even with the bugs present; they become CI regression gates once the fixes land.

---

## Verification sequence

1. `python3 -m pyflakes pi/bench/` — import/syntax check
2. `python3 bench/run.py --list` — smoke: prints test table
3. `python3 bench/run.py --restore` — on clean Pi: "nothing to restore", exit 0
4. `python3 bench/run.py --test service_lifecycle` — single test end-to-end
5. `python3 bench/run.py --all --skip reboot_survival` — full suite
6. `python3 bench/run.py --test reboot_survival` — run separately

See `docs/bench-tests.md` for per-test research and `docs/runbook.md` for ops guidance.
