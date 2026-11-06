# Bakehouse Databricks PoC — Solution Specification

Version 0.1 · 02/11/2026 · Owner: Tim Davies

This PoC builds a small, low-cost analytics platform for a fictional bakery franchise network on Azure Databricks. It exercises notebooks, Lakeflow Designer, Databricks Apps, Python ETL jobs, a reusable Python package and Declarative Automation Bundles, all from one public GitHub repository.

![Solution architecture](bakehouse-architecture.svg)

Editable version: [`bakehouse-architecture.drawio`](bakehouse-architecture.drawio). It uses draw.io's built-in Azure icons. Dashed squares are placeholders for the Databricks, GitHub, Docker, Python and PostgreSQL logos.

---

## 1. Overview

### 1.1 Scenario

Head office of the Bakehouse franchise network wants a daily view of:

- franchise performance against targets, with underperformers flagged
- customer loyalty tiers
- how much each franchise depends on a single supplier
- alerts from customer reviews

Business users tune the rules behind these outputs (10 parameters) in a Dash web app. A simulator releases one "virtual day" of data per run, so the PoC can show many runs over many days.

### 1.2 Features demonstrated

| Feature | Where it appears |
|---|---|
| Notebooks | Exploration, data-quality report, promo what-if, backend cost comparison |
| Lakeflow Designer | Two visual data preps (`*.designer.ipynb`) deployed as job tasks |
| Databricks Apps | Dash parameter-maintenance app |
| ETL (Python jobs) | Simulator writes raw files to bronze, then Auto Loader → silver → gold, as wheel tasks |
| Reusable Python package | `bakehouse_core`, used by the jobs, the app and the tests |
| Lakebase | Parameter store backend A |
| Delta + SQL warehouse | Parameter store backend B |
| Declarative Automation Bundles | Everything is deployed by one bundle with `dev`, `test` and `prod` targets |
| GitHub Actions | CI on PRs, deploy to test on merge, deploy to prod on tag |

### 1.3 Cost principles

- Serverless compute everywhere. There are no always-on clusters.
- Schedules are paused in `dev` and `test`. Runs are triggered manually or as backfills.
- **1-minute idle shutdown** by default for everything that bills while running: Lakebase, the SQL warehouse and the Databricks App (section 6.4).
- Lakebase and the warehouse run at their smallest sizes.
- Sample data only: a few thousand rows per virtual day.
- Every resource is tagged in both Azure and Databricks (section 6.3). Spend is checked weekly in `system.billing.usage` and Azure Cost Management.

### 1.4 Out of scope

Real customer data, private networking (VNet injection, Private Link), multi-workspace isolation, and high availability.

---

## 2. Architecture

The diagram above shows three zones:

1. **Local development (cut-out).** Docker Compose runs the Dash app and a Postgres 16 container that stands in for Lakebase. Unit tests use in-memory SQLite. The developer deploys the `dev` target with the Databricks CLI as themselves.
2. **GitHub.** Pull requests run CI. Merging to `main` deploys `test`. A `v*` tag deploys `prod` after approval. Both deploy as that environment's service principal.
3. **Azure.** One subscription and one resource group hold the Azure Databricks workspace, its storage, its managed resource group, Azure Policy and Cost Management. Entra ID provides user sign-in. The Databricks account console (account-level, not an Azure resource) holds service principals, usage policies and budgets.

### 2.1 Parameter flows

| Flow | Backend A: Lakebase | Backend B: Delta |
|---|---|---|
| App reads at startup | Postgres connection (app SP role) | SQL via serverless warehouse |
| App saves an edit | INSERT a new version row | INSERT via warehouse |
| ETL reads as-of virtual date | Unity Catalog-registered Lakebase catalog (one query per run) | Direct Delta read of `ops.param_versions`, no warehouse |
| Local dev | Postgres container | Dev warehouse (OAuth as you), optional |
| Unit tests | SQLite in-memory | SQLite in-memory |
| Compute billed for app access | Lakebase compute while awake | Warehouse while running |

**Option C (future).** The app could reach Delta through Databricks Connect on an all-purpose cluster shared with the ETL. It is not built in the PoC. Keep it in view if real users need to share compute with the ETL or need Spark from the app.

---

## 3. Data and ETL

### 3.1 Unity Catalog layout

There is one catalog per target: `bakehouse_dev`, `bakehouse_test` and `bakehouse_prod`. The bundle variable `catalog` selects it.

| Schema | Contents |
|---|---|
| `bronze` | Volume `raw` holding the simulator's daily files, `/{entity}/date=YYYY-MM-DD/`. Raw files only, no Delta tables. |
| `silver` | Delta tables loaded by Auto Loader from bronze, with ingest metadata (source file, ingest timestamp, virtual date). Cleaned and deduplicated. `customers` and `franchises` are SCD Type 2. Bad rows go to quarantine tables. |
| `gold` | `franchise_daily_kpis`, `franchise_flags`, `customer_loyalty_tiers`, `supplier_concentration`, `review_alerts`, `sales_vs_target` |
| `ops` | `sim_clock`, `run_log`, `reset_log`, `param_definitions`, `param_versions` (Delta backend), checkpoints volume |

Source: `samples.bakehouse` (`sales_transactions`, `sales_customers`, `sales_franchises`, `sales_suppliers`, `media_customer_reviews`), read-only.

### 3.2 Simulator and virtual clock

