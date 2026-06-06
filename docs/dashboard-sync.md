# Dashboard sync to Grafana Cloud

`grafana/dashboard.json` and `grafana/dashboard-compare.json` are pushed to
Grafana Cloud automatically by the **Sync Dashboards** GitHub Actions workflow
(`.github/workflows/sync-dashboards.yml`) on every push to `main` that touches
`grafana/*.json`. The push overwrites the existing dashboards **in place by
`uid`** — no more duplicate-on-import.

| Dashboard file | `uid` |
|---|---|
| `dashboard.json` | `towerwatch-main` |
| `dashboard-compare.json` | `towerwatch-compare` |

## One-time setup

### 1. Create a stack service-account token

The dashboard API lives on the **stack** (`https://towerwatch.grafana.net`) and
needs a **service-account token** — *not* an Access Policy token. Access Policy
tokens (grafana.com → Access Policies, scopes like `metrics`/`logs`) push
telemetry data and **cannot write dashboards**.

1. Go to `https://towerwatch.grafana.net` → **Administration → Users and access
   → Service accounts**.
2. **Add service account**: name `github-dashboard-sync`, role **Editor**.
3. **Add token**, copy it (starts with `glsa_`). You can't view it again.

### 2. Add the GitHub repo secret + variable

In the GitHub repo → **Settings → Secrets and variables → Actions**:

- **Secrets** tab → **New repository secret**:
  `GRAFANA_SA_TOKEN` = the `glsa_...` token from step 1.
- **Variables** tab → **New repository variable**:
  `GRAFANA_URL` = `https://towerwatch.grafana.net`

(The URL is a non-secret *variable*; the token is a *secret*.)

That's it. The next push that changes a dashboard JSON syncs automatically. You
can also trigger a manual re-sync from the **Actions** tab → **Sync Dashboards**
→ **Run workflow**.

## Running it by hand

The workflow is a thin wrapper around `scripts/sync_dashboards.py`, which you can
run locally:

```bash
GRAFANA_URL=https://towerwatch.grafana.net \
GRAFANA_SA_TOKEN=glsa_... \
python scripts/sync_dashboards.py
```

## Why the script transforms the JSON

The dashboard files are stored in Grafana **export** format: they carry an
`__inputs` block and a `${DS_PROMETHEUS}` datasource placeholder that the UI
import wizard resolves interactively. The raw `POST /api/dashboards/db` API does
**not** process `__inputs` — it would store the literal `${DS_PROMETHEUS}` as a
datasource UID and break those panels. `scripts/sync_dashboards.py` resolves the
placeholder to `grafanacloud-towerwatch-prom`, strips `__inputs`/`__requires`/
`id`, and wraps the body with `overwrite: true` before pushing.

## Gotchas

- **Empty `uid` = duplicate on every push.** The script refuses to sync a
  dashboard with a blank `uid`. Keep the UIDs in the table above stable.
- **Editing in the UI is not blocked.** This is one-directional (Git → Grafana).
  If someone edits a synced dashboard in the Grafana UI, the next CI push
  overwrites their change. Treat the JSON in this repo as the source of truth.
- **Datasource UIDs are stack-specific.** `grafanacloud-towerwatch-prom` /
  `grafanacloud-towerwatch-logs` are this stack's names (Connections → Data
  sources). If the stack is recreated with different datasource UIDs, update
  `PROM_DS_UID` in `scripts/sync_dashboards.py` and the hardcoded Loki UID in
  the JSON.
