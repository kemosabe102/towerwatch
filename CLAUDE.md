# CLAUDE.md

Agent-facing guide. The user-facing README is imported below and is the source of truth for architecture, probes, deployment, and the data budget — read it before acting, then come back here for the agent-only notes.

@README.md

---

## Bench harness and ops docs

`pi/bench/` contains the failure-mode test harness for pre-deployment validation. See `docs/bench-tests.md` for the per-test research catalog (injection methods, pass criteria) and `docs/runbook.md` for the symptom-indexed ops runbook for the remote-deployment phase. Run `python pi/bench/run.py --list` for available tests; run on the Pi over SSH.

## Post-change workflow (required after any code change)

Every code change goes through **CI → push → CD**, in that order, run from the dev machine:

1. `./ci.sh` — fast mode: ruff lint + format-check, pyright, pytest, clean-tree check, stamps `src/towerwatch/_version.txt` with `<short-hash> <iso-date>`.
2. `./ci.sh full` — fast + a 30s smoke run. Run before deploying.
3. **`git push origin <branch>`** — `scripts/deploy.sh` runs `git pull --ff-only` on the Pi against the *remote*, so any unpushed local commits silently won't ship. The Pi will report "Already up to date" and restart on the previous version. Always push before deploy.
4. `./scripts/deploy.sh <user@host>` — SSHes to the Pi, `git pull --ff-only` on the current branch, `pip install --upgrade .` into `/opt/towerwatch/.venv`, then restarts the service. **Refuses to deploy** unless `src/towerwatch/_version.txt` exists and is at least as new as every `.py` under `src/`.

Failure modes to expect:
- **Dirty working tree** blocks stamping. Commit or stash first (or `./ci.sh fast --allow-dirty` for local experiments — do not deploy the result).
- **`scripts/deploy.sh` says _version.txt is stale**: a `.py` changed after the last stamp. Re-run `./ci.sh`.
- **Pi reports "Already up to date" but you just committed**: you forgot to `git push`. The version stamp shipped via SCP says new code, but `pip install` rebuilt the *old* code already on the Pi. Push, then re-run `scripts/deploy.sh`.

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
5. **Verify passwordless sudo:** `sudo -n true && echo OK`. Imager normally writes `/etc/sudoers.d/010_pi-nopasswd`. If this prompts for a password, create the file manually before continuing — `deploy.sh` will hang on `sudo` otherwise.
6. `sudo apt update && sudo apt upgrade -y`.
7. Install Tailscale: `curl -fsSL https://tailscale.com/install.sh | sudo bash`, then `sudo tailscale up --hostname=<hostname>`. Authorize in admin console, tag `tag:towerwatch`. **For unattended/remote nodes, also "Disable key expiry"** in the admin console.
8. `git clone https://github.com/<your-fork>/towerwatch.git` (HTTPS — repo is public, no deploy key needed).
9. Run `sudo bash scripts/partition-pi-data.sh` — grows rootfs to 6 GB, creates `twdata` partition. Idempotent.

**Back on the dev machine:**

10. Run `./ci.sh` first with `LOCATION="towerwatch"` (home creds) active so the test suite passes. The `tests/test_influx_line_format.py` autouse fixture pins `INFLUX_HOST_TAG` to "towerwatch" so per-site credentials no longer break CI; if you've removed that fixture, swap creds back temporarily.
11. Swap active credentials to the per-site file: `cp src/towerwatch/credentials.<site>.py src/towerwatch/credentials.py`. Then `touch src/towerwatch/_version.txt` so `deploy.sh`'s staleness check doesn't reject the cred-swap mtime bump.
12. `./scripts/deploy.sh admin@<tailscale-ip>` — SCPs the per-site `credentials.py` and the freshly-stamped `_version.txt`, runs `pip install --upgrade .` into the Pi's venv, restarts the service.
13. Restore home creds on the dev machine for the next CI run: `cp src/towerwatch/credentials.home.py src/towerwatch/credentials.py`.

**On the Pi, one-time post-deploy:**

