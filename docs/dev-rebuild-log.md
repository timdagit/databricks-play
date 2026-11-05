# Dev rebuild log (workstream 14)

Dev is destroyed and rebuilt by hand in the portal, from the workspace Git folder, with no bundle deployment. The goal is understanding: what the portal route costs, what has to be captured back into YAML, and what surprises it holds. The data and jobs have no value of their own.

This file records every manual step, setting and challenge, so the rebuild can be repeated. It is public: values that identify the workspace (hosts, IDs, emails) never go in it. Resource names from this project are fine.

## 1. What dev contains today (all deployed by the bundle)

| Resource | Defined in | Rebuild route | Settings to reproduce and known gotchas |
|---|---|---|---|
| Catalog `bakehouse_dev` | az-inf (Terraform) | Stays. `bundle destroy` does not remove it | Storage, credential and external location come from az-inf |
| Schemas `bronze`, `silver`, `gold`, `ops` | `resources/schemas.yml` | Catalog Explorer or SQL | Comments are in the YAML. Tables created outside the bundle (Designer outputs) may block a schema delete |
| Volumes `bronze.raw`, `ops.checkpoints`, `ops.artifacts` | `resources/schemas.yml` | Catalog Explorer or SQL | All managed volumes |
| The `bakehouse_core` wheel | built by the bundle artifact step | `uv build --wheel`, then upload to `ops.artifacts` by hand | Jobs must install it from the volume path, never a `.bundle/` path |
| Job `simulator` | `resources/jobs.yml` | Jobs UI | Serverless, environment version 5 plus the wheel; wheel task `bakehouse-simulate`; parameters `days`, `mode`, `scenarios`, `triggered_by`; one concurrent run; timeout from `job_timeout_seconds` |
| Job `ops_reset` | `resources/jobs.yml` | Jobs UI | Entry point `bakehouse-reset`; parameters `level`, `confirm`, `requested_by`; the confirm value must equal the target name |
| Job `bakehouse_etl` | `resources/etl.yml` | Jobs UI | Tasks `load_params`, `silver_build`, `gold_build` (wheel tasks) and `review_explorer` (notebook task); parameters `mode`, `from_date`, `triggered_by`; duration health rule; params passed as `--catalog`, `--param-backend`, `--params-catalog` |
| Job `daily_run` | `resources/etl.yml` | Jobs UI | `run_job_task`s for the simulator and the ETL; parameters `days`, `scenarios`, `etl_mode`, `triggered_by`; daily schedule, paused. A parent's parameters are pushed down to child jobs by name |
| Lakebase project `bakehouse-params-dev` | `resources/lakebase.yml` | Lakebase UI | Postgres 16, 0.5 to 2 CU, suspend after 60 seconds, 2 days history, tags |
| Lakebase database `bakehouse` | `resources/lakebase.yml` | Lakebase UI or SQL | Branch `production`. Its owner must be an existing role |
| Unity Catalog registration `bakehouse_params_dev` | `resources/lakebase.yml` | to find out | The ETL reads parameters through it with Spark SQL |
| App `bakehouse-param-admin-dev` | `resources/app.yml` | Apps UI | MEDIUM compute, command `gunicorn app:server`, created stopped; Postgres and job resources; env vars below; then the self-stop grant |
| App self-stop grant | `scripts/grant_app_self_manage.sh` | already a manual script | Run once after the app exists |
| Cost tags | `presets.tags` and per-resource tags | by hand on each resource | `project`, `env`, `owner`; the bundle applied these everywhere |
| Saved parameter values | Lakebase `params` schema | seeded by the app on first start | Anything changed in the app since the first deploy is lost on destroy |

App environment: `PARAM_BACKEND`, `JOB_TRIGGER=databricks`, `ETL_JOB_ID` and `DAILY_RUN_JOB_ID` (from the job resources), `LAKEBASE_ENDPOINT`, `APP_NAME`, `IDLE_STOP_MINUTES`.

## 2. Challenges expected before starting

- **The app needs `bakehouse_core`, and its staged copy is gitignored.** The bundle copies the package into `apps/param_admin/` at deploy. A Git folder has no such copy. Options to try: the wheel as an app dependency, or committing the copy.
- **The Unity Catalog registration of Lakebase** has no known portal route yet.
- **Tags are not applied by presets.** Each hand-built resource needs them set, or billing attribution is lost.
- **Job and app IDs** that the bundle wired through `value_from` and `${resources...}` become manual settings.
- **Destroy may not remove everything,** for example schemas holding tables the bundle does not manage.

## 3. Before destroy

- [x] Everything committed and pushed, from the Mac and from the workspace Git folder, including `lakeflow/review_explorer.designer.ipynb`
- [x] `databricks bundle plan -t dev -p personal` shows no drift, or the drift is written down below
- [x] Parameter values saved in the app are noted below
- [ ] Manual post-deploy steps noted below
- [x] Deletion list from `databricks bundle destroy -t dev -p personal` reviewed together before confirming

Drift found: none. `bundle plan` showed zero changes (2026-11-05).

Saved parameter values (name and value only): none worth keeping. They were for demonstration, so the rebuild starts from the seeded defaults.

Manual post-deploy steps done so far:

- App self-stop grant (`scripts/grant_app_self_manage.sh`)

## 4. Rebuild steps

Filled in as each step is done, with the setting used and anything unexpected.

### Planned order

Each step is checked before the next. The ETL is tested first with the Delta parameter backend (`--param-backend delta`), so the jobs can be proven before Lakebase exists.

1. Schemas and volumes (portal or SQL).
2. Build the wheel and upload it to `ops.artifacts`.
3. Jobs: `simulator`, `ops_reset`, `bakehouse_etl`, then `daily_run`. Run them with the Delta backend.
4. Lakebase project, database and Unity Catalog registration. Switch the ETL to Lakebase.
5. The app, with its resources and environment variables. Then the self-stop grant.
6. The `review_explorer` task.
7. Seed with `ops_reset` and `daily_run`, and check the gold tables, the app and `review_explorer`.

### Done

- **Destroy (2026-11-05).** `databricks bundle destroy -t dev -p personal` reported 15 deleted: four jobs, the app, three Lakebase resources, four schemas and three volumes. Tim checks the portal for anything left over.

## 5. Challenges met

Each entry: what happened, the cause, the fix, and what to do next time.

- **`bundle destroy` needs an interactive terminal.** Run through Claude Code's `!` prefix it printed "To proceed, use --auto-approve" and deleted nothing, because there is no prompt to answer. Fix: run it in a normal terminal, where it shows the list and asks. Use `bundle summary` first to review what it will remove, and avoid `--auto-approve` for anything you have not reviewed.
