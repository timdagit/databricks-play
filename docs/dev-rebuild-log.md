# Dev rebuild log (workstream 14)

Dev is destroyed and rebuilt by hand in the portal, from the workspace Git folder, with no bundle deployment. The goal is understanding: what the portal route costs, what has to be captured back into YAML, and what surprises it holds. The data and jobs have no value of their own.

This file records every manual step, setting and challenge, so the rebuild can be repeated. It is public: values that identify the workspace (hosts, IDs, emails) never go in it. Resource names from this project are fine.

## 1. What dev contains today (all deployed by the bundle)

| Resource | Defined in | Rebuild route | Settings to reproduce and known gotchas |
|---|---|---|---|
| Catalog `bakehouse_dev` | az-inf (Terraform) | Stays. `bundle destroy` does not remove it | Storage, credential and external location come from az-inf |
| Schemas `bronze`, `silver`, `gold`, `ops` | `dab-data/resources/schemas.yml` | The data bundle, not the portal | Comments are in the YAML. Tables created outside the bundle (Designer outputs) may block a schema delete |
| Volumes `bronze.raw`, `ops.checkpoints`, `ops.artifacts` | `dab-data/resources/schemas.yml` | The data bundle, not the portal | All managed volumes |
| The `bakehouse_core` wheel | built by the bundle artifact step | Not used in dev: the jobs, the app and the notebooks all run from source. Needed from sandbox up | Never install it from a `.bundle/` path |
| Job `simulator` | `dab-compute/resources/jobs.yml` | Jobs UI | Serverless; `spark_python_task` running `jobs/run_from_src.py simulate ...` from the Git folder, environment listing `sqlalchemy`; parameters `days`, `mode`, `scenarios`, `triggered_by`; one concurrent run; timeout from `job_timeout_seconds` |
| Job `ops_reset` | `dab-compute/resources/jobs.yml` | Jobs UI | `jobs/run_from_src.py reset ...`; parameters `level`, `confirm`, `requested_by`; the confirm value must equal the target name |
| Job `bakehouse_etl` | `dab-compute/resources/etl.yml` | Jobs UI | Tasks `load_params`, `silver_build`, `gold_build` (`jobs/run_from_src.py` with `load-params`, `silver-build`, `gold-build`) and `review_explorer` (notebook task); parameters `mode`, `from_date`, `triggered_by`; duration health rule; params passed as `--catalog`, `--param-backend`, `--params-catalog` (on `load_params` and `gold_build`: `--param-backend lakebase --params-catalog bakehouse_params_dev`) |
| Job `daily_run` | `dab-compute/resources/etl.yml` | Jobs UI | `run_job_task`s for the simulator and the ETL; parameters `days`, `scenarios`, `etl_mode`, `triggered_by`; daily schedule, paused. A parent's parameters are pushed down to child jobs by name |
| Lakebase project `bakehouse-params-dev` | `dab-data/resources/lakebase.yml` | The data bundle | Postgres 16, 0.5 to 2 CU, suspend after 60 seconds, 2 days history, tags |
| Lakebase database `bakehouse` | `dab-data/resources/lakebase.yml` | The data bundle | Branch `production`. Its owner must be an existing role |
| Unity Catalog registration `bakehouse_params_dev` | `dab-data/resources/lakebase.yml` | The data bundle (`postgres_catalogs`) | The ETL reads parameters through it with Spark SQL |
| App `bakehouse-param-admin-dev` | `dab-compute/resources/app.yml` | Apps UI, then the Deploy button with the Git folder's `apps/param_admin` as the source | MEDIUM compute, created stopped; Postgres and job resources named `lakebase`, `etl-job` and `daily-run-job`, as in `app.yml`; command and env vars come from `apps/param_admin/app.yaml`; then the self-stop grant |
| App self-stop grant | `scripts/grant_app_self_manage.sh` | already a manual script | Run once after the app exists |
| Cost tags | `presets.tags` and per-resource tags | by hand on each resource | `project`, `env`, `owner`; the bundle applied these everywhere |
| Saved parameter values | Lakebase `params` schema | seeded by the app on first start | Anything changed in the app since the first deploy is lost on destroy |