- `ops.sim_clock` holds `virtual_date`, `seed` and `phase` (replay or synthetic).
- The **simulator job** advances one day per run. Parameters: `days` (default 1, for backfill), `mode` (`auto`, `replay` or `synthetic`), `scenarios` (a list).
- **Replay phase:** releases the real sample transactions one source day at a time, shifted onto the virtual calendar.
- **Replay length:** the sample covers 1–17 May 2024, so replay runs for 17 virtual days before the synthetic phase starts.
- **Synthetic phase:** after the real days run out, picks a real day as a template and varies it. The variations are volume ±x%, weekday/weekend shape and price drift. It is seeded per date, so every day is reproducible.
- **Other entities:** new reviews daily, sampled from real review text. Occasional new or changed customers (which exercise SCD2). Rare supplier or franchise changes.
- **Injectable scenarios:** late files, duplicates, a null spike, a quietly underperforming franchise, a burst of negative reviews. Each one exercises a data-quality rule or one of the 10 parameters.
- **Writes to:** the bronze volume directly. There is no separate landing zone.
- **Reset:** handled by the separate `ops_reset` job (section 3.6).
- **Code:** `bakehouse_core.simulator`, unit-tested locally.

### 3.3 ETL job (`bakehouse_etl`)

The job runs as wheel tasks on serverless compute. It takes a `mode` parameter: `incremental` (the default, processes new bronze files) or `recompute_gold` (rebuilds gold for every virtual day so far, with each day using its own as-of parameter values). It also takes `triggered_by`, which is recorded in `run_log`. `max_concurrent_runs` is 1.

1. `load_params`: reads all parameters as of `virtual_date` once. It writes the versions used to `ops.run_log` for lineage and passes them on as task values.
2. `silver_build`: Auto Loader with `trigger(availableNow=True)` reads the new bronze files, with checkpoints in the `ops` volume. Unexpected columns go to `_rescued_data`. It adds ingest metadata, then does deduplication, type casting, SCD2 `MERGE` for customers and franchises, and DQ checks against `dq_max_null_pct`. Failing rows go to quarantine.
3. `gold_build`: KPIs, flags, loyalty tiers, supplier concentration, review alerts and targets, all driven by the parameters.
4. `designer_review_alerts` and `designer_supplier_mix`: the visual data prep tasks (section 3.4).

The simulator and ETL are chained in a `daily_run` job (simulate → ETL). Its schedule is paused except in `prod`.

### 3.4 Lakeflow Designer

Lakeflow Designer is generally available. Each visual data prep is stored as `<name>.designer.ipynb` in the Git folder and deployed as a `notebook_task` in the bundle.

- `review_explorer.designer.ipynb`: joins silver reviews and franchises into a review summary by franchise and week.
- `supplier_mix.designer.ipynb`: joins suppliers, franchises and sales into an ingredient share per supplier.

Designer parameters (`catalog`) are overridden per bundle target. Finding (04/11/2026): in this workspace the Source and Output operators only offer **Disabled** in their Parameters view, not the documented Table/Catalog fields. So parameters currently reach a flow only through SQL operators (`IDENTIFIER(:catalog || '.silver.reviews')`), and Output tables are fixed to `bakehouse_dev`. That's fine while the build is dev-only. Rule: edit these files only on the canvas. Code changed outside the editor locks the step until you choose **Regenerate code**.

### 3.5 Notebooks

| Notebook | Purpose |
|---|---|
| `01_explore_samples` | Profile `samples.bakehouse` and confirm the date range |
| `02_dq_report` | Quarantine counts, null rates, late-file report |
| `03_promo_what_if` | Projects revenue at `promo_discount_rate` against the history |
| `04_backend_comparison` | Latency logs plus `system.billing.usage` by tag, Lakebase vs Delta |

### 3.6 Reset actions

One job, `ops_reset`, returns a target to a known starting state. It has two levels, plus a confirmation parameter so it can't run by accident.

| Level | Clears | Keeps |
|---|---|---|
| `data` | Bronze files, silver and gold rows, quarantine tables, Auto Loader checkpoints, `run_log`. `sim_clock` goes back to virtual day 0. | Parameter history (both backends), app metrics |
| `hard` | Everything in `data`, plus parameters in both backends reset to the version 1 defaults, and `ops.app_metrics` | Table definitions, grants, the Lakebase project, the warehouse |

- **Truncate, not drop.** Tables are truncated, so Unity Catalog grants, lineage and app resource bindings survive. Use `rebuild=true` to recreate the tables from the DDL in `bakehouse_core.schema` instead, for when a table definition has changed.
- **Files:** the bronze volume files and checkpoint folders are deleted.
- **Parameters:**
  - Delta: truncate `ops.param_versions`, then insert the seed from `db/seed`.
  - Lakebase: the same steps, connecting as the env SP.
  - The seed is the same file used for local Postgres and the SQLite tests, so every environment starts identical.
- **Result:** after a hard reset, the target matches a fresh deployment: virtual day 0, with all 10 parameters at their defaults. Use the app's Reload button, or restart the app, to pick up the defaults.
- **Safeguards:**
  - `confirm` must equal the target name, e.g. `confirm=test`.
  - In prod, the job is deployed with run permission for Tim only. It refuses `hard` unless `allow_prod=true`.
  - Every reset writes a row to `ops.reset_log` (who, when, level, target). That table is never cleared.
- **Local and dev:**
  - `docker compose down -v && docker compose up` restores the local Postgres to the seed.
  - `scripts/reset_dev.sh` runs `databricks bundle run ops_reset -t dev --params level=hard,confirm=dev`.

---

## 4. Parameter management

### 4.1 The 10 parameters

Defaults are first guesses, to be calibrated in phase 1 from the data's spend distribution. Money is in the source currency.

