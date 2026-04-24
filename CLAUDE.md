# CLAUDE.md

Agent-facing guide. The user-facing README is imported below and is the source of truth for architecture, probes, deployment, and the data budget — read it before acting, then come back here for the agent-only notes.

@README.md

---

## Bench harness and ops docs

`pi/bench/` contains the failure-mode test harness for pre-deployment validation. See `docs/bench-tests.md` for the per-test research catalog (injection methods, pass criteria) and `docs/runbook.md` for the symptom-indexed ops runbook for the remote-deployment phase. Run `python pi/bench/run.py --list` for available tests; run on the Pi over SSH.

## Post-change workflow (required after any code change)

Every code change goes through **CI then CD**, in that order, run from the dev machine:

1. `./ci.sh` — fast mode: ruff lint + format-check, pyright, pytest, clean-tree check, stamps `src/towerwatch/_version.txt` with `<short-hash> <iso-date>`.
2. `./ci.sh full` — fast + a 30s smoke run. Run before deploying.
3. `./scripts/deploy.sh <user@host>` — SSHes to the Pi, `git pull --ff-only` on the current branch, `pip install --upgrade .` into `/opt/towerwatch/.venv`, then restarts the service. **Refuses to deploy** unless `src/towerwatch/_version.txt` exists and is at least as new as every `.py` under `src/`.

Failure modes to expect:
- **Dirty working tree** blocks stamping. Commit or stash first (or `./ci.sh fast --allow-dirty` for local experiments — do not deploy the result).
- **`scripts/deploy.sh` says _version.txt is stale**: a `.py` changed after the last stamp. Re-run `./ci.sh`.

`cd.sh` is a thin shim that execs `scripts/deploy.sh` — old muscle memory keeps working.

`BUILD_VERSION` / `BUILD_DATE` are loaded by `config.py` from `_version.txt`; they appear in the `service_restarted` log and in outage-annotation text. Don't re-derive them from `git` on the Pi — version authority lives on the dev machine.

## Editing entry points

Work the code in this order:

1. `src/towerwatch/config.py` — all tunable constants (targets, intervals, URLs, buffer paths, `LOG_EVENT_*` identifiers). Source of truth for behaviour.
2. `src/towerwatch/app.py` + `src/towerwatch/tick.py` — the 60 s main loop and per-tick orchestration. `main.py` is the compose root.
3. `src/towerwatch/probes/` — per-probe modules (ping, dns, tcp, http, m6, ookla).
4. `src/towerwatch/clients/` — GrafanaClient + LokiClient (outbound HTTP adapters).

See [`docs/architecture.md`](docs/architecture.md) for the design narrative.

## Invariants — do not "clean up"

- **Metric units are `_ms`, not seconds.** Prometheus convention says seconds; dashboards query `_ms`. Don't normalise.
- **Target labels are baked into field names** (`rtt_avg_google`, `jitter_cloudflare`), not Prometheus label selectors. Dashboards query by metric name — do not refactor into labels.
- **`INFLUX_HOST_TAG` is loaded lazily from `credentials.LOCATION`.** It's the per-site identifier baked into every metric line and Loki stream. Do not convert this back to a hard-coded constant — each deployment has its own `LOCATION`. Default fallback is `"towerwatch"` to preserve single-site history.
- **`LOKI_PUSH_LEVEL = "INFO"`; per-tick logs must NOT use `loki.push`/`loki.log_and_push`.** The Loki gate is informational, not the throttle. The actual throttle is: anything that fires every tick (~1/min) or every push (~30/hour) stays out of the Loki call surface entirely — use stdlib `log.debug`/`log.info` only. `loki.push` is reserved for events that fire per-restart, per-state-change, or at most a few times per day. New event types must justify their cadence against the ~230 MB/month data budget.
- **Buffer capped at 256 KB** (`LOKI_BUFFER_MAX_BYTES`) — the data partition is 1 GB; don't raise this without thinking.
- **Data budget is a hard constraint, not a guideline.** Any change that adds network traffic (new probes, larger samples, higher frequencies, smaller batches) must be justified against the ~230 MB/month baseline. Ookla stays manual-only.

## Log events

Use existing `LOG_EVENT_*` constants from `config.py` — don't invent new string literals. Dashboards and LogQL alerts filter on these stable keys.

## Windows dev mechanics

The script runs on Windows for dev. Platform gates via `sys.platform`:

- Ping flags: `-n`/`-w` (Windows) vs `-c`/`-W` (Linux)
- Paths: `./data/` (Windows) vs `/opt/towerwatch/data/` (Linux)
- Speedtest binary: `./speedtest_bin/speedtest.exe` vs `/usr/bin/speedtest`
- Skips `mountpoint` check on Windows

Router signal polling and speedtest fail gracefully off-network — that's expected locally.

## Deploy gotchas

- **`scripts/deploy.sh` deploys credentials:** `src/towerwatch/credentials.py` is SCP'd to the checked-out repo on the Pi on every deploy, *before* `pip install` runs. Keep the dev-machine copy up to date — it's the source of truth for credentials. `/opt/towerwatch/` is owned by `towerwatch:towerwatch`; deploy stages the file via `/tmp` then moves it into the repo tree.
- **Multi-site deploys need per-Pi credentials.py:** `LOCATION` must differ between Pis. Keep separate credentials files on the dev box (e.g. `credentials.home.py`, `credentials.remote.py`) and swap the active one before running `scripts/deploy.sh`, or deploy from per-host branches. Mixing `LOCATION` values across deploys creates dashboard discontinuities.
- **`deploy-local.sh` is a gitignored wrapper** that hardcodes the host. Use `scripts/deploy.sh <user>@<host>` in docs and suggestions.
- **Outage-annotation token needs `datasources:read`:** The `GRAFANA_ANNOTATION_TOKEN` service account is used by both towerwatch (annotations write) and the bench harness (datasource resolution + Loki/Prom reads). It needs `annotations:read`, `annotations:write`, and `datasources:read` in the Grafana service account permissions.
- **`GRAFANA_API_KEY` is push-only:** it authenticates Prometheus/Loki write endpoints, not the Grafana stack API. Do not use it for read queries.