App environment (now in `apps/param_admin/app.yaml`, because the portal has no field for the command or env vars): `PARAM_BACKEND`, `JOB_TRIGGER=databricks`, `ETL_JOB_ID` and `DAILY_RUN_JOB_ID` (from the job resources), `LAKEBASE_ENDPOINT`, `IDLE_STOP_MINUTES`.

## 2. Challenges expected before starting

- **Dev runs from source, so each job passes its name first to `jobs/run_from_src.py`.** The script finds `src` from its own location. `__file__` does not exist on serverless, which runs the file with `exec(compile(...))` (first run: `NameError`), so it uses `main.__code__.co_filename` instead, which keeps the real path. A test runs the file that way.

- **The app needs `bakehouse_core`, and its staged copy was gitignored.** The bundle copies the package into `apps/param_admin/` at deploy. A Git folder has no such copy. Resolved: the copy is committed, and a test keeps it current (section 5).
- **The Unity Catalog registration of Lakebase** had no known portal route, so Lakebase moved to the data bundle (`postgres_catalogs`), which declares it. Resolved: it deployed first time, and the ETL read parameters through it.
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

- App self-stop grant (`scripts/grant_app_self_manage.sh`), run again after the dev rebuild (2026-11-09)

## 4. Rebuild steps

Filled in as each step is done, with the setting used and anything unexpected.

### Planned order

Each step is checked before the next. The ETL is tested first with the Delta parameter backend (`--param-backend delta`), so the jobs can be proven before Lakebase exists.

1. Deploy the data bundle (`dab-data/`): schemas and volumes. Lakebase joined it on 2026-11-09, so a second deploy of the data bundle creates the Lakebase project, database and Unity Catalog registration (step 4 below). **Done.**
2. (Wheel: deferred. Dev jobs run from source.)
3. Jobs: `simulator`, `ops_reset`, `bakehouse_etl`, then `daily_run`. Run them with the Delta backend. **Done.**
4. Lakebase project, database and Unity Catalog registration: deploy the data bundle again. Switch the ETL to Lakebase. **Done.**
5. The app, with its resources and environment variables. Then the self-stop grant. **Done.**
6. The `review_explorer` task. **Done** (it already existed from the earlier build, and ran successfully).
7. Seed with `ops_reset` and `daily_run`, and check the gold tables, the app and `review_explorer`. **Still to do as a full pass.**

### Done

- **Destroy (2026-11-05).** `databricks bundle destroy -t dev -p personal` reported 15 deleted: four jobs, the app, three Lakebase resources, four schemas and three volumes. Tim checked the portal afterwards: only `information_schema` was left under `bakehouse_dev`, and the old jobs were gone.
- **Data bundle deployed (2026-11-05).** From `dab-data/`: `validate`, `plan` and `deploy` with `-t dev -p personal`. Four schemas and three volumes were created. Tim checked Catalog Explorer and it looked right.
- **`simulator_dev` built and run (2026-11-06).** Serverless job, one `spark_python_task` `simulate` running `jobs/run_from_src.py` from the Git folder. Task parameters are a JSON array of strings (the portal wants that shape). Job parameters `days`=`1`, `mode`=`auto`, `scenarios`=empty, `triggered_by`=`manual`, as in `dab-compute/resources/jobs.yml`. The environment lists `sqlalchemy`. The run timeout is under Job health configuration / Metric thresholds, not in the main settings. First run released 1 day up to 2024-05-01 (207 transactions, 301 customers, 48 franchises, 27 suppliers, 5 reviews).
- **`ops_reset_dev` built and run (2026-11-06).** Same shape as the simulator: serverless, one `reset` task running `jobs/run_from_src.py`, JSON-array task parameters, the `sqlalchemy` environment. Job parameters `level`=`data`, `confirm`=empty, `requested_by`=`manual`; the run used `confirm`=`dev`. Tim ran it and it worked, then re-ran `simulator_dev` to put day 1 back.
- **`bakehouse_etl_dev` built and run (2026-11-06).** Tasks `load_params`, `silver_build` and `gold_build` run `jobs/run_from_src.py` with the Delta parameter backend (`--param-backend delta`). With `delta`, `--params-catalog` is ignored, so it is left off. All tasks use the `sqlalchemy` environment. The first full run failed in `silver_build` (see the `bakehouse_core` pickling entry in section 5). After the fix, the ETL on its own ran to completion.
- **`daily_run_dev` built and run (2026-11-06).** Two `run_job_task`s, the simulator then the ETL, with the parent's parameters pushed down by name. The failed attempts still released their virtual days, so the passing run was day 4 (2024-05-04). `ops.run_log` had a row per task, all `succeeded`, with the simulator logging its own run ID and the ETL tasks sharing another. Gold held 4 days (192 rows in `franchise_daily_kpis`, 48 franchises times 4). Timing on serverless: `silver_build` about 2 minutes, `gold_build` about 1 minute, `simulator` seconds. All four jobs now work with the Delta backend.