| Key | Type | Default | Min–max | Used by |
|---|---|---|---|---|
| `loyalty_gold_min_spend` | decimal | 250 | 0–100000 | gold tiers |
| `loyalty_silver_min_spend` | decimal | 100 | 0–100000 | gold tiers (must be < gold) |
| `loyalty_lookback_days` | int | 90 | 7–365 | gold tiers |
| `underperform_pct` | decimal | 0.20 | 0.05–0.50 | franchise flags |
| `min_txn_for_ranking` | int | 50 | 1–10000 | franchise flags |
| `promo_discount_rate` | decimal | 0.10 | 0–0.50 | what-if notebook, gold projection |
| `supplier_concentration_threshold` | decimal | 0.10 | 0.10–1.00 | supplier concentration |
| `review_alert_min_count` | int | 3 | 1–50 | review alerts (7-day window) |
| `dq_max_null_pct` | decimal | 0.02 | 0–0.50 | silver DQ checks |
| `sales_target_uplift_pct` | decimal | 0.05 | -0.50–1.00 | sales vs target |

Cross-field rules, such as silver below gold, are validated in `bakehouse_core.params`, not in the database.

### 4.2 Data model (same in both backends)

- `param_definitions(param_key PK, data_type, min_value, max_value, unit, description)`: seeded by the bundle or a migration.
- `param_versions(param_key, version, value_text, effective_from DATE, updated_by, updated_at, change_note)`, with primary key (`param_key`, `version`).
- The table is **append-only**. An edit inserts a new version and never updates in place. That removes upsert dialect differences (Postgres `ON CONFLICT` vs Delta `MERGE`) and gives a full audit trail.
- **Current value:** the highest version per key.
- **As-of value:** the highest version with `effective_from <= virtual_date`.

### 4.3 Package interface

- `bakehouse_core.params.model`: typed `Parameter` dataclasses plus validation.
- `bakehouse_core.params.repository.ParamRepository`: `load_current()`, `load_as_of(date)`, `save(changes, user, note)`, `history(key)`.
- Implementations on SQLAlchemy Core:
  - `SqlRepository(engine)`: SQLite in tests, Postgres locally and on Lakebase.
  - `DeltaRepository`: uses the `databricks-sqlalchemy` dialect. If that dialect falls short, it uses plain `databricks-sql-connector`.
- **Factory:** `get_repository()` reads `PARAM_BACKEND` (`sqlite`, `postgres`, `lakebase` or `delta`) and the connection settings from the environment.
- **Lakebase auth:** an OAuth token from the Databricks SDK, refreshed through a SQLAlchemy `do_connect` hook. Locally, a static `PGPASSWORD` for the container.

### 4.4 Dash app (`apps/param_admin`)

- Loads all parameters at startup into an editable table showing the value, the range and the effective-from date.
- Validates changes before saving. Each save inserts a new version. A blank effective-from date means "from the start" (every virtual day), because the ETL reads parameters as of the virtual date and a real "today" would never be in force. A history panel shows each parameter's versions.
- `app.yaml` command: `gunicorn app:server`. Locally the same `app.py` runs in Docker.
- **Identity:** the app's own service principal, created automatically by Databricks Apps. Users sign in with workspace SSO, and `updated_by` is taken from the forwarded user header.
- **App resources** (in the bundle):
  - Lakebase backend: a `postgres` resource. This creates the app's Postgres role and injects `PGHOST`, `PGDATABASE` and `PGUSER`.
  - Delta backend: `sql-warehouse` (CAN_USE) plus `table` resources on `ops.param_definitions` (Select) and `ops.param_versions` (Select and Modify).
- **Run buttons:** after a save, the app offers to apply the change:
  - **Rerun ETL** starts `bakehouse_etl` with `mode=recompute_gold`. Gold is rebuilt with the new values from their `effective_from` date onward.
  - **Run next day** starts `daily_run`, which advances the virtual clock and processes one new day.
  - Both pass `triggered_by=<user email>`.
  - While a run is active, the buttons are disabled. The app polls the run state, shows status and duration, and links to the run page.
  - The app checks for active runs before starting one, and the job's `max_concurrent_runs: 1` is the backstop.
- **How it triggers jobs:** `bakehouse_core.jobs.JobTrigger` wraps the Databricks SDK (`jobs.run_now`, `jobs.list_runs`, `jobs.get_run`).
  - On Databricks Apps, job IDs come from `job` app resources with `CAN_MANAGE_RUN`, injected through `valueFrom`.
  - The run executes as the job's `run_as` service principal, not as the app.
  - Locally, `JOB_TRIGGER=databricks` uses your CLI login against the dev jobs. `JOB_TRIGGER=fake` simulates runs for UI work and tests, and is the default.
- **Compute:** the smallest app compute size. The app is stopped by default in `dev` and `test`.

### 4.5 Lakebase configuration

- One Lakebase Autoscaling project per target: `bakehouse-params-${bundle.target}`. It is defined as `postgres_projects` in the bundle (Beta, needs CLI 1.0+). The database is `bakehouse` and the schema is `params`.
- **Compute:** the smallest size, a maximum of 2 CU, scale-to-zero after 60 seconds in every target (section 6.4).
- **Unity Catalog registration:** `postgres_catalogs` creates `bakehouse_params_${target}`, so the ETL reads with Spark SQL.
- **Permissions are two-layer:**
  - Project ACL: the env SP has `CAN_MANAGE` and the app has `CAN_USE`, via the app resource.
  - Postgres roles: the app role owns `params`, and the env SP gets SELECT for the ETL, plus DELETE and INSERT on `param_versions` for the hard reset. Grant this once per target after the first deploy, using `postgres_roles` or a one-off grant script in `scripts/`.

