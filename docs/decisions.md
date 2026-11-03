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