- **Lakebase moved to the data bundle and deployed (2026-11-09).** `dab-data` now holds the project, the `bakehouse` database and the Unity Catalog registration. `validate` and `plan` looked right on both bundles, and `deploy -t dev -p personal` from `dab-data` finished with no errors, so the Beta fields in the moved YAML were accepted first time. Not yet checked in the portal: the project, branch and endpoint names, the tags, and the catalog `bakehouse_params_dev` in Catalog Explorer. The `params` schema only appears once the app starts.
- **Lakebase checked in the portal (2026-11-09).** The project, branch (`production`), endpoint (`primary`) and database (`bakehouse`) names matched `LAKEBASE_ENDPOINT` and the bundle. The portal's Lakebase pages show the project's settings, not the tables inside, so they cannot confirm the data.
- **App created in the portal and deployed from the Git folder (2026-11-09).** The Apps UI has no field for the start command or env vars, and no field that points at source code, so they come from `apps/param_admin/app.yaml` in the source folder. Tim created the app with the Postgres and job resources (named as in `app.yml`) and used the Deploy button with the Git folder's `apps/param_admin` as the source. The committed copy of `bakehouse_core` came with it. The app started and seeded the `params` schema. A saved change was still in the history after the app was stopped and restarted.
- **Registration checked from SQL (2026-11-09).** From the SQL editor, `bakehouse_params_dev` showed the `params` schema and its tables, with the app's saved data in them. Catalog Explorer and the Lakebase pages had shown no tables, which was a view limit, not a fault.
- **Self-stop grant run (2026-11-09).** `scripts/grant_app_self_manage.sh` completed.
- **ETL switched to Lakebase and proven (2026-11-09).** `load_params` and `gold_build` on `bakehouse_etl_dev` got `--param-backend lakebase --params-catalog bakehouse_params_dev`, keeping `--catalog` and the `sqlalchemy` environment. A control run before the change (still on Delta) passed, and so did the run after it. Then a parameter was changed in the app and Rerun ETL pressed: the run succeeded, the `load_params` and `gold_build` output showed the changed values, read through Lakebase, and gold changed with them. The app stopped itself on the idle minute before the run finished. The page only showed it stopped after a refresh.
- **`review_explorer` re-run (2026-11-09).** It already existed from the earlier build and was re-run in `bakehouse_etl_dev`, successfully.
- **Notebooks 02 and 03 moved to source (2026-11-09).** They failed because the wheel was no longer in the `ops.artifacts` volume. They now put `../src` on `sys.path` and ran successfully from the Git folder. The SQL notebook (01) always worked.
- **Fallback to the seed defaults checked (2026-11-09).** `load_params` was run with `--param-backend lakebase --params-catalog bakehouse_dev`, a catalog with no `params` schema. It succeeded and logged: "No parameters found in bakehouse_dev.params yet (the app seeds them on its first start); using the seed defaults, version 1." Before that, a run with both arguments removed also passed, but only because the argument parsers still default to `delta`, so it read the old Delta tables and did not exercise the fallback. A run with the arguments put back passed as before.

## 5. Challenges met

Each entry: what happened, the cause, the fix, and what to do next time.