### 4.6 Delta configuration

- Tables `ops.param_definitions` and `ops.param_versions` in the target catalog.
- A serverless SQL warehouse `bakehouse-params-wh-${target}`: 2X-Small, `auto_stop_mins: 1` (section 6.4), max 1 cluster, tagged.
- The ETL reads Delta directly in its own job, so it never needs the warehouse.

### 4.7 Comparison plan

Both backends run in `test` for 14 virtual days. A small exerciser script makes 20 reads and 5 saves per day.

| Measure | How |
|---|---|
| App cold-start and warm load latency | App logs timing → `ops.app_metrics` |
| Save latency | App logs |
| ETL parameter read time | `run_log` timing |
| Cost per day, idle and active | `system.billing.usage` grouped by `component` tag |
| Local development experience | Notes: setup steps, offline ability |
| Governance and lineage | Unity Catalog lineage view, audit trail |
| Code complexity | Lines of code per backend, test count |

The result is a recommendation in `docs/backend-comparison.md`, covering when option C (Databricks Connect on a shared all-purpose cluster) would win.

---

## 5. Repository, package and bundle

### 5.1 Layout

```
bakehouse-dbx-poc/
├── CLAUDE.md                     # existing project file; imports docs/CLAUDE_CODE_GUIDE.md
├── databricks.yml                # bundle, variables, targets dev/test/prod
├── infra/                        # Terraform foundation (bootstrap/ for state)
├── resources/
│   ├── jobs.yml                  # simulator, etl, daily_run
│   ├── app.yml                   # param_admin app + resources
│   ├── lakebase.yml              # postgres_projects / catalogs / roles
│   ├── warehouse.yml             # params SQL warehouse
│   └── schemas.yml               # UC schemas + volumes
├── src/bakehouse_core/           # reusable package (wheel)
│   ├── params/  simulator/  transforms/  dq/  io/
├── jobs/                         # thin entry points for wheel tasks
├── designer/                     # *.designer.ipynb (edit on canvas only)
├── notebooks/                    # 01_…04_
├── apps/param_admin/             # app.py, app.yaml, requirements.txt, Dockerfile
├── docker-compose.yml            # app + postgres:16 + seed
├── db/                           # SQL DDL + seed (shared by Postgres and SQLite)
├── tests/                        # pytest, SQLite in-memory
├── scripts/                      # one-off grants, exerciser, reset helpers
├── docs/                         # SPEC.md, BUILD_PLAN.md, CLAUDE_CODE_GUIDE.md, diagrams, comparison
├── .github/workflows/            # ci.yml, deploy.yml
├── .gitleaks.toml  pyproject.toml  uv.lock
```

### 5.2 Package

- `pyproject.toml` with `uv` and `hatchling`, built by the bundle `artifacts` section into a wheel.
- The jobs use `python_wheel_task` with entry points (`bakehouse-sim`, `bakehouse-etl`).
- The app installs the package from the repo path in `requirements.txt` (`-e ../../` locally, or the built wheel when deployed).
- Pin the Python version to match the serverless environment version chosen for the jobs. Check the Databricks Apps runtime version too (open item).

### 5.3 Bundle targets

| Target | Mode | Deploys as | Runs as | Catalog | Schedules |
|---|---|---|---|---|---|
| `dev` | development | Tim (U2M) | Tim | `bakehouse_dev` | paused |
| `test` | production | `sp-bakehouse-test` | `sp-bakehouse-test` | `bakehouse_test` | paused |
| `prod` | production | `sp-bakehouse-prod` | `sp-bakehouse-prod` | `bakehouse_prod` | daily |

- **Change planned (build workstream 14):** dev is rebuilt by hand in the portal (§9.3), and the bundle gets a new `sandbox` target (development mode, as Tim, catalog `bakehouse_sandbox`). That target replaces `dev` as the compute bundle's first target. Schemas and volumes are a separate data bundle (§9.3) that is deployed to every environment, dev included.
- **Deploy and run identity:** when the deploying identity differs from `run_as`, bundles only support jobs and pipelines. The app and Lakebase resources therefore require that CI deploys as the same SP that runs the resources.
- **Workspace host:** supplied through `DATABRICKS_HOST`, never written in `databricks.yml`, because the repo is public.
- **Tags:** `presets.tags` sets `project`, `env: ${bundle.target}` and `owner` on everything the bundle deploys (section 6.3).

### 5.4 CI/CD

- **`ci.yml`** runs on PRs to `main`:
  - setup Python with `uv`
  - `ruff check` and `ruff format --check`
  - `pytest` against SQLite
  - gitleaks
  - `databricks bundle validate -t test`, using the `test` environment's secrets
- **`deploy.yml`:**
  - on push to `main`: `bundle deploy -t test`, then a smoke run of `daily_run` with `days=1`
  - on tag `v*`: the `prod` environment, which has a required reviewer (Tim), then `bundle deploy -t prod`
- **GitHub Environments:**
  - `test` and `prod`, each holding `DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID` and `DATABRICKS_CLIENT_SECRET` as environment secrets
  - no repo-wide secrets
  - push protection stays on

---

## 6. Azure and Databricks setup

### 6.1 Identities

Terraform automates most of the steps in 6.1 and 6.3 (see 6.5). The steps are listed here so they can be checked, or done by hand.