14. `sudo journalctl -u towerwatch -f` — confirm `service_started` event with the real BUILD_VERSION (not `"dev"`).
15. Verify a metric reaches Grafana: query `towerwatch_connected{host="<site>"}` against the Prometheus datasource through the Grafana stack proxy.

**For unattended remote nodes only — after metrics confirmed flowing:**

16. Enable read-only root via overlayroot. See `docs/setup-pi.md` §"Read-only root filesystem". Do this *only* after `var-lib-tailscale.mount` is `active` (not just `enabled`) and `fake-hwclock` is configured to write to `/opt/towerwatch/data/`. Otherwise the next reboot loses Tailscale auth and clock state. Reboot once more after writing `/etc/overlayroot.local.conf` to confirm the service comes back cleanly.

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

**Tailscale state on the data partition is owned `towerwatch:towerwatch`, not root, and that's correct.** `install-pi.sh` chowns `/opt/towerwatch/data/tailscale-state/` to `towerwatch:towerwatch` after copying state from `/var/lib/tailscale/`. `tailscaled` runs as root but tolerates non-root ownership of its state directory — verified on the home Pi which has been running this layout for months. **Don't "fix" this** by chowning to root; doing so during a live session can cause tailscaled to lose its state across the next bind-mount activation.

**`towerwatch-user` SSH access needs the operator's pubkey explicitly seeded.** Pi OS Imager only writes the operator's pubkey into `~admin/.ssh/authorized_keys`. The `towerwatch-user` account (created by `install-pi.sh` for the manual-speedtest CLI) has its own home directory and its own `authorized_keys`, separate from `admin`'s. As of this commit, `install-pi.sh` auto-copies `~admin/.ssh/authorized_keys` → `/home/towerwatch-user/.ssh/authorized_keys` (idempotent: only seeds if the destination is empty/missing). On Pis installed before this change, the file is absent and `ssh towerwatch-user@<pi>` fails with "Permission denied (publickey)" until manually provisioned per `docs/setup-pi.md` Option B. Re-running `install-pi.sh` on those Pis fixes it.

**`ssh towerwatch-user@<pi> --triggered-by <name>` fails with `ssh: unknown option -- -`.** ssh parses `--triggered-by` as its own option before forwarding to the remote command. Use `ssh towerwatch-user@<pi> -- --triggered-by <name>` (the bare `--` ends ssh's option parsing). Quoting the flags (`"--triggered-by <name>"`) does **not** help — Bash strips the quotes before ssh sees them. Documented for end users in `docs/manual-speedtest.md` troubleshooting.

**Passwordless sudo for `admin` comes from Pi OS Imager, not `install-pi.sh`.** When you set the username + password in Imager's advanced settings, cloud-init writes `/etc/sudoers.d/010_pi-nopasswd` with `admin ALL=(ALL) NOPASSWD: ALL`. Without that file, `scripts/deploy.sh` will fail mid-run on `sudo` calls (it doesn't pass passwords). Verify on a fresh Pi before running `install-pi.sh`:

```bash
ls /etc/sudoers.d/010_pi-nopasswd && sudo cat /etc/sudoers.d/010_pi-nopasswd
```

If absent (rare — would mean Imager skipped or you used a different image), create it manually:

```bash
echo "admin ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/010_pi-nopasswd
sudo chmod 440 /etc/sudoers.d/010_pi-nopasswd
sudo visudo -c -f /etc/sudoers.d/010_pi-nopasswd   # validate before trusting it
```

This is intentionally not in `install-pi.sh` — sudoers changes are security-sensitive and the operator should consciously review/approve them, not have them applied silently by a bootstrap script.

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
- **Data budget is a hard constraint, not a guideline.** The always-on probes are ~230 MB/month. The Cloudflare adaptive throughput probe (2/day, default caps 400 MB down + 150 MB up per run) adds up to ~10 GB/month on top of that — accuracy tradeoff was deliberate. Any change that adds further traffic (new probes, more frequent runs, raising the per-direction caps) must be justified against this baseline. Per-site overrides via `CLOUDFLARE_THROUGHPUT_MAX_TOTAL_BYTES_OVERRIDE` / `CLOUDFLARE_UPLOAD_MAX_TOTAL_BYTES_OVERRIDE` are the lever for metered sites.
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


