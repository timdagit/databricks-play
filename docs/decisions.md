# Decisions

Departures from `SPEC.md` and `CLAUDE_CODE_GUIDE.md`, and choices made along the way. Newest last.

## 2026-11-02: alignment with the repo's CLAUDE.md

- **One large PoC, one bundle.** The root `CLAUDE.md` was written for many small PoCs. It now describes one bundle named `bakehouse` with the spec's layout.
- **`CLAUDE.md` wins over the guide.** The docs were written outside this repo, so conflicts are resolved in `CLAUDE.md`.
- **Targets.** `dev` is the default and the focus. `test` and `prod` stay defined but minimal until needed.
- **Tim deploys and pushes.** Claude doesn't run `git push`, `bundle deploy`/`run`/`destroy` or `terraform apply`/`destroy`/`import`. It supplies the commands.
- **Infrastructure lives in az-inf, not `infra/`.** This repo has no Terraform. az-inf already provides the workspace, ADLS storage, access connector, Unity Catalog storage credential and external location, and a `learning` catalog. Needed changes are written up in `docs/az-inf-requirements.md`. SPEC §5.1 and §6.5 and BUILD_PLAN workstream 2 still describe `infra/` and need updating.
- **Catalog.** `bakehouse_dev` gets its own container on the existing az-inf ADLS account. The `learning` catalog is left alone and there is no separate resource-group budget. Details in `docs/az-inf-requirements.md`.
- **Python versions (docs checked Oct 2026).** Serverless environment version 5 uses Python 3.12.3. Databricks Apps that use pip use Python 3.11, and only uv-based apps can choose a version. Decision: because the app uses uv, the app can match the jobs. Use Python 3.12 everywhere (`.python-version`, `requires-python`, Docker `python:3.12-slim` as in SPEC §7). The binding constraint is the serverless runtime, not Apps. uv downloads the interpreter, so no brew install is needed. The app directory must not contain a `requirements.txt`.
- **GitHub Environments and secrets** are set up by hand or with `gh`, because az-inf has no GitHub provider.
- **Python tooling.** uv, as in SPEC §5.2, so the app isn't tied to Python 3.11. This overrides the global venv + `requirements.txt` rule.
- **Designer notebooks.** `.gitignore` ignores `*.ipynb` except `*.designer.ipynb`. To be reviewed once the first Designer file is committed.
- **CI.** Deploy-on-merge workflows use a `paths` filter.

## 2026-11-03: orientation findings

- **Tools:** uv 0.13.6, Databricks CLI 1.23.0, gitleaks 8.30.1, Docker 29.7.2. `gh` and `az` are blocked in the Claude sandbox, so Tim runs those.
- **No `bakehouse_dev` catalog exists.** The spec's step-0 catalog is gone, so nothing needs dropping before the az-inf apply.
- **No all-purpose cluster exists.** The spec assumed one. Nothing to note about auto-termination, and the guardrail about not using it is moot. az-inf only defines cluster policies.
- **Resource group `az_databricks`** is in northeurope and has only a `created_by` tag. The spec's project and owner tags are not applied yet (deferred, see `az-inf-requirements.md`).
- **No gitignored config yet.** Locally the `personal` profile supplies the host. CI will use GitHub Environment secrets. Add `.env.local` only when something needs it.
- **Git hooks.** Local commits run only the global gitleaks hook (`core.hooksPath` in the user config points to `~/.git-hooks`). No pre-commit framework: ruff runs in CI and by hand (`uv run ruff check .`, `uv run ruff format .`).

## 2026-11-03: bundle schema findings (CLI 1.23.0)

