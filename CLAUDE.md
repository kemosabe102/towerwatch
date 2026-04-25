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

## First-time Pi onboarding (ordered)

For onboarding a brand-new Pi (vs. deploying changes to an existing one). Each step assumes the previous succeeded; never reorder.

**On the dev machine, before booting the Pi:**

1. Flash SD with Imager. In advanced settings: hostname (`towerwatch-<site>`), username `admin`, **paste your `~/.ssh/id_ed25519.pub` into "Allow public-key authentication only"**, no password auth.
2. Edit `/Volumes/bootfs/cmdline.txt` (or equivalent on Linux/Windows) to remove the bare `resize` token (legacy Pi OS may use `init=/usr/lib/raspberrypi-sys-mods/firstboot` instead — `cat` first to see). This stops rootfs from auto-expanding to fill the card. Eject properly.
3. Create a per-site credentials file: `cp src/towerwatch/credentials.py.example src/towerwatch/credentials.<site>.py`, set `LOCATION="<site>"`, fill in Grafana creds. Per-site files are gitignored via `credentials.*.py` pattern.

**On the Pi, after first boot:**

4. SSH in: `ssh admin@<hostname>.local` (key from Imager, no password).
5. `sudo apt update && sudo apt upgrade -y`.
6. Install Tailscale: `curl -fsSL https://tailscale.com/install.sh | sudo bash`, then `sudo tailscale up --hostname=<hostname>`. Authorize in admin console, tag `tag:towerwatch`, optionally disable key expiry.
7. `git clone https://github.com/<your-fork>/towerwatch.git`.
8. Run `sudo bash scripts/partition-pi-data.sh` — grows rootfs to 6 GB, creates `twdata` partition. Idempotent.

**Back on the dev machine:**

9. Swap active credentials: `cp src/towerwatch/credentials.<site>.py src/towerwatch/credentials.py`.
10. `./ci.sh` — stamps `_version.txt`. (Tests assume `LOCATION="towerwatch"`, so they'll fail with a mismatched per-site `LOCATION`. Either run CI with home creds active and swap to per-site creds *only* for deploy, or fix the tests to be LOCATION-agnostic — see "Test fragility" below.)
11. `./scripts/deploy.sh admin@<tailscale-ip>` — SCPs the per-site `credentials.py` and the freshly-stamped `_version.txt`, runs `pip install --upgrade .` into the Pi's venv, restarts the service.

**On the Pi, one-time post-deploy:**

12. `sudo systemctl restart towerwatch && sudo journalctl -u towerwatch -f` — confirm `service_started` event with the real BUILD_VERSION (not `"dev"`).
13. Verify a metric reaches Grafana: query `towerwatch_connected{host="<site>"}` against the Prometheus datasource through the Grafana stack proxy.

**Why this order matters:**
- `install-pi.sh` runs *before* `deploy.sh` because it creates the venv, systemd unit, and data partition mount that `deploy.sh` relies on. After install, BUILD_VERSION shows `"dev"` until the first deploy ships `_version.txt`.
- `partition-pi-data.sh` runs *before* `install-pi.sh` because install-pi.sh expects `/dev/mmcblk0p3` (`twdata`) to exist when it sets up the fstab entry and `tailscale-state` bind-mount.
- Tailscale `up` runs *before* `install-pi.sh` because install-pi.sh detects an existing `/var/lib/tailscale/` and migrates state into the data partition. Reversing this loses the auth.
- Per-site credentials swap happens after `git clone` on the Pi but before `install-pi.sh`, so the in-repo `credentials.py` on the Pi already has the right `LOCATION` for the first run.

## Onboarding gotchas (learned the hard way)

**`parted` prompts on a mounted partition even with `-s`.** Modifying a partition that's currently mounted (e.g. resizing rootfs while booted from it) makes `parted` print "Partition is being used. Are you sure?" and wait on stdin. The `-s`/`--script` flag does *not* suppress this. The reliable scripted form is `printf 'Yes\n' | parted ---pretend-input-tty <DEVICE> ...`. `partition-pi-data.sh` uses this for the rootfs grow.

**`deploy.sh` rejects deploys when any `.py` mtime > `_version.txt` mtime.** This is the staleness check protecting against deploying code that CI hasn't seen. But swapping per-site credentials (`cp credentials.<site>.py credentials.py`) bumps the mtime past the stamp, even though no source actually changed. Fix: `touch src/towerwatch/_version.txt` after the cred swap, then re-run `./scripts/deploy.sh`. **Don't** edit `.py` files between `ci.sh` and `deploy.sh` — the staleness check is doing its job and you should re-run CI.

**Pi-side `git clone` uses HTTPS, not SSH.** The repo is public, so `git clone https://github.com/<fork>/towerwatch.git` works without provisioning a deploy key on each Pi. The dev-machine remote stays on `git@github.com:...` for push auth via your SSH key.

**Tailscale auth is interactive on first `tailscale up`.** It prints an auth URL the operator must open in a browser. Scripts that try to chain `tailscale up` into the next step will block. Either (a) run `tailscale up` manually in a separate session and wait for the human to authorize, or (b) use `tailscale up --auth-key=tskey-...` with a pre-minted reusable key from the admin console for fully-automated provisioning. The watchdog timer installed by `install-pi.sh` keeps the connection healthy after auth.

## Test fragility — LOCATION-coupled assertions (fixed)

`tests/test_influx_line_format.py` historically hard-coded `host=towerwatch` in its assertions, so swapping `credentials.py` to a different `LOCATION` would break CI. As of `tests/test_influx_line_format.py`'s autouse `_pin_host_tag` fixture, `INFLUX_HOST_TAG` is monkeypatched to `"towerwatch"` for the duration of those tests, regardless of the active credentials file. CI now passes on per-site credentials. Don't remove that fixture without first auditing every assertion in the file.

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
- **`credentials.py` is mode 640 owned by `towerwatch:towerwatch`** (not 600). The `towerwatch-user` SSH login account reads it via group membership to run the speedtest CLI. Do not "tighten" to 600 — that breaks `ssh towerwatch-user@pi`.
- **`/usr/local/bin/towerwatch-speedtest` symlink → `/opt/towerwatch/.venv/bin/towerwatch-speedtest`.** sshd's `ForceCommand` for `towerwatch-user` resolves this stable path. Both `install-pi.sh` and `deploy.sh` refresh it idempotently; don't remove either call.

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
- **`towerwatch-user` is created by `install-pi.sh`, not by `deploy.sh`.** A first-time deploy to a new Pi without re-running `install-pi.sh` will leave the speedtest CLI working for `admin` but broken for `towerwatch-user` (no account, no sshd drop-in, no symlink). Re-run `install-pi.sh` on the Pi after any change to user/sshd/symlink wiring.
- **`deploy-local.sh` is a gitignored wrapper** that hardcodes the host. Use `scripts/deploy.sh <user>@<host>` in docs and suggestions.
- **Outage-annotation token needs `datasources:read`:** The `GRAFANA_ANNOTATION_TOKEN` service account is used by both towerwatch (annotations write) and the bench harness (datasource resolution + Loki/Prom reads). It needs `annotations:read`, `annotations:write`, and `datasources:read` in the Grafana service account permissions.
- **`GRAFANA_API_KEY` is push-only:** it authenticates Prometheus/Loki write endpoints, not the Grafana stack API. Do not use it for read queries.