| Identity | Type | Purpose | Grants |
|---|---|---|---|
| Tim | Entra ID user | Develop, deploy dev, approve prod | Azure RBAC Owner on `rg-bakehouse-poc`. Databricks workspace admin + account admin (billing admin to see all usage policies). |
| `sp-bakehouse-test` | Databricks-managed SP | CI deploy + run_as for `test` | Workspace user (not admin). UC: all privileges on `bakehouse_test` only. CAN_MANAGE on its Lakebase project and warehouse. User on the usage policy. |
| `sp-bakehouse-prod` | Databricks-managed SP | CI deploy + run_as for `prod` | As above, for `bakehouse_prod`. |
| App SP (`bakehouse-param-admin-<target>`) | Created automatically by Databricks Apps | The Dash app's runtime identity | Granted only through app resources: Lakebase role, warehouse CAN_USE, table Select/Modify, CAN_MANAGE_RUN on `bakehouse_etl` and `daily_run`. |
| Access connector | Azure managed identity | Unity Catalog access to ADLS (only if an external location is needed) | Storage Blob Data Contributor on the storage account. |

**Steps on the Azure side**

1. In the Azure portal, confirm Tim has **Owner** (or Contributor plus User Access Administrator) on `rg-bakehouse-poc`.
2. Confirm Tim is an **account admin** in the Databricks account console. Azure tenant Global Administrators can grant this the first time.
3. Service principals are **Databricks-managed**, so they need no Entra app registration. If an organisation later requires Entra-managed SPs:
   - register the app in Entra ID
   - add it to the Databricks account using its application ID
   - generate a Databricks OAuth secret for it in the same way
4. Storage: catalog creation already works, so the metastore has managed storage. Check in Catalog Explorer whether it uses **default storage** or an ADLS external location. If it's ADLS, confirm the access connector's managed identity has **Storage Blob Data Contributor** on that account. No action is needed otherwise.

**Steps on the Databricks side**

1. Account console → User management → Service principals → add `sp-bakehouse-test` and `sp-bakehouse-prod`, then assign them to the workspace.
2. For each SP, go to Secrets → generate an OAuth secret with an expiry (for example 180 days). Record the rotation date in `docs/ops.md`.
3. Grant Unity Catalog privileges on each SP's own catalog only. Grant the SPs `CAN_USE` on the usage policy.
4. Copy the client ID and secret into the matching GitHub Environment.

### 6.2 Secrets

- No secrets in the repo, `app.yaml` or bundle files.
- The app uses `valueFrom` for resource values.
- Local `.env` files are gitignored. gitleaks runs in pre-commit and CI.

### 6.3 Tagging

**Tag keys (all lower-case):**

- `project=bakehouse-poc`
- `env=dev|test|prod|shared`
- `owner=tim`
- `component=etl|sim|app|params-lakebase|params-delta|designer|notebook`
- `cost-centre=personal`

Characters are limited to letters, numbers, spaces and `+ - = . _ : / @`, and an Azure resource can have at most 50 tags.

**Azure side**

1. Tag the resource group and the workspace resource. `Merge` keeps existing tags:
   ```bash
   az group update -n rg-bakehouse-poc --set tags.project=bakehouse-poc tags.owner=tim tags.env=shared tags.cost-centre=personal
   az tag update --resource-id <workspace-resource-id> --operation Merge \
     --tags project=bakehouse-poc owner=tim env=shared cost-centre=personal
   ```
2. Workspace tags propagate to classic compute VMs in the managed resource group, which can take up to an hour. Existing clusters pick them up only after a restart.
3. **Azure Policy:** assign the built-in policies *Require a tag on resource groups* (`project`) and *Inherit a tag from the resource group if missing* (`project`, `owner`) at resource-group scope.
4. **Cost Management:**
   - create a monthly budget on `rg-bakehouse-poc` (for example £20) with email alerts at 50%, 80% and 100%
   - in cost analysis, group by tag `project` and by the meter for the Databricks resource
5. Serverless usage-policy tags also appear in the Azure cost analysis billing records, so serverless spend can be filtered by `project` in Azure too.

**Databricks side**

1. **Serverless usage policy:** Settings → Compute → Serverless usage policies → create `bakehouse-poc`. Add tags `project=bakehouse-poc` and `owner=tim`. Make it available to this workspace, and grant User to Tim and both SPs. With one policy each, it applies automatically to notebooks, jobs, pipelines and apps.
2. **Bundle presets:**
   ```yaml
   presets:
     tags:
       project: bakehouse-poc
       env: ${bundle.target}
       owner: tim
   ```
3. **Per-resource tags in the bundle:**
   - Jobs: `tags: {component: etl}`, plus the job's usage policy field. Confirm the field name with `databricks bundle schema`; it was formerly `budget_policy_id`.
   - SQL warehouse: `tags.custom_tags` with `component=params-delta`.
   - Lakebase project: custom tags `component=params-lakebase`. Confirm the field in the `postgres_projects` schema.
   - App: the usage policy applies, plus `component=app` if the app resource supports tags.
4. **Account budget:** Account console → Usage → Budgets → create `bakehouse-poc` filtered on tag `project=bakehouse-poc`, with an email alert.
5. **Reporting query** (in `04_backend_comparison`):
   ```sql
   SELECT usage_date, custom_tags['component'] AS component, billing_origin_product, SUM(usage_quantity) AS dbus
   FROM system.billing.usage
   WHERE custom_tags['project'] = 'bakehouse-poc'
   GROUP BY ALL ORDER BY usage_date DESC;
   ```

### 6.4 Idle shutdown defaults

Everything that bills while it runs stops after **1 minute** of inactivity by default. Where the platform's minimum is longer, the minimum is used. The default lives in one bundle variable, `idle_stop_minutes` (default `1`), and one Terraform variable of the same name.