- **Usage policy fields.** Jobs and apps have both `budget_policy_id` and `usage_policy_id`. Lakebase projects (`postgres_projects`) only have `budget_policy_id`. Check which the workspace accepts when we get to tagging.
- **Lakebase.** `postgres_projects` has `custom_tags`, `default_endpoint_settings`, `history_retention_duration`, `pg_version` and `purge_on_delete`. `postgres_catalogs`, `postgres_roles`, `postgres_branches` and `postgres_endpoints` also exist.
- **Presets.** `name_prefix`, `tags`, `trigger_pause_status` and `jobs_max_concurrent_runs` exist, so schedules can be paused and tags set at bundle level.
- **Workspace.** `workspace.artifact_path` exists, so the wheel can be uploaded to a volume path (to verify when deploying).
- **Warehouse.** `sql_warehouses` has `auto_stop_mins` and `tags`, so the 1-minute idle default is expressible.

## 2026-11-03: dev target without development mode

- **Schema names.** `mode: development` prefixes schemas with `dev_<user>_`, giving `bakehouse_dev.dev_<user>_silver` instead of the spec's `bakehouse_dev.silver`. An empty `name_prefix` is ignored, so the `dev` target drops `mode: development`. It sets `presets.trigger_pause_status: PAUSED` by hand to keep schedules paused.
- **Check when adding resources.** Apps and jobs are no longer auto-prefixed either, so give them explicit names. Other effects of development mode (such as concurrent run limits) are now off unless set.
- **Volumes** are not prefixed in either case.

## 2026-11-03: first jobs on Databricks

- **Compute.** The `simulator` and `ops_reset` jobs run on serverless jobs compute: an `environments` entry (`environment_version: "5"`, the wheel as the dependency) and `environment_key` on the task, with no cluster defined. A run takes about 80 seconds, mostly startup.
- **Storage permission.** The first run failed with `Request for user delegation key is not authorized`. The access connector needed the `Storage Blob Delegator` role at storage account scope, because its `Storage Blob Data Contributor` role is scoped to single containers. Fixed in az-inf, written up in `docs/az-inf-requirements.md`.
- **Proven on Databricks:** wheel install and entry points, loading `samples.bakehouse`, writing JSON files to the `raw` volume with plain file paths, creating the `ops` Delta tables, a multi-day backfill, the `duplicates` scenario (14 repeated IDs on 207 rows) and `ops_reset` with its `confirm` guard.
- **Not yet proven:** the other four scenarios on Databricks (covered by unit tests only), and the `reset_log` and `run_log` rows. Checking them needs a SQL warehouse or a notebook.
- **Deferred:** `rebuild=true` on reset, and parameter reseeding on a hard reset (needs the Lakebase and Delta backends).

## 2026-11-03: ETL design (workstream 5)

- **Parameters between tasks.** Spec 3.3 says `load_params` passes the values on as task values. Instead it writes them, with their versions, into its `ops.run_log` row under the job run id, and `silver_build` and `gold_build` read that row. This avoids depending on the `dbutils` task-values API, and the lineage record is the same data the tasks use.
- **Parameter source.** The ETL reads Delta directly through a minimal `DeltaRepository` (`ops.param_definitions`, `ops.param_versions`), created and seeded by `load_params` on first use. Delta has no primary keys, so two simultaneous saves could pick the same version number. The app is the only writer, so this is accepted. **Open for workstream 8:** with the Lakebase backend, `load_params` should read the Unity Catalog-registered Lakebase catalog, chosen by a bundle variable.
- **Bronze is read as strings.** Auto Loader runs with `inferColumnTypes=false` and `schemaEvolutionMode=rescue`. Silver casts with `try_cast`, so a value that does not fit becomes null and the row is quarantined rather than failing the job.
- **Quarantine rules.** Rows missing a required value, or with a non-positive quantity, go to `transactions_quarantine` or `reviews_quarantine` with a `_dq_reason`. If the null rate of `paymentMethod` in a batch is above `dq_max_null_pct`, the null rows are quarantined too. Below the limit they are kept.
- **SCD2** for customers and franchises is staged by `stage_scd2` and applied with one MERGE per virtual day, oldest first, so a backfill gives the same history as day-by-day runs.
- **Gold is one snapshot per virtual day.** Each table has `as_of_date`. Days are the release day (`_virtual_date`), not the transaction timestamp, so a late file counts on the day it arrives and `incremental` never revisits earlier days. `recompute_gold` rebuilds every day with that day's own parameters. A day's rows are replaced atomically. `incremental` starts after the last successful `gold_build` in `run_log`.
- **Gold rules (not in the spec).**
  - Flags compare the last 3 days' average daily revenue with all earlier days.
  - Loyalty tiers use spend over the lookback window.
  - Supplier concentration is the share of 7-day revenue sold through each supplier's franchises, using the SCD2 row in force that day.
  - Review alerts count negative reviews in 7 days. A review is negative if it matches a short keyword list, which suits the simulator's burst text but misses differently worded real reviews.
  - Targets are the average daily revenue of all earlier days, plus the uplift.
