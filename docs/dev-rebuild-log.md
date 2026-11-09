# Dev rebuild log (workstream 14)

Dev is destroyed and rebuilt by hand in the portal, from the workspace Git folder, with no bundle deployment. The goal is understanding: what the portal route costs, what has to be captured back into YAML, and what surprises it holds. The data and jobs have no value of their own.

This file records every manual step, setting and challenge, so the rebuild can be repeated. It is public: values that identify the workspace (hosts, IDs, emails) never go in it. Resource names from this project are fine.

## 1. What dev contains today (all deployed by the bundle)

| Resource | Defined in | Rebuild route | Settings to reproduce and known gotchas |
|---|---|---|---|
| Catalog `bakehouse_dev` | az-inf (Terraform) | Stays. `bundle destroy` does not remove it | Storage, credential and external location come from az-inf |
| Schemas `bronze`, `silver`, `gold`, `ops` | `dab-data/resources/schemas.yml` | The data bundle, not the portal | Comments are in the YAML. Tables created outside the bundle (Designer outputs) may block a schema delete |
| Volumes `bronze.raw`, `ops.checkpoints`, `ops.artifacts` | `dab-data/resources/schemas.yml` | The data bundle, not the portal | All managed volumes |
| The `bakehouse_core` wheel | built by the bundle artifact step | Not used by dev jobs, which run from source. Still needed for the app, probably, and from sandbox up | Never install it from a `.bundle/` path |
| Job `simulator` | `dab-compute/resources/jobs.yml` | Jobs UI | Serverless; `spark_python_task` running `jobs/run_from_src.py simulate ...` from the Git folder, environment listing `sqlalchemy`; parameters `days`, `mode`, `scenarios`, `triggered_by`; one concurrent run; timeout from `job_timeout_seconds` |
| Job `ops_reset` | `dab-compute/resources/jobs.yml` | Jobs UI | `jobs/run_from_src.py reset ...`; parameters `level`, `confirm`, `requested_by`; the confirm value must equal the target name |
| Job `bakehouse_etl` | `dab-compute/resources/etl.yml` | Jobs UI | Tasks `load_params`, `silver_build`, `gold_build` (`jobs/run_from_src.py` with `load-params`, `silver-build`, `gold-build`) and `review_explorer` (notebook task); parameters `mode`, `from_date`, `triggered_by`; duration health rule; params passed as `--catalog`, `--param-backend`, `--params-catalog` |
| Job `daily_run` | `dab-compute/resources/etl.yml` | Jobs UI | `run_job_task`s for the simulator and the ETL; parameters `days`, `scenarios`, `etl_mode`, `triggered_by`; daily schedule, paused. A parent's parameters are pushed down to child jobs by name |
| Lakebase project `bakehouse-params-dev` | `dab-data/resources/lakebase.yml` | The data bundle | Postgres 16, 0.5 to 2 CU, suspend after 60 seconds, 2 days history, tags |
| Lakebase database `bakehouse` | `dab-data/resources/lakebase.yml` | The data bundle | Branch `production`. Its owner must be an existing role |
| Unity Catalog registration `bakehouse_params_dev` | `dab-data/resources/lakebase.yml` | The data bundle (`postgres_catalogs`) | The ETL reads parameters through it with Spark SQL |
| App `bakehouse-param-admin-dev` | `dab-compute/resources/app.yml` | Apps UI | MEDIUM compute, command `gunicorn app:server`, created stopped; Postgres and job resources; env vars below; then the self-stop grant |
| App self-stop grant | `scripts/grant_app_self_manage.sh` | already a manual script | Run once after the app exists |
| Cost tags | `presets.tags` and per-resource tags | by hand on each resource | `project`, `env`, `owner`; the bundle applied these everywhere |
| Saved parameter values | Lakebase `params` schema | seeded by the app on first start | Anything changed in the app since the first deploy is lost on destroy |

App environment: `PARAM_BACKEND`, `JOB_TRIGGER=databricks`, `ETL_JOB_ID` and `DAILY_RUN_JOB_ID` (from the job resources), `LAKEBASE_ENDPOINT`, `APP_NAME`, `IDLE_STOP_MINUTES`.

## 2. Challenges expected before starting

- **Dev runs from source, so each job passes its name first to `jobs/run_from_src.py`.** The script finds `src` from its own location. `__file__` does not exist on serverless, which runs the file with `exec(compile(...))` (first run: `NameError`), so it uses `main.__code__.co_filename` instead, which keeps the real path. A test runs the file that way.

- **The app needs `bakehouse_core`, and its staged copy is gitignored.** The bundle copies the package into `apps/param_admin/` at deploy. A Git folder has no such copy. Options to try: the wheel as an app dependency, or committing the copy.
- **The Unity Catalog registration of Lakebase** had no known portal route, so Lakebase moved to the data bundle (`postgres_catalogs`), which declares it. Unproven until the first deploy from `dab-data`.
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

1. Deploy the data bundle (`dab-data/`): schemas and volumes. Lakebase joined it on 2026-11-09, so a second deploy of the data bundle creates the Lakebase project, database and Unity Catalog registration (step 4 below).
2. (Wheel: deferred. Dev jobs run from source.)
3. Jobs: `simulator`, `ops_reset`, `bakehouse_etl`, then `daily_run`. Run them with the Delta backend.
4. Lakebase project, database and Unity Catalog registration: deploy the data bundle again. Switch the ETL to Lakebase.
5. The app, with its resources and environment variables. Then the self-stop grant.
6. The `review_explorer` task.
7. Seed with `ops_reset` and `daily_run`, and check the gold tables, the app and `review_explorer`.

### Done