| What runs | Setting | Default | Notes |
|---|---|---|---|
| Lakebase compute | Scale-to-zero timeout | 60 seconds | The platform minimum. The first query afterwards wakes it in a few hundred milliseconds. |
| SQL warehouse (Delta backend) | `auto_stop_mins` | 1 | 1 is allowed for serverless when set through the bundle or API. The UI minimum is 5. |
| Databricks App | Idle watchdog in the app | 1 minute | Databricks Apps have no built-in idle stop (see below). |
| Jobs (serverless) | Stop when the run ends; `timeout_seconds` per job | 900 | A job has no idle state. A 1-minute run timeout would kill normal runs, so the timeout only catches runaways. A `RUN_DURATION_SECONDS` health rule warns at 300. |
| Existing all-purpose cluster | Auto-termination | 10 minutes | Believed to be the platform minimum; check in step 0. Jobs in this project don't use it. |
| Serverless notebooks and Designer | Platform-managed idle release | Platform default | Not configurable. Detach when finished. |

**App idle watchdog**

- The app records the time of the last user-initiated callback. Polling callbacks, such as run-status checks, don't count, so an open but unattended tab doesn't keep the app alive.
- A background thread checks every 15 seconds. Once the app has been idle for `IDLE_STOP_MINUTES`, it logs the event to `ops.app_metrics` and calls the Apps API to stop itself (`apps.stop(<own name>)`).
- This needs the app's service principal to have CAN_MANAGE on its own app. The bundle grants this after the first deploy, with a one-off script that reads the app's SP client ID. Confirm it works in build phase 9.
- Fallback if self-stop isn't permitted: a scheduled GitHub Actions workflow (`stop-idle-apps.yml`, every 10 minutes; free on a public repo). It calls the app's `/idle` endpoint with the env SP's token, then stops the app with the CLI if it has been idle longer than `IDLE_STOP_MINUTES`.
- Restarting the app is manual: the Apps UI, `databricks apps start`, or `scripts/app_start.sh`. Cold start takes a minute or two.
- Locally the watchdog is off (`IDLE_STOP_MINUTES=0`).

### 6.5 Infrastructure as code (Terraform)

The resource group, the Databricks workspace (with Unity Catalog) and a cluster already exist. Terraform reads those as data sources and adds only what is missing. Everything inside the workspace that belongs to the workload stays in the bundle.

| Layer | Owned by | Contents |
|---|---|---|
| Existing | Not managed (data sources) | Subscription, resource group, Databricks workspace and its managed RG, metastore, existing cluster, GitHub repo |
| Foundation | Terraform (`infra/`) | ADLS Gen2 account with a container per target. Access connector (system-assigned managed identity) with Storage Blob Data Contributor. UC storage credential, external locations, and catalogs `bakehouse_dev/test/prod` with their managed location on that account. Databricks-managed SPs `sp-bakehouse-test/-prod`: workspace assignment, OAuth secrets, catalog grants. Serverless usage policy and account budget. Azure tags on the existing RG and workspace, policy assignments, RG budget. GitHub Environments `test`/`prod` with their secrets and the prod reviewer. |
| Workload | Bundle (`databricks.yml`) | Schemas, volumes, jobs, app, SQL warehouse, Lakebase project/catalog/roles, job and app permissions |

**Rules**

- **Reuse existing names.** The existing names (subscription, resource group, workspace, region, GitHub repo) go into `infra/terraform.tfvars` and `.env.local`. Both files are gitignored, since the repo is public.
- **State backend:** an Azure Storage container in a separate state storage account, created once by `infra/bootstrap/`, with blob versioning on. State contains SP secrets, so it is never committed and the container is private.
- **Providers:** `azurerm` (v4), `azapi` (to merge tags onto the existing RG and workspace without taking ownership of them), `databricks` (two aliases: account-level and workspace-level), and `github`.
- **Workflow:** Terraform runs locally from the Mac, never from CI. Claude Code may run `terraform fmt`, `validate` and `plan`. Tim runs `apply`.
- **If the step-0 catalog exists:** `bakehouse_dev` was created during the step-0 check. Drop it before the first apply, or `terraform import` it.

---

## 7. Local development

- **`docker compose up`** starts:
  - `app`: built from `apps/param_admin/Dockerfile` (`python:3.12-slim`, gunicorn, mounted source for reload), on port 8050
  - `postgres`: `postgres:16`, port 5432, initialised from `db/` with the same DDL and seed used on Lakebase
- **Environment:** `PARAM_BACKEND=postgres` and `DATABASE_URL=postgresql+psycopg://bakehouse:bakehouse@postgres/bakehouse`.
- **Optional:** `PARAM_BACKEND=delta` points the container at the dev warehouse using `databricks auth login` (U2M) credentials mounted read-only.
- **Tests:** `uv run pytest` uses SQLite in-memory through the same `SqlRepository`. It needs no Docker or network, so it runs identically in CI.
- **Deploy to dev:** `databricks bundle deploy -t dev` as Tim. Dev resources are prefixed `[dev tim]` and their schedules are paused.
- **Pre-commit hooks:** ruff, gitleaks, and a check that `*.designer.ipynb` was not hand-edited (warning only).

---

## 8. Checks, phases and open items

### 8.1 Step 0 checks