- **Jobs.** `bakehouse_etl` chains `load_params`, `silver_build` and `gold_build`. `daily_run` runs `simulator` then `bakehouse_etl` as run-job tasks, with a schedule that is paused. Both have one concurrent run, the shared timeout and a duration health warning (`job_duration_warning_seconds`).
- **Parameter names in `daily_run` (found on the first run).** Job parameters are pushed down by name to the jobs a Run Job task starts, and they override the child's own value. `daily_run` first had a `mode` parameter for the ETL (`incremental`), which overrode the simulator's `mode` (`auto`, `replay`, `synthetic`) and made the simulator exit with an argument error. It is now `etl_mode`. Keep parameter names in a parent job distinct from the children's.

## 2026-11-03: supplier concentration, after the first ETL run

- **Finding.** In `samples.bakehouse` the 48 franchises have 48 different `supplierID`s, and the suppliers table has 27 rows with 27 different ingredients. Only 27 of the franchises' supplier ids appear in it. So there is one supplier per franchise, 21 franchises point at a supplier that is not in the suppliers table, and grouping by ingredient gives the same split as grouping by supplier.
- **Change.** Keep the per-supplier measure from the spec. `supplier_concentration` now also carries `ingredient`, `continent` and `supplier_known`, and keeps unknown suppliers with `supplier_known = false` so the gap in the source shows up.
- **Calibrated default.** `supplier_concentration_threshold` defaults to 0.10, the lowest value allowed, instead of 0.60. The largest supplier holds about 14% of 7-day revenue, so 0.60 could never trigger. The spec calls the defaults first guesses to calibrate from the data. Parameter values already stored keep their old value (version 1 is 0.60) until changed in the app or reseeded by a hard reset.

## 2026-11-03: ETL proven on Databricks (days 1 to 5)

- **Proven:** the `daily_run` chain (simulate, then `load_params`, `silver_build`, `gold_build`) on serverless. A 3-day run takes about 5½ minutes, and a 5-day `recompute_gold` about 4 minutes, most of it the per-day gold queries. Auto Loader with `availableNow`, `foreachBatch`, the insert-new, upsert and quarantine writes, per-day replacement of gold rows, and parameter versions per day all work.
- **Scenarios proven end to end:** `null_spike` (10.9% nulls against the 2% limit quarantined 43 of 396 rows with reason `null_rate_exceeded:paymentMethod`) and `duplicates` (22 repeated ids collapsed). Silver matched the released rows exactly after both.
- **Serverless gotcha:** `foreachBatch` runs in a separate Python process, so values set in the handler never reach the main program. `silver_build` now writes each batch's counts to files under the checkpoints volume (`_stats/<run id>`) and reads them back.
- **Not yet exercised on real data:** SCD2 with an actual change (days 1 to 5 released no changed customers or franchises), `late_files`, `underperforming_franchise` and `negative_review_burst`. The synthetic phase starts on virtual day 18.
- **Note:** gold logs a Spark warning about a window with no partition, from the supplier share total. The data is tiny, so it is left as is.