- **Destroy (2026-11-05).** `databricks bundle destroy -t dev -p personal` reported 15 deleted: four jobs, the app, three Lakebase resources, four schemas and three volumes. Tim checked the portal afterwards: only `information_schema` was left under `bakehouse_dev`, and the old jobs were gone.
- **Data bundle deployed (2026-11-05).** From `dab-data/`: `validate`, `plan` and `deploy` with `-t dev -p personal`. Four schemas and three volumes were created. Tim checked Catalog Explorer and it looked right.
- **`simulator_dev` built and run (2026-11-06).** Serverless job, one `spark_python_task` `simulate` running `jobs/run_from_src.py` from the Git folder. Task parameters are a JSON array of strings (the portal wants that shape). Job parameters `days`=`1`, `mode`=`auto`, `scenarios`=empty, `triggered_by`=`manual`, as in `dab-compute/resources/jobs.yml`. The environment lists `sqlalchemy`. The run timeout is under Job health configuration / Metric thresholds, not in the main settings. First run released 1 day up to 2024-05-01 (207 transactions, 301 customers, 48 franchises, 27 suppliers, 5 reviews).
- **`ops_reset_dev` built and run (2026-11-06).** Same shape as the simulator: serverless, one `reset` task running `jobs/run_from_src.py`, JSON-array task parameters, the `sqlalchemy` environment. Job parameters `level`=`data`, `confirm`=empty, `requested_by`=`manual`; the run used `confirm`=`dev`. Tim ran it and it worked, then re-ran `simulator_dev` to put day 1 back.
- **`bakehouse_etl_dev` built and run (2026-11-06).** Tasks `load_params`, `silver_build` and `gold_build` run `jobs/run_from_src.py` with the Delta parameter backend (`--param-backend delta`). With `delta`, `--params-catalog` is ignored, so it is left off. All tasks use the `sqlalchemy` environment. The first full run failed in `silver_build` (see the `bakehouse_core` pickling entry in section 5). After the fix, the ETL on its own ran to completion.
- **`daily_run_dev` built and run (2026-11-06).** Two `run_job_task`s, the simulator then the ETL, with the parent's parameters pushed down by name. The failed attempts still released their virtual days, so the passing run was day 4 (2024-05-04). `ops.run_log` had a row per task, all `succeeded`, with the simulator logging its own run ID and the ETL tasks sharing another. Gold held 4 days (192 rows in `franchise_daily_kpis`, 48 franchises times 4). Timing on serverless: `silver_build` about 2 minutes, `gold_build` about 1 minute, `simulator` seconds. All four jobs now work with the Delta backend.

## 5. Challenges met

Each entry: what happened, the cause, the fix, and what to do next time.

- **`bundle destroy` needs an interactive terminal.** Run through Claude Code's `!` prefix it printed "To proceed, use --auto-approve" and deleted nothing, because there is no prompt to answer. Fix: run it in a normal terminal, where it shows the list and asks. Use `bundle summary` first to review what it will remove, and avoid `--auto-approve` for anything you have not reviewed.
- **`destroy` deleted the schemas together with tables the bundle did not manage.** `gold` held two Designer output tables created outside the bundle, and it still went cleanly with them. Anything a bundle-managed schema holds is lost on destroy, managed or not.
- **Portal lists lag.** After the destroy the Jobs list kept showing the old jobs for a while, and the Jobs list has no refresh button. They disappeared after a delay. Do not read a stale list as a failed destroy. Catalog Explorer does have a refresh button, and it cleared the stale `bronze`, `silver` and `gold` entries. `databricks schemas list <catalog>` shows the real state.
- **Leftover `.bundle` folders.** After the destroy, the workspace still showed `.bundle/bakehouse` and `.bundle/bakehouse-data` with `dabs-runner-*.py` files in them, although `bakehouse-data` had never been deployed. Cause not established. Tim deleted both folders by hand. Harmless, but check `.bundle` after a destroy.
- **`__file__` is not defined on serverless.** The first `simulator_dev` run failed with `NameError`, because serverless runs the file through `exec(compile(...))`. Fix: `run_from_src.py` reads the path from `main.__code__.co_filename`, and a test runs it that way. Reasons and alternatives are in `docs/decisions.md`. Next time: do not assume `__file__` in anything a job runs as a script.
- **A task with no environment has no `sqlalchemy`.** `daily_run_dev` failed with `ModuleNotFoundError: No module named 'sqlalchemy'`. Each job has an Environment (version plus dependencies) and each task has to select it, otherwise it gets the plain default. Fix: an environment listing `sqlalchemy` (`jobs/base_environment.yml`), attached to every task. The portal list is a second copy of the dependencies in `pyproject.toml`, so keep them in step.
- **`silver_build` failed at `.start()` with `No module named 'bakehouse_core'` (STREAMING_PYTHON_RUNNER_INITIALIZATION_FAILURE).** `foreachBatch` pickles its handler and a separate worker process loads it. The pickle only said "import `bakehouse_core`", and `run_from_src.py` put `src` on `sys.path` in the driver only. The other tasks never ship package objects to another process, so they passed. Fix: `run_from_src.py` registers `bakehouse_core` with cloudpickle to be pickled by value (`pickle_package_by_value`), so edits in the Git folder still run with no wheel. A test unpickles a handler in a fresh process without `src`, and fails when the registration is removed. Proven on Databricks: `bakehouse_etl_dev` then ran to completion. Sandbox and above install the wheel everywhere and do not need this. Next time: any Python callback Spark runs elsewhere (`foreachBatch`, UDFs) needs the package in that process.
- **A job with no parameters set fails on the first run.** The task had no parameter values to pass (the exact error was not recorded). Set the job parameters and their defaults before the first run.