| Check | Result |
|---|---|
| Lakebase enabled, visible on Compute | Confirmed |
| Databricks Apps available, Dash template listed | Confirmed (GA) |
| Lakeflow Designer (New → Visual data prep) | Confirmed (GA) |
| Serverless notebook `SELECT 1` | Confirmed |
| `CREATE CATALOG bakehouse_dev` | Confirmed |
| Create SP + OAuth secret | Confirmed |
| Sample date range | 1–17 May 2024 (17 days) |
| Databricks CLI ≥ 1.0 on the Mac | 1.23 |
| App can stop itself (app SP CAN_MANAGE on own app) | To check in build phase 9 |
| Minimum auto-termination for the existing cluster | To check (believed 10 minutes) |

### 8.2 Delivery phases

The phases follow the workstreams in `docs/BUILD_PLAN.md`, the build guide for Claude Code. The order is a suggestion, not a script.

0. **Discover and configure:** existing names into gitignored config, tool checks.
1. **Repo skeleton and CI:** `pyproject`, package layout, lint, tests, gitleaks, pre-commit.
2. **Terraform foundation:** ADLS, access connector, catalogs, SPs, usage policy, budgets, Azure tags and policy, GitHub Environments.
3. **Bundle skeleton:** three targets, presets tags, `idle_stop_minutes`, schemas and volumes, deployed to dev.
4. **Simulator and reset:** virtual clock, replay of the 17 sample days, synthetic phase, scenarios, `ops_reset`.
5. **ETL:** Auto Loader to silver, gold, `run_log`, `recompute_gold` mode. Backfill 7 virtual days in dev.
6. **Parameters core:** model, DDL and seed, SQLite tests.
7. **Dash app locally:** Docker Compose with Postgres, fake job trigger.
8. **Lakebase backend:** bundle resources, app deployed to dev, Postgres role grants, ETL as-of read.
9. **Run buttons and idle watchdog:** real job triggers, app self-stop or the fallback workflow.
10. **Delta backend:** warehouse, app resources, switchable backend.
11. **Designer and notebooks:** Tim builds the two visual data preps on the canvas; Claude Code wires them into the bundle and writes the four notebooks.
12. **Test and prod:** CD workflows, then tagging, budgets and idle shutdown verified in Azure and Databricks.
13. **Comparison:** 14-day test run, `backend-comparison.md`, recommendation.

### 8.3 Open items

- Lakebase bundle resources are Beta: confirm the `postgres_projects` fields for minimum size, scale-to-zero and tags with the current CLI.
- Exact bundle field names for usage policies on jobs and apps.
- Python version of the Databricks Apps runtime vs the serverless job environment.
- Whether Designer parameters can take values from job parameters, which would let the review threshold come from the parameter store.
- When parameterised Source/Output fields reach this workspace. Until then, Designer outputs are fixed to one catalog.
- Whether to use one Lakebase project per target or one project with a branch per target. A branch per target is more elegant, but bundle support needs checking.

---

## 9. Moving to the real world

This section collects the points where a production version would differ from the PoC. Each one keeps the PoC's code and data model; what changes is mainly hosting, identity and networking.

### 9.1 Hosting the Dash app on a managed AKS platform

In many organisations a dedicated platform team already runs a secure, managed AKS platform. Hosting the app there swaps Databricks Apps for a platform the organisation already supports.

| Concern | PoC (Databricks Apps) | Managed AKS platform |
|---|---|---|
| Packaging | Source + `app.yaml` | The same Docker image as local dev, pushed to the organisation's container registry. A Helm chart or Kustomize overlay lives in `deploy/aks/`. |
| Deployment | Bundle deploys the app | The bundle still deploys the Databricks side (jobs, Lakebase, warehouse). The app image is released through the platform's route, e.g. GitOps (Flux/Argo) or its pipeline, with versions aligned by release tag. |
| App identity | Auto-created app service principal | AKS workload identity: a user-assigned managed identity with a federated credential, added to Databricks as an Entra-managed SP. It gets the same grants as the PoC app SP: a Lakebase Postgres role, warehouse CAN_USE, table Select/Modify, CAN_MANAGE_RUN on the jobs. No secrets. |
| User sign-in | Workspace SSO, forwarded user header | The platform's ingress authentication (for example an Entra-integrated gateway or OAuth2 proxy) passes the user's identity in headers. The app maps that to `updated_by` and restricts edits to an Entra group such as `param-admins`. |
| Network | Inside Databricks | Private connectivity to the workspace and Lakebase (Private Link / private endpoints and private DNS), plus egress rules on the platform firewall. Private connectivity for Lakebase needs confirming. |
| Configuration | `valueFrom` resources | ConfigMap for `PARAM_BACKEND`, hosts and IDs. Key Vault CSI driver if any secret is unavoidable. |
| Operations | Managed by Databricks | Health probes (`/healthz`), 2 replicas and a pod disruption budget, small resource requests, logs and metrics into the platform's monitoring. |
| Cost | 0.5 DBU/hour while running | A small share of a cluster already paid for. Lakebase and warehouse costs are unchanged. |

**What the PoC should do now to make this easy**

- Keep the app container-first. The same image already runs locally.
- Hide token acquisition behind a small `TokenProvider` interface in `bakehouse_core`. It would have three implementations: Databricks Apps, a local CLI login, and an Azure managed identity. Moving to AKS is then a configuration change.
- Read the user identity through one function, so the header name can differ per host.

**Questions for the platform team**

- Ingress authentication pattern, and which identity headers reach the pod.
- Workload identity support, and how managed identities are requested.
- Private DNS and routes to Databricks and Lakebase endpoints, plus egress allowlisting.
- Container registry, image scanning and the deployment model (GitOps or pipeline).
- Namespace, RBAC and resource quotas, monitoring integration, support hours and SLA.

### 9.2 Databricks Connect on an always-on cluster