## 2026-11-03: franchise flags, after days 1 to 19

- **Finding.** With flags comparing a franchise's last 3 days with all earlier days, 36 of 48 franchises were flagged on at least one day, and 25 on day 19. The network median change was -26%, because days 18 and 19 are a weekend, and a franchise sells only 4 to 5 transactions a day, so a 3-day average swings by 30% or more by chance.
- **Change.** The recent window is now 7 days (a full weekly cycle), and the flag compares a franchise's change with the network's change over the same windows (`relative_change_pct`). `underperform_pct` now means how much worse than the network. `franchise_flags` gains `network_change_pct` and `relative_change_pct`.
- **Gold tables evolve.** `GoldTables.ensure_tables` adds columns that a table lacks, so adding a column no longer needs a manual drop. New columns are null on old rows until `recompute_gold` runs. Removing or renaming a column still needs a deliberate drop and rebuild, and the job says so.
- **Calibration result (day 19).** The network's 7-day change was -12%. Flagged franchises at `underperform_pct` 0.20, 0.30, 0.40 and 0.50: 3, 2, 1 and 0 (down from 25 with the old rule). The default stays 0.20, which is what is stored. Raising it in the app and rerunning the ETL narrows the flags, which makes a good parameter demo.
- Also removed the no-partition window from the supplier shares by computing the total once.

## 2026-11-03: the parameter table component

- **Finding.** Dash 4.5 marks `dash_table.DataTable` as deprecated: it still works, will leave the built-in components in a future major version, and `dash-ag-grid` is the suggested replacement.
- **Decision.** Keep `DataTable` for now and cap Dash at `<5` in the `app` dependency group, so an upgrade cannot remove it by accident. The handlers (`bakehouse_core.webapp.Handlers`) work on plain rows, so switching to `dash-ag-grid` later only touches the layout and callback wiring in `create_app`. Revisit if Dash 5 is wanted, or once the app has been proven on Databricks (workstream 8).

## 2026-11-03: local app setup

- **Schema and seed from code, not `db/`.** Spec 5.1 and 7 have Postgres initialised from SQL files in `db/`. The tables and the version 1 defaults are already defined once in code (`params.schema`, `params.model`) and used by the tests, so the app calls `ensure_schema()` on start and no second copy of the DDL exists. A hard reset reseeds from the same code.
- **Ports.** The compose file publishes Postgres on host port 5433 and the app on 8050, both on localhost only and both set in `.env.local`, so a Postgres already running on 5432 is not disturbed. Compose reads that file through `--env-file`, because it only reads `.env` by default.
- **One gunicorn worker with threads.** The idle tracker behind `/idle` lives in the process, so every request must reach the same one.

## 2026-11-03: Lakebase and the app on Databricks, research findings (workstream 8)

- **Bundle resources (CLI 1.23).** `postgres_projects` has `default_endpoint_settings` (`autoscaling_limit_min_cu`, `autoscaling_limit_max_cu`, `suspend_timeout_duration`), `custom_tags` (a list of key and value), `pg_version`, `history_retention_duration` (2 to 35 days) and `purge_on_delete`. The minimum compute is 0.5 CU. `postgres_catalogs` takes `catalog_id`, `branch`, `postgres_database` and `create_database_if_missing`. A database can be registered with only one catalog.
- **App resources.** The smallest app compute size is `MEDIUM`. An app's `postgres` resource has `branch`, `database` and `permission: CAN_CONNECT_AND_CREATE`. The `job` resource supports `CAN_MANAGE_RUN`. The app's command and environment variables can be set in the bundle (`config`).
- **Lakebase login from an app.** The platform injects `PGHOST`, `PGDATABASE`, `PGUSER`, `PGPORT` and `PGSSLMODE`. The password is an OAuth token, valid for an hour, from the SDK's `generate_database_credential`. The database needs the `databricks_auth` extension, and the Postgres user is the app's service principal. Not yet verified: the exact SDK call, and whether the app resource creates the role by itself.
- **Getting `bakehouse_core` into the app.** The docs only show a Unity Catalog volume wheel for `requirements.txt`, which makes the app use pip and ignore `uv.lock`. Plan: copy `src/bakehouse_core` into the app folder at build time (generated, gitignored, added to the sync with `sync.include`). Fallback: a wheel in a volume with a generated `requirements.txt`.
- **No built-in idle stop for apps.** A community answer says Databricks Apps has none. The Stop App API exists. Whether an app's own service principal may stop itself is untested, so workstream 9 keeps the GitHub workflow fallback from the spec.
- **Order of work.** (1) the Lakebase project and catalog in the bundle, (2) the Lakebase token provider and the catalog read for `load_params`, (3) the app resource, (4) the Postgres grants script and a first end-to-end test.

