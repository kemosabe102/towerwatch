# CLAUDE.md

Agent-facing guide. The user-facing README is imported below and is the source of truth for architecture, probes, deployment, and the data budget — read it before acting, then come back here for the agent-only notes.

@README.md

---

## Editing entry points

Work the code in this order:

1. `pi/config.py` — all tunable constants (targets, intervals, URLs, buffer paths, `LOG_EVENT_*` identifiers). Source of truth for behaviour.
2. `pi/towerwatch.py` — the 60 s main loop.
3. `pi/probes/` — per-probe modules (ping, dns, tcp, http, m6, ookla).

## Invariants — do not "clean up"

- **Metric units are `_ms`, not seconds.** Prometheus convention says seconds; dashboards query `_ms`. Don't normalise.
- **Target labels are baked into field names** (`rtt_avg_google`, `jitter_cloudflare`), not Prometheus label selectors. Dashboards query by metric name — do not refactor into labels.
- **`LOKI_PUSH_LEVEL = "WARN"` in production.** `INFO` will flood Loki and burn the data budget. Only flip to `INFO` for local dev.
- **Buffer capped at 512 KB** (`BUFFER_MAX_BYTES`) — the data partition is 1 GB; don't raise this without thinking.
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

- **Secrets live on the Pi at `/opt/towerwatch/secrets.py`** — never committed, never copied back by `deploy.sh`. Editing `secrets.py.example` in the repo does NOT update the running service.
- **`deploy-local.sh` is a gitignored wrapper** that hardcodes the host. Use `deploy.sh <user>@<host>` in docs and suggestions.
- **Outage-annotation token is a one-time bootstrap:** Grafana Cloud → Administration → Service accounts → create one with `annotations:write` → paste into `GRAFANA_ANNOTATION_TOKEN` on the Pi. `GRAFANA_ANNOTATIONS_URL` in `config.py` must point at `<stack>.grafana.net` (user-facing URL), not the `prometheus-prod-*` push endpoint.

## Deferred boot warnings

Warnings emitted before the network is up are queued and flushed on the first successful metric push — don't "fix" this by swallowing them or pushing early.
