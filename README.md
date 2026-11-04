# Bakehouse Databricks PoC

A small, low-cost learning PoC on Azure Databricks. It builds a bakery franchise analytics platform on `samples.bakehouse`, with a simulator that releases one virtual day of data at a time.

It exercises Python ETL jobs, Lakeflow Designer, Databricks Apps (a Dash app that maintains 10 business parameters, backed by Lakebase or Delta), a reusable Python package and Declarative Automation Bundles. Everything uses synthetic or public sample data.

- Design: [`docs/SPEC.md`](docs/SPEC.md)
- Build order: [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md)
- Decisions and departures from the spec: [`docs/decisions.md`](docs/decisions.md)
- Changes needed in the separate Terraform project: [`docs/az-inf-requirements.md`](docs/az-inf-requirements.md)

## Status

Scope: `dev` only. The spec's `test` and `prod` targets, service principals and deploy workflows (build plan workstream 12) are out of scope for this build.

Early. Built and working on Databricks: the `dev` bundle (schemas, volumes), the simulator and the reset job. The ETL (`load_params`, `silver_build`, `gold_build`, chained by `daily_run`) runs on Databricks, with the Delta parameter store. Built and tested locally: the SQLite parameter store and its backend factory. The parameter admin app runs locally (SQLite, or Postgres in Docker) and on Databricks Apps with Lakebase, and the ETL reads the saved parameters from Lakebase. The run buttons start the real jobs, and the app stops itself after a minute of inactivity. Notebooks 01 (SQL), 02 (data-quality report) and 03 (promo what-if, with charts) run in the workspace. Not built yet: the Delta backend in the app, Designer, and the backend comparison notebook.

## Local development

The project uses [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
uv sync                  # create ./.venv and install everything from uv.lock
uv run pytest            # run the tests
uv run ruff check .      # lint
uv run ruff format .     # format
uv add <package>         # add a dependency (updates pyproject.toml and uv.lock)
```

Tests run without cloud access. Tests that need Spark or Databricks are marked `spark` or `databricks`, so `uv run pytest -m "not spark and not databricks"` skips them.

## The parameter app, locally

The Dash app in `apps/param_admin` edits the 10 business parameters. Locally it uses a simulated job trigger, so the run buttons never touch Databricks.

```bash
# Quickest: an in-memory SQLite database that resets on every start
uv run python apps/param_admin/app.py          # http://127.0.0.1:8050

# Against Postgres 16 in Docker (host port 5433, so it avoids a Postgres already on 5432)
cp .env.local.example .env.local               # gitignored; edit the ports if you like
docker compose --env-file .env.local up --build
docker compose down -v                          # back to the seeded defaults
```

Settings are environment variables: `PARAM_BACKEND`, `DATABASE_URL`, `JOB_TRIGGER`, `APP_USER`, `IDLE_STOP_MINUTES`. The app creates its tables and the default parameters when it starts.

## The parameter app on Databricks

`bundle deploy` creates the Lakebase project (scale to zero after 60 seconds, 0.5 to 2 CU), registers it in Unity Catalog, and creates the app **stopped**. The app bills for compute only while it runs.

```bash
databricks bundle run param_admin -t dev -p personal    # start it (a minute or two)
databricks apps stop bakehouse-param-admin-dev -p personal   # stop it when finished
```

The app stops itself after `idle_stop_minutes` of inactivity (page loads and clicks count, the status polling does not). After the first deploy, run `bash scripts/grant_app_self_manage.sh` once so it is allowed to. Open it from the Apps page in the workspace. It signs you in with workspace SSO and records your email as `updated_by`. A save with the effective-from field blank applies to every virtual day. Press Rerun ETL to rebuild gold from the earliest date you saved, or Run next day to release and process one more virtual day. You can also run `bakehouse_etl` with `mode=recompute_gold` (and optionally `from_date=YYYY-MM-DD`). The ETL reads the saved values from Lakebase through its Unity Catalog registration.

The app folder only gets its own files, so every deploy copies `bakehouse_core` into `apps/param_admin/` first (`scripts/stage_app.py`, run by the artifact build). That copy is gitignored.

## How `bakehouse_core` reaches each part

| Where it runs | How it gets the package | Status |
|---|---|---|
| ETL and simulator jobs | The bundle builds a wheel (`uv build --wheel`), uploads it to the `ops.artifacts` volume (`workspace.artifact_path`), and the serverless job environment installs it at the start of each run | Proven |
| The Dash app | A staged copy in `apps/param_admin/`, made by `scripts/stage_app.py` at deploy | Proven |
| Notebook 03 | `%pip install` of the wheel from the `ops.artifacts` volume, then a restart. No fallback | Proven |
| Notebook 02, opened from a Git folder | Not installed. If `import bakehouse_core` fails, the notebook adds the repo's `../src` folder to `sys.path` | Likely, not yet confirmed which path is used |
| Notebooks run as bundle `notebook_task`s | Needs the wheel in the job environment, or `src` synced next to the notebook | Not yet built |

The volume has to exist before a deploy can upload to it, so it was created by an earlier deploy than the one that set `artifact_path`. The notebook's install path names the catalog and the wheel version, so it needs editing if either changes. A notebook imported as a single file has neither the volume install nor the `src` folder, so it cannot import the package.

## Variables, deploy and destroy

Commands use the `personal` Databricks CLI profile (`-p personal`), and no workspace identifiers are stored in the repo.

Variables (defaults are placeholders, override with `--var` or `BUNDLE_VAR_<name>`):

| Variable | Meaning |
|---|---|
| `catalog` | Unity Catalog catalog for the target |
| `idle_stop_minutes` | Idle minutes before anything that bills while running stops |
| `job_timeout_seconds` | Run timeout for jobs |
| `param_backend` | Where the ETL reads parameters: `lakebase` (default) or `delta` |
| `params_catalog` | Unity Catalog name that Lakebase is registered as |
| `job_duration_warning_seconds` | A run longer than this raises a health warning |

```bash
databricks bundle validate -t dev -p personal
databricks bundle plan -t dev -p personal
databricks bundle deploy -t dev -p personal

# Release one virtual day, or several, optionally with scenarios switched on
databricks bundle run simulator -t dev -p personal
databricks bundle run simulator -t dev -p personal --params 'days=3,scenarios=duplicates;null_spike'

# Run the ETL on what has been released (incremental, or mode=recompute_gold)
databricks bundle run bakehouse_etl -t dev -p personal
databricks bundle run bakehouse_etl -t dev -p personal --params mode=recompute_gold

# Release a day, then run the ETL on it
databricks bundle run daily_run -t dev -p personal

# Back to virtual day 0 (confirm must equal the target name)
databricks bundle run ops_reset -t dev -p personal --params level=data,confirm=dev
```

Scenarios: `late_files`, `duplicates`, `null_spike`, `underperforming_franchise`, `negative_review_burst`.

The jobs run on serverless compute. `daily_run` has a schedule, but it is paused, so nothing runs or costs money until you start it. `databricks bundle destroy -t dev -p personal` removes everything the bundle created, including the Lakebase project and its data (`purge_on_delete` is true in dev).