## 2026-11-03: first start of the app on Databricks

- **Proven:** the staged copy of `bakehouse_core` is uploaded with the app, `uv` installs from the app's own lock file, the platform's port (`DATABRICKS_APP_PORT`, 8000) is picked up by `gunicorn.conf.py`, and the app's Postgres role is created automatically (`dbrx-apps-<service principal id>`, Lakebase OAuth login). The Lakebase token provider logs in successfully.
- **Fixed on the way:** the SDK's `expire_time` is a protobuf `Timestamp`, not a `datetime`. And the runtime picked CPython 3.15 because the app's `requires-python` had no upper bound, so it is `>=3.12,<3.13` now.
- **`public` is closed to the app.** `CAN_CONNECT_AND_CREATE` does not let the app create tables in `public` (`permission denied for schema public`). The tables now go in a `params` schema (spec 4.5) that the app creates and owns: `ensure_schema` runs `CREATE SCHEMA IF NOT EXISTS params` on Postgres, and the Postgres and Lakebase connections set `search_path=params`. SQLite is unchanged. The Lakebase catalog exposes them as `<params catalog>.params.*`, which is where the ETL reads. If the platform refuses to let the app create schemas, the fallback is one `CREATE SCHEMA params AUTHORIZATION "<app role>"` run as the database owner.

## 2026-11-03: effective-from in simulated time

- **Finding.** A save with a blank effective-from date used the real date (2026-11-03). The ETL reads parameters as of the virtual date (May 2024 and on), so such a change was stored but never in force. The app cannot see the virtual date, because the clock is in Delta.
- **Change.** In the app, a blank date means "from the start" (the seed date, 1900-01-01), shown as `from the start`. An explicit date still limits the change to days from then on. `incremental` applies the new values to new days, and `recompute_gold` rebuilds every day with them. The repository's own default (today) is unchanged for other callers.

## 2026-11-03: the app on Databricks works end to end

- **Proven:** the app starts on Databricks Apps, creates the `params` schema and the seed in Lakebase, and saves versions there. The signed-in user's email arrives in `X-Forwarded-Email` and is recorded as `updated_by`. Spark reads the same table through the Unity Catalog registration (`<params catalog>.params.param_versions`).
- **Note:** decimal values are stored without trailing zeros (`0.3`), which parses back to the same number.

## 2026-11-03: the ETL reads parameters from Lakebase (proven)

- **Proven end to end:** `underperform_pct` saved as 0.40 in the app (version 3, from the start) went to Lakebase. `load_params` read it through the Unity Catalog registration and logged it (value `0.4`, version 3). `recompute_gold` then flagged 1 franchise on day 19 instead of 3 at 0.20.
- **Default.** `param_backend` now defaults to `lakebase`, because the app writes there and a plain deploy must not point the ETL back at Delta. Workstream 10 adds the Delta backend to the app for the comparison, and switching is one variable.