- **`bundle destroy` needs an interactive terminal.** Run through Claude Code's `!` prefix it printed "To proceed, use --auto-approve" and deleted nothing, because there is no prompt to answer. Fix: run it in a normal terminal, where it shows the list and asks. Use `bundle summary` first to review what it will remove, and avoid `--auto-approve` for anything you have not reviewed.
- **`destroy` deleted the schemas together with tables the bundle did not manage.** `gold` held two Designer output tables created outside the bundle, and it still went cleanly with them. Anything a bundle-managed schema holds is lost on destroy, managed or not.
- **Portal lists lag.** After the destroy the Jobs list kept showing the old jobs for a while, and the Jobs list has no refresh button. They disappeared after a delay. Do not read a stale list as a failed destroy. Catalog Explorer does have a refresh button, and it cleared the stale `bronze`, `silver` and `gold` entries. `databricks schemas list <catalog>` shows the real state.
- **Leftover `.bundle` folders.** After the destroy, the workspace still showed `.bundle/bakehouse` and `.bundle/bakehouse-data` with `dabs-runner-*.py` files in them, although `bakehouse-data` had never been deployed. Cause not established. Tim deleted both folders by hand. Harmless, but check `.bundle` after a destroy.
- **`__file__` is not defined on serverless.** The first `simulator_dev` run failed with `NameError`, because serverless runs the file through `exec(compile(...))`. Fix: `run_from_src.py` reads the path from `main.__code__.co_filename`, and a test runs it that way. Reasons and alternatives are in `docs/decisions.md`. Next time: do not assume `__file__` in anything a job runs as a script.
- **A task with no environment has no `sqlalchemy`.** `daily_run_dev` failed with `ModuleNotFoundError: No module named 'sqlalchemy'`. Each job has an Environment (version plus dependencies) and each task has to select it, otherwise it gets the plain default. Fix: an environment listing `sqlalchemy` (`jobs/base_environment.yml`), attached to every task. The portal list is a second copy of the dependencies in `pyproject.toml`, so keep them in step.
- **`silver_build` failed at `.start()` with `No module named 'bakehouse_core'` (STREAMING_PYTHON_RUNNER_INITIALIZATION_FAILURE).** `foreachBatch` pickles its handler and a separate worker process loads it. The pickle only said "import `bakehouse_core`", and `run_from_src.py` put `src` on `sys.path` in the driver only. The other tasks never ship package objects to another process, so they passed. Fix: `run_from_src.py` registers `bakehouse_core` with cloudpickle to be pickled by value (`pickle_package_by_value`), so edits in the Git folder still run with no wheel. A test unpickles a handler in a fresh process without `src`, and fails when the registration is removed. Proven on Databricks: `bakehouse_etl_dev` then ran to completion. Sandbox and above install the wheel everywhere and do not need this. Next time: any Python callback Spark runs elsewhere (`foreachBatch`, UDFs) needs the package in that process.
- **The Apps UI has no field for the command, env vars or source path.** They come from `app.yaml` in the source folder, and the source is chosen on the Deploy button. Fix: `apps/param_admin/app.yaml`. The bundle's `config:` block was removed so there is one copy. Next time: write `app.yaml` before creating the app by hand.
- **A Git folder has no gitignored files.** The staged copy of `bakehouse_core` was gitignored, so an app deployed from the Git folder had no package. Fix: commit the copy, and make `tests/test_stale_copy.py` fail when it is missing or differs. Next time: run `python scripts/stage_app.py` and commit the result whenever `src/bakehouse_core` changes (a pre-commit hook is recommended in the README, not built).
- **`databricks workspace import-dir` turns `.py` files into notebooks** (it strips the extension), so it cannot upload an app. `databricks sync` skips gitignored files unless `--include` is passed. Neither was needed in the end.
- **Notebooks that install the wheel fail once there is no wheel.** 02 and 03 now run from source like everything else in dev.
- **`uv run` fails inside the Claude Code sandbox** (it cannot write its cache). Run dependency-free scripts such as `scripts/stage_app.py` with `.venv/bin/python`, or run `uv` from a normal terminal.
- **A job with no parameters set fails on the first run.** The task had no parameter values to pass (the exact error was not recorded). Set the job parameters and their defaults before the first run.