If the real environment already runs an all-purpose cluster for the hours when the app is used, the app could read and write the Delta parameter tables through Databricks Connect (option C). That removes the need for both the SQL warehouse and Lakebase.

- **When it fits:** the cluster is already up for other workloads during business hours, and its cost is already accepted.
- **Trade-offs:** if the cluster is down, the app waits minutes for it to start. All-purpose compute has a higher DBU rate than jobs compute. Spark adds latency to single-row reads and writes. App traffic shares capacity with other users of the cluster.
- **Requirements:** a Unity Catalog-enabled access mode, and a cluster policy that lets the app identity attach.
- **Implementation:** a third `ParamRepository` that uses a Spark session, selected with `PARAM_BACKEND=delta-connect`. The data model is unchanged.

### 9.3 Development workflow: portal-first dev, bundles from sandbox up

This PoC is code-first: Claude Code wrote the code and the YAML up front, so even dev is deployed from the bundle. In most real projects, development starts in an IDE and the workspace, with Git in between. The bundle only takes over when work leaves dev.

| Environment | How resources get there | Who changes it |
|---|---|---|
| **dev** | Compute (jobs, apps) by hand in the portal, running code from source in a workspace Git folder. Data objects (schemas, volumes) from the data bundle. | Developers |
| **sandbox** | First bundle deployment (development mode). Integration and rough testing. | Developers, through the bundle |
| **test, prod** | Bundle deployments from CI only (production mode, run as a service principal) | CI |

**What lives in Git**

- **Reusable code reaches Git directly.** This covers packages written in an IDE, plus notebooks, Designer flows and SQL edited in a workspace Git folder.
- **Resource definitions don't.** A job, warehouse, app or permission created from the normal portal pages is stored in the Databricks service, not as a file. It reaches Git in one of three ways:
  1. **Author it in the bundle editor.** Open the Git folder in the bundle editor and add a new job definition. Edits made in the UI are written back to the YAML (Public Preview; jobs and pipelines only).
  2. **Export it** with the job's **Edit as YAML** and copy that into `resources/`. This works for any task type, including Python wheel tasks.
  3. **Capture it with `databricks bundle generate … --bind`**. This works for jobs with notebook tasks only, plus pipelines, dashboards and apps.
- **Some resources are always written as YAML by hand:** warehouses, Lakebase projects, schemas, volumes and permissions.

**Parameters**

- **Reusable definitions use variables** for anything environment-specific: `${var.catalog}`, `${var.warehouse_id}`, `${var.run_as}`.
- **In dev, the values are whatever is set on the resource in the workspace.** They never leave dev.
- **At capture, swap literals for variables.** Replacing dev's literal values with variables is the one manual step in promotion. Consistent naming keeps it small.
- **Sandbox and higher get values at deploy time:** a target's `variables` block for non-secret defaults, or `--var` / `BUNDLE_VAR_<name>` from the CI environment. Secrets never go in YAML. They live in GitHub Environment secrets or Databricks secret scopes.

**Rules**

- Once a resource is captured, the YAML is the master for sandbox and above. Later dev changes are captured again, and the variable substitutions are re-checked in the diff.
- Never point a bundle target at the same catalog or names as hand-made dev resources. Separate catalogs (`bakehouse_dev`, `bakehouse_sandbox`) avoid clashes.
- Dev jobs reference code in the Git folder, never the copies a bundle deploys under `.bundle/`.

**Two bundles (decided 2026-11-05)**

- **`dab-data/`** holds the schemas and volumes, including the `ops.artifacts` volume for the wheel. Unity Catalog objects cannot be exported from the portal into Git, so they always come from a bundle, in every environment including dev. It never uses development mode, because that would prefix schema names; environments are separated by catalog instead. It is deployed first.
- **`dab-compute/`** holds jobs, the app, Lakebase and the Designer tasks. It is first deployed at sandbox. Until then its YAML is the reference that the hand-built dev jobs are compared with.
- **Why separate:** compute can be rebuilt or destroyed without touching data. The reverse is not true: destroying the data bundle deletes every table in its schemas, including tables a job or a Designer flow created.

**Code in dev and above**

- **Dev runs from source.** Jobs are `spark_python_task`s that run `jobs/run_from_src.py` from the Git folder, with the job name first and then its parameters. Their environment lists the dependencies (for example `sqlalchemy`) because there is no wheel to declare them.
- **Sandbox and above run the wheel**, through `python_wheel_task` entry points, uploaded to the `ops.artifacts` volume by `workspace.artifact_path`. Notebooks install it with `%pip install` from the volume.
- **Capture is a conversion,** not a copy: task type, code path and dependencies all differ between dev and sandbox, as well as the variables.

---

## Sources

- [Lakeflow Designer – move to production](https://learn.microsoft.com/en-us/azure/databricks/designer/production)
- [Manage Lakebase with bundles](https://learn.microsoft.com/azure/databricks/oltp/projects/manage-with-bundles)
- [Lakebase scale to zero](https://learn.microsoft.com/en-us/azure/databricks/oltp/projects/scale-to-zero)
- [Add a Lakebase resource to a Databricks app](https://learn.microsoft.com/azure/databricks/dev-tools/databricks-apps/lakebase)
- [Databricks Apps resources](https://learn.microsoft.com/azure/databricks/dev-tools/databricks-apps/resources)
- [Bundle run identity](https://learn.microsoft.com/azure/databricks/dev-tools/bundles/run-as)
- [Serverless usage policies](https://learn.microsoft.com/azure/databricks/admin/usage/budget-policies)
- [Tags and usage attribution](https://learn.microsoft.com/en-us/azure/databricks/admin/account-settings/usage-detail-tags)
