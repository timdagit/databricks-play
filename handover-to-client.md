# Handover to the client environment

This file hands over the Bakehouse Databricks PoC to a new environment and a new Claude Code session. That session will have all the code but none of the conversation history that produced it. This file carries the part the code cannot: why things are the way they are, what went wrong on the way, what is proven and what is not, and how the work was done with Claude Code.

Read it together with these files, which hold the detail:

| File | What it holds |
|---|---|
| `docs/SPEC.md` | The design. §9 ("Moving to the real world") matters most for a client |
| `docs/BUILD_PLAN.md` | The workstreams, outcomes and known traps |
| `docs/decisions.md` | Every departure from the spec, with date and reason. Newest last |
| `docs/dev-rebuild-log.md` | The by-hand rebuild of dev in the portal: steps and challenges met |
| `docs/changes-since-claude-app.md` | A summary of the 2026-11-05 to 2026-11-09 changes |
| `docs/az-inf-requirements.md` | What the Terraform project had to provide |
| `docs/CLAUDE_CODE_GUIDE.md` and `CLAUDE.md` | The working rules used with Claude Code |
| `README.md` | Local development, deploy and destroy commands, variables |

Nothing in this file or the repo identifies the original workspace. Hosts, IDs, tenants and emails were kept out on purpose. You will supply your own.

## 1. What this project is, and why it exists

**A learning PoC.** It was built by one person (Tim) on a personal Azure Databricks workspace and a personal budget, to learn and to demonstrate the current Databricks feature set end to end. It is not a product and holds no real data. The repo is public, so the code and docs are meant to be read by colleagues.

**The scenario.** Head office of a bakery franchise network ("Bakehouse") wants a daily view of franchise performance against target (with underperformers flagged), customer loyalty tiers, supplier dependence and review alerts. Business users tune the rules behind those outputs through 10 parameters in a web app. The data is Databricks' public `samples.bakehouse` sample.

**What it exercises**

| Feature | Where |
|---|---|
| Python ETL on serverless jobs | Bronze, then silver, then gold, with Auto Loader and `foreachBatch` |
| A reusable Python package | `src/bakehouse_core`, used by the jobs, the app, the notebooks and the tests |
| Databricks Apps | A Dash app, `apps/param_admin`, that edits the 10 parameters and has run buttons |
| Lakebase (managed Postgres) | The parameter store the demo uses, read by the ETL through a Unity Catalog registration |
| Lakeflow Designer | One visual data prep, `lakeflow/review_explorer.designer.ipynb` |
| Notebooks | `01` explore (SQL), `02` data-quality report, `03` promo what-if with charts |
| Declarative Automation Bundles | Two bundles, `dab-data/` and `dab-compute/` |

**Why the data is simulated this way.** The sample covers only 17 days (1 to 17 May 2024). A simulator releases one "virtual day" per run, replaying the real days and then generating synthetic ones, so a demo can show many runs over many days. A virtual clock lives in `ops.sim_clock`. Five injectable scenarios each exercise a data-quality rule or a parameter: `late_files`, `duplicates`, `null_spike`, `underperforming_franchise`, `negative_review_burst`.

**Cost was a design requirement.** It ran on personal money. So: serverless only, no always-on clusters, schedules paused, Lakebase scaling to zero after 60 seconds, a 1-minute idle stop on the app, and apps left stopped when not in use. If your environment has different cost rules, the places to change are the `idle_stop_minutes` and Lakebase settings in the bundles and `apps/param_admin/app.yaml`.

## 2. Architecture in one page

```
samples.bakehouse ──► simulator job ──► bronze volume (raw JSON, one folder per day)
                                              │ Auto Loader (availableNow)
                                              ▼
 Dash app ──► Lakebase (params schema)    silver tables (+ SCD2, quarantine)
   │              │ Unity Catalog              │
   │ run buttons  │ registration               ▼
   ▼              └────────► load_params ──► gold_build ──► gold tables (one snapshot per virtual day)
 bakehouse_etl / daily_run jobs                              │
                                              review_explorer (Designer) and notebooks 01-03
```

- **Catalog layout.** One catalog per environment (`bakehouse_dev`, and so on) with schemas `bronze`, `silver`, `gold` and `ops`. Schema names are fixed in every environment, and environments are separated by catalog.
- **Parameters.** Versioned and append-only (`param_definitions`, `param_versions`). An edit inserts a new version. The current value is the highest version, and the as-of value is the highest version whose `effective_from` is on or before the virtual date. Cross-field rules (silver tier spend must be below gold) are validated in `bakehouse_core.params`, not in the database.
- **Where logic lives.** `bakehouse_core` holds all reusable logic. Jobs, notebooks and the app stay thin. Tests run locally with no cloud access, and tests that need Spark or Databricks are marked `spark` or `databricks`.
- **Parameter backends.** `sqlite` (tests and quick local runs), `postgres` (Docker locally), `lakebase` (Databricks) and `delta` (code kept, not used in the demo). `bakehouse_core.params.factory.get_repository` chooses from environment variables.

### The ETL, and the rules that are not in the spec

`bakehouse_etl` chains `load_params`, `silver_build`, `gold_build` and the Designer task `review_explorer`. `daily_run` runs `simulator` and then `bakehouse_etl`, on a schedule that is paused. The modes of `bakehouse_etl` are `incremental` and `recompute_gold` (with an optional `from_date`).

These choices were made during the build and are not in the original spec:

- **Parameters pass between tasks through `ops.run_log`**, not task values. `load_params` writes the values and versions it used into its `run_log` row, and the later tasks read that row. This avoids the `dbutils` task-values API, and the lineage record is the same data the tasks use.
- **Bronze is read as strings** (`inferColumnTypes=false`, `schemaEvolutionMode=rescue`). Silver casts with `try_cast`, so a bad value becomes null and the row is quarantined instead of failing the job.
- **Quarantine.** Rows missing a required value, or with a non-positive quantity, go to `transactions_quarantine` or `reviews_quarantine` with a `_dq_reason`. If the null rate of `paymentMethod` in a batch exceeds `dq_max_null_pct`, the null rows are quarantined too.
- **SCD2** for customers and franchises: one MERGE per virtual day, oldest first, so a backfill gives the same history as day-by-day runs.
- **Gold is one snapshot per virtual day** (`as_of_date`). The day is the release day, not the transaction timestamp, so a late file counts on the day it arrives. `recompute_gold` rebuilds every day with that day's own parameters.
- **Franchise flags** compare a franchise's last 7 days with its earlier days, relative to the network's change over the same windows. The first version (3-day windows, absolute change) flagged 25 of 48 franchises on one day because a franchise sells only 4 to 5 transactions a day and the 3-day average swings by chance.
- **`supplier_concentration_threshold` defaults to 0.10**, the lowest allowed, not 0.60. In the sample data every franchise has its own supplier and the biggest holds about 14% of revenue, so 0.60 could never trigger. The spec calls the defaults first guesses.
- **Source data quirk.** 21 of 48 franchises point at a `supplierID` missing from the suppliers table. Gold keeps them with `supplier_known = false` so the gap is visible.
- **Negative reviews** are detected by a short keyword list. It suits the simulator's burst text and would miss differently worded real reviews.
- **Gold tables evolve.** `GoldTables.ensure_tables` adds missing columns. Removing or renaming a column needs a deliberate drop and `recompute_gold`.

## 3. How the project was built

The build followed `docs/BUILD_PLAN.md` in workstreams, one small reviewable commit at a time. In order: repo foundations and CI; simulator and reset; the ETL; the parameter core with SQLite tests; the Dash app locally in Docker Compose with Postgres; Lakebase and the app on Databricks; the run buttons and idle stop; Designer and notebooks; then the rebuild of dev by hand (workstream 14).

**Dropped scope, on purpose**

| Dropped | Why |
|---|---|
| Workstream 12 (`test` and `prod` targets, service principals as run identities, deploy workflows, GitHub Environments) | No time in the initial build. CI still runs tests, ruff and gitleaks, which need no Databricks access |
| Workstream 10 (a Delta backend in the app) and 13 (the Lakebase versus Delta comparison, notebook `04`) | No time, and the demo uses Lakebase from the start so nothing has to be repointed mid-demo. The Delta repository stays because the read-only Lakebase repository builds on it |
| The `supplier_mix` Designer prep | One prep was enough to show the pattern |
| `ops.app_metrics` event log for idle stops | The stop is logged to the app log instead |

**The big mid-project change (2026-11-05).** The build was code-first: the bundle created everything, dev included. After a discussion, Tim decided to destroy dev and rebuild its compute by hand in the portal, from the workspace Git folder, to learn what the portal route costs and what has to be captured back into YAML. The consequences shape the repo today:

- **Two bundles.** `dab-data/` holds schemas, volumes and the Lakebase project, and is deployed everywhere. `dab-compute/` holds jobs, the app and the Designer tasks. The reason is that Unity Catalog objects cannot be exported from the portal into Git, so they always come from a bundle, and keeping them apart means compute can be rebuilt or destroyed without touching data. Destroying the data bundle deletes every table in its schemas, including tables it did not create.
- **Dev runs from source.** Dev jobs run `jobs/run_from_src.py`, which puts `src` on the path. The app and notebooks 02 and 03 use source or a committed copy. The wheel is used from the `sandbox` target upwards.
- **`dab-compute/` has never been deployed.** It is the reference YAML that the hand-built dev jobs were compared with. Its first deployment was meant to be at `sandbox`. The `sync`, artifact and path settings in it are therefore **unproven**. Expect to fix things at the first deploy.

**What was proven on Databricks** (see the dated entries in `docs/decisions.md`): the simulator and reset jobs; a multi-day backfill; the `duplicates` and `null_spike` scenarios end to end; the full ETL chain on serverless (a 3-day run takes about 5½ minutes, mostly the per-day gold queries); the app on Databricks Apps with Lakebase, including saves and the signed-in user recorded in `updated_by`; the ETL reading parameters from Lakebase through the Unity Catalog registration and gold changing with them; real job triggers from the app; the app stopping itself; `review_explorer`; and notebooks 01 to 03.

**Not yet exercised on real data:** SCD2 with an actual change (days 1 to 5 released none), and the scenarios `late_files`, `underperforming_franchise` and `negative_review_burst` on Databricks (unit-tested only). The synthetic phase starts on virtual day 18.

## 4. Problems met, and what to do next time

These cost real time. They are also in `docs/dev-rebuild-log.md` and `docs/decisions.md`, grouped here by theme.

### Serverless jobs

- **`__file__` is undefined.** Serverless runs a Python file with `exec(compile(source, filename, "exec"))`. The first dev job failed with `NameError`. `jobs/run_from_src.py` finds `src` from `main.__code__.co_filename`, and `tests/test_run_from_src.py` runs it the serverless way so a change back to `__file__` fails.
- **`foreachBatch` runs in another process.** `silver_build` failed with `No module named 'bakehouse_core'` (`STREAMING_PYTHON_RUNNER_INITIALIZATION_FAILURE`), because the handler is pickled and a worker loads it, and `src` had been added to the driver's path only. The runner now registers the package with cloudpickle to be pickled by value. Sandbox and above install the wheel and do not need this. Any Python callback Spark runs elsewhere (`foreachBatch`, UDFs) needs the package in that process.
- **Values set inside `foreachBatch` never reach the main program.** `silver_build` writes each batch's counts to files under the checkpoints volume (`_stats/<run id>`) and reads them back.
- **Each task must select its environment.** A task with none gets no `sqlalchemy` (`jobs/base_environment.yml` lists the dependencies). That file is a second copy of the dependencies in `pyproject.toml`, so keep them in step.
- **Parent job parameters override the child's by name.** `daily_run` first had a `mode` parameter, which overrode the simulator's `mode` and made it exit with an argument error. It is now `etl_mode`. Keep parameter names in a parent job distinct from its children's.
- **A job with no parameter values set failed on its first run.** Set job parameters and defaults before the first run. The run timeout lives under Job health configuration, not the main settings.
- **Task parameters** are entered in the portal as a JSON array of strings.

### Storage and permissions

- **`Request for user delegation key is not authorized`** on the first job. The access connector's `Storage Blob Data Contributor` role was scoped to single containers, and generating a delegation key is an account-level action. The fix was a `Storage Blob Delegator` role at storage-account scope (in the Terraform project). Role assignments can take minutes to apply.
- **Catalog storage.** The existing `learning` catalog uses the root of the shared container, so the new catalog got its own container to avoid overlapping managed locations and to allow a clean teardown.

### Lakebase and the app

- **`public` is closed to the app.** The app resource's `CAN_CONNECT_AND_CREATE` permission does not let it create tables in `public`. Tables live in a `params` schema that the app creates and owns (`ensure_schema` runs `CREATE SCHEMA IF NOT EXISTS params`, and connections set `search_path=params`).
- **The app owns the parameters, not the ETL.** If a job seeded the schema, the job's identity would own it and the app would lose access. So the ETL has a read-side fallback (`CatalogReadRepository`): when the `params` schema or tables do not exist or are empty, it reads the seed defaults (version 1) and logs a warning. A missing catalog or any other error still fails, so a broken registration is not mistaken for an empty store. **One case is unverified:** the exact error a Lakebase-registered catalog raises for a missing table on a fresh Lakebase. If the fallback does not trigger there, add that error to the marker list in `params/delta.py`.
- **The app's Postgres role** is created automatically (`dbrx-apps-<service principal id>`) with OAuth login. The password is a token valid for an hour from the SDK's `generate_database_credential`, wrapped in `LakebaseTokenProvider`. The SDK's `expire_time` is a protobuf `Timestamp`, not a `datetime`.
- **The runtime chose Python 3.15** until the app's `requires-python` was bounded to `>=3.12,<3.13`. Always give an upper bound.
- **A Git folder has no gitignored files.** The staged copy of `bakehouse_core` had been gitignored, so an app deployed from the Git folder had no package. The copy in `apps/param_admin/bakehouse_core/` is now **committed**, `scripts/stage_app.py` refreshes it, `tests/test_stale_copy.py` fails if it is missing or stale, and `app.py` refuses to start locally on a stale copy. A pre-commit hook to do this automatically was recommended and **not built**.
- **The Apps UI has no field for the start command, environment variables or source path.** They come from `apps/param_admin/app.yaml`, and the source is chosen on the Deploy button. Write `app.yaml` before creating an app by hand.
- **The idle stop.** There is no built-in idle stop for Databricks Apps. A watchdog thread in the app (`config/idle.py`) calls `apps.stop` on itself after `IDLE_STOP_MINUTES` of inactivity, with a 180-second start-up grace so a 1-minute limit cannot stop the app before its starter has opened it. The first attempts failed, and the error text was lost because **logs of a stopped app cannot be read** (`websocket: bad handshake (HTTP 503)`). It worked after two simultaneous changes: passing the app's name explicitly, and granting the app's service principal `CAN_MANAGE` on itself with `scripts/grant_app_self_manage.sh`. Which one was the cause is unknown, so keep both. Run the script once after the app exists. Capture any logs you need while the app is running.
- **A bundle resource cannot refer to its own outputs** (`cycle detected`), which is why the self-stop grant is a script and not bundle config.
- **The app page does not show it has stopped** until it is refreshed.
- **Effective-from in simulated time.** A save with a blank effective-from used the real date (November 2026), but the ETL reads parameters as of the virtual date (May 2024), so the change was stored and never applied. A blank date in the app now means "from the start" (1900-01-01). An explicit date limits the change to days from then on.
- **Reruns cover only the days a save affects.** The app remembers the earliest effective date saved since the last rerun and passes it as `from_date`.

### Bundles and the CLI

- **Development mode prefixes schema names** (`dev_<user>_silver`), and an empty `name_prefix` is ignored. So no target uses `mode: development` in the data bundle. Jobs and apps then need explicit names (`..._${bundle.target}`), and settings that development mode would have applied (such as concurrent run limits) are set by hand.
- **Cross-bundle references do not resolve.** The app names the Lakebase project through a `lakebase_project` variable, and `params_catalog` is a variable in both bundles. The defaults must match each other, and the project id in `lakebase.yml` must match `lakebase_project`.
- **Lakebase bundle resources were Beta** (CLI 1.23). The moved YAML deployed first time, but check `databricks bundle schema` against the CLI you have.
- **`bundle destroy` needs an interactive terminal.** Run through a tool that has no prompt it printed "To proceed, use --auto-approve" and deleted nothing. Review with `bundle summary` first.
- **`destroy` deleted tables the bundle did not manage** in a bundle-managed schema (two Designer outputs in `gold`). Leftover `.bundle/` folders appeared in the workspace afterwards. Cause not established. Check and delete by hand.
- **Portal lists lag.** The Jobs list has no refresh button, and kept showing destroyed jobs for a while. `databricks schemas list <catalog>` shows the real state.
- **`databricks workspace import-dir` turns `.py` files into notebooks**, so it cannot upload an app.
- **The workspace labelled every Git folder file "part of a bundle"** while a `databricks.yml` sat at the folder root. The cause is assumed, not confirmed.
- **Per-user dev schemas were considered and dropped**: the code uses fixed schema names throughout, and a Designer source cannot take a parameter.

### Lakeflow Designer

- **Source and Output operators cannot take parameters** in the original workspace (the Parameters view only offered Disabled). So `review_explorer` reads franchises from a fixed table and writes a fixed output table. The `catalog` parameter reaches only the SQL operator. Check whether this has changed in your workspace, because it limits promotion between environments.
- **A saved GPU preference** in the notebook metadata (`computePreferences.hardware.accelerator`) made the job fail. Clear it on the canvas, or recreate the flow.
- **A job task cannot carry its own tags.** The task has `component=designer` in its description instead.
- **Edit `*.designer.ipynb` only on the canvas.** Code changed outside the editor locks the step until you choose Regenerate code. `ruff format .` once rewrote one, so format only the code folders: `ruff format src tests jobs`.

### Notebooks

- **Notebook 01 uses a session variable, not a widget**, because `CREATE WIDGET` is not supported on a SQL warehouse.
- **Notebooks 02 and 03** went wheel-from-volume, then back to source in dev (they put `../src` on `sys.path`). They must be opened from the Git folder, next to `src`. If deployed notebooks ever need the wheel, make the choice configurable rather than editing the cells.

### Tooling

- **Python 3.12 everywhere.** Serverless environment version 5 runs 3.12.3, and apps using pip run 3.11, so the app uses uv and matches the jobs. Docker uses `python:3.12-slim`. Dash is capped at `<5` because `dash_table.DataTable` is deprecated (the suggested replacement is `dash-ag-grid`). Swapping only touches the layout and callbacks in `create_app`, because the handlers work on plain rows.
- **The pre-commit hook runs gitleaks only.** Ruff runs in CI and by hand.
- **`uv run` fails inside the Claude Code sandbox** (it cannot write its cache). Use `.venv/bin/python` or run `uv` in a normal terminal.
- **The `.gitignore` ignores `*.ipynb`** except `*.designer.ipynb`. Notebooks are source-format `.py` and `.sql` with no outputs.

## 5. Recent changes you should know about

- **Parameter backend default.** The `--param-backend` argument of `load_params` and `gold_build` now defaults to `lakebase` (commit `d47acb5`). Before, the parsers defaulted to `delta`, so a run with the arguments removed passed silently on the old Delta tables. With the default and no `--params-catalog`, the job fails with "the lakebase backend needs the catalog Lakebase is registered as". `--param-backend delta` still works.
- **That change has not yet been re-run on Databricks.** Re-run `bakehouse_etl` once. If it fails at argument parsing, `git revert d47acb5` undoes the parser refactor and the default change.
- **`ops_reset`.** `confirm` must equal the target name (`confirm=dev`), and `level` is `data` or `hard`. A run with `confirm` empty fails with "confirm must equal the target name; nothing was reset". That is the safeguard, not a bug. If a hand-built job's task parameters do not pass `--confirm` and `--level` from the job parameters, check them against `dab-compute/resources/jobs.yml`.
- **Known gap:** a `hard` reset does not reseed the Lakebase parameters (the app owns them, and a job-created seed would take the schema away from the app). It resets what the code can reach. To get the defaults back, start the app on an empty `params` schema, or save the defaults again.

## 6. Running things locally

Quick run of the app, in memory, with a simulated job trigger (no Databricks access):

```bash
uv sync
uv run python apps/param_admin/app.py        # http://127.0.0.1:8050
```

Settings are environment variables: `PARAM_BACKEND` (default `sqlite`), `DATABASE_URL` (a file such as `sqlite:////absolute/path/demo.db` to persist between restarts; without it SQLite is in memory), `JOB_TRIGGER` (`fake` by default, or `databricks` with `ETL_JOB_ID` and `DAILY_RUN_JOB_ID`), `APP_USER`, and `IDLE_STOP_MINUTES` (leave unset locally).

- **A PyCharm run configuration** is a plain Python configuration: script `apps/param_admin/app.py`, working directory `apps/param_admin`, the project `.venv`, and `PARAM_BACKEND=sqlite`. Use gunicorn only to rehearse the container path: module `gunicorn`, parameters `app:server`.
- **Gunicorn** (`gunicorn.conf.py`) runs **one worker with four threads** on purpose, because the idle tracker behind the `/idle` endpoint lives in one process.
- **If the app exits with "The staged copy ... differs"**, run `python scripts/stage_app.py` and commit the refreshed copy.
- **Tests:** `uv run pytest -m "not spark and not databricks"` runs without cloud access (298 passed, 36 deselected at the last count).
- **Postgres in Docker:** copy `.env.local.example` to `.env.local` (gitignored) and run `docker compose --env-file .env.local up --build`. It uses host port 5433 so an existing Postgres on 5432 is undisturbed.

## 7. What to do in the new environment

This is the order that worked here, adapted. Everything that reaches Azure or Databricks is run by a person, not by Claude Code (see section 8).

1. **Infrastructure first.** The original project kept Azure infrastructure in a separate Terraform project. Whatever you use, you need: a Databricks workspace with Unity Catalog, a storage location and credential for a new catalog (with `Storage Blob Delegator` at account scope as well as container-level data access), and a catalog per environment. Read `docs/az-inf-requirements.md` for the reasoning. Also decide how tags and budgets are applied, because the bundle presets set `project`, `env` and `owner` tags and hand-built resources need them set by hand.
2. **Replace placeholders with your values**, in gitignored files or with `--var` / `BUNDLE_VAR_<name>`: `catalog`, `params_catalog`, `lakebase_project`, `warehouse_id` if used. The variable table is in `README.md`. The `-p personal` CLI profile name in the docs is just a profile name, so use your own. Do not set `workspace.host` or tokens in bundle files.
3. **Deploy `dab-data/` first** (schemas, volumes, Lakebase project and its Unity Catalog registration).
4. **Compute.** Either follow the portal-first route (`docs/dev-rebuild-log.md` has the settings for each job and the app), or deploy `dab-compute/` straight from the bundle, which is faster and suits an environment where the bundle is the source of truth. Whichever you choose, state it clearly, because the two routes need different `bakehouse_core` delivery (source or wheel).
5. **Create the app stopped**, deploy it from `apps/param_admin`, run `scripts/grant_app_self_manage.sh` once, then check it stops itself.
6. **Seed and check.** Run `ops_reset` (`confirm` equal to the target), then `daily_run` a few times. Check the gold tables, change a parameter in the app, press Rerun ETL, and confirm `load_params` logs the new value and version.
7. **When finished**, stop the app and remember what keeps costing money: the app while it runs, and Lakebase compute while awake.

### Questions the client's environment will force

- **Where should the app run?** `docs/SPEC.md` §9.1 sets out hosting on a managed AKS platform instead of Databricks Apps, with workload identity, ingress authentication and private networking. The app is container-first for this reason. §9.2 covers Databricks Connect on an always-on cluster as an alternative store.
- **How does the user identity reach the app?** On Databricks Apps it arrives in the `X-Forwarded-Email` header and is stored as `updated_by`. Read it through one function so the header can differ per host.
- **Is private networking required?** The PoC explicitly excluded it. Private connectivity for Lakebase needs confirming.
- **Service principals.** Jobs here run as the deploying user. A real environment needs service-principal run identities, and the Postgres and Unity Catalog grants that go with them. They were not written.
- **How is dev work promoted?** `docs/SPEC.md` §9.3 describes the workflow and the three ways a portal-built resource reaches Git (bundle editor, Edit as YAML, `bundle generate --bind`). Capture is a conversion (task type, code path, dependencies, variables), not a copy, and swapping literals for variables is the one manual step.

## 8. How the work was done with Claude Code

If you carry on with Claude Code, these are the habits that made it productive and safe. The first block is project policy. Review it and keep what suits your organisation.

**Rules that were in force**

- Nothing identifying in tracked files (hosts, IDs, tenant and subscription, emails, tokens). Gitleaks runs as a commit hook and in CI. Staged diffs were also grepped for `azuredatabricks`, `adb-` and `@` before every commit.
- Claude Code never pushed, deployed, destroyed or applied. A person ran `git push`, `databricks bundle deploy`, `run` and `destroy`, `terraform apply`, and anything that changes GitHub settings. Claude supplied the exact command. Commands that reach Azure, Databricks or GitHub were often blocked by the sandbox, and the answer was to have the person run them and paste back the output, never to work around the block.
- Claude asked before deploying the compute bundle to dev, and before anything that deletes data or resources.
- Small commits grouped by purpose, with subject lines starting `[claude] ` and a `Co-Authored-By` trailer, so a person's commits could be told from Claude's.
- Claude proposed a short plan and waited for a go-ahead on anything substantial.
- Where a spec choice looked dated, Claude proposed a change, waited for agreement, and recorded it in `docs/decisions.md`. The docs were written outside the repo, so `CLAUDE.md` wins where it differs from the spec or the guide.
- `*.designer.ipynb` files are written only on the Designer canvas, never by Claude.
- Beta or uncertain features (Lakebase bundle fields, usage policy fields) were checked against `databricks bundle schema` and current docs, not guessed.
- After each piece of work Claude summarised what changed, what it costs to run, what is still running, and what the person must do.

**Practical lessons**

- Keep `docs/decisions.md` current. It was the best memory the project had, and it is what let a new session pick up where the last stopped.
- Write down the *why* at the time. Several decisions here were reversed (wheel in notebooks, then source; Lakebase in compute, then data; gitignored staged copy, then committed). The reasons are what stop a reversal being repeated.
- When something fails on Databricks, record the error text immediately. Two failures here (the first idle stop, a job with no parameters) lost their messages, so the cause was never confirmed.
- `CLAUDE.md` is imported into every session. Keep it short and put long guidance in `docs/`, which `CLAUDE.md` can import.
- Moving to a client environment means the hard rules should be reviewed, not copied: the client's own security, approval and deployment policies take precedence.

## 9. Open items and known stale docs

**Open items**

- Re-run `bakehouse_etl` after the `lakebase` default change (section 5), and exercise the fresh-Lakebase fallback (section 4).
- Make `ops_reset level=hard` reseed the Lakebase parameters, or document how to reset them.
- Rebuild step 7 of `docs/dev-rebuild-log.md` ("seed with `ops_reset` and `daily_run`, check everything") is marked "still to do as a full pass".
- Capture the hand-built dev jobs back into YAML, and compare them with `dab-compute/resources/` (the reason for the portal-first exercise). Nothing has been captured yet.
- Per-target `LAKEBASE_ENDPOINT`. It is fixed to the dev project in `apps/param_admin/app.yaml`. Check the docs for whether a bundle can supply environment variables alongside an `app.yaml`, and which wins.
- `docs/ops.md` (start, stop, reset and cost checks) was planned and not written. A dev wrap-up checklist is in `docs/decisions.md` (2026-11-03, workstream 12): tags show in billing, Lakebase suspends, the app stops at its idle setting, nothing is left running.
- The pre-commit hook for the staged copy of `bakehouse_core`.
- Tags and budgets: Azure tags on the resource group, serverless usage policy and an account budget were deferred because the PoC was dev-only.
- The Designer limits (fixed source and output tables), and whether your workspace allows parameters there.
- If Dash 5 is wanted, replace `DataTable` with `dash-ag-grid`.

**Docs known to be out of step with the work**

- `docs/SPEC.md`: the file tree in §8 is stale, §5.1 and §6.5 and BUILD_PLAN workstream 2 still describe an `infra/` folder (Terraform is in a separate project), and §3.6 mentions `scripts/reset_dev.sh`, which does not exist.
- `README.md`: the status section and some deploy commands still describe the older single-bundle dev. The `-t dev` compute commands only apply from `sandbox` upwards.
- `CLAUDE.md` has a stale "Known differences" line about venv and `requirements.txt` (the project uses uv, and the file's Python section says so).
- `docs/CLAUDE_CODE_GUIDE.md` still says the app has two switchable backends, Lakebase and Delta, and a single bundle.

## 10. Repository map

```
apps/param_admin/     Dash app: app.py, app.yaml, gunicorn.conf.py, Dockerfile, stale_copy.py,
                      bakehouse_core/ (committed copy of src/bakehouse_core, made by scripts/stage_app.py)
dab-data/             Bundle 1: schemas, volumes, Lakebase project and Unity Catalog registration
dab-compute/          Bundle 2: jobs, app, Designer task (reference YAML, never deployed)
jobs/                 run_from_src.py (dev runner) and base_environment.yml (serverless dependencies)
lakeflow/             review_explorer.designer.ipynb (edited only on the Designer canvas)
notebooks/            01 explore (SQL), 02 data-quality report, 03 promo what-if
scripts/              stage_app.py, grant_app_self_manage.sh
src/bakehouse_core/   config (identity, idle), dq, io, jobs, params, simulator, transforms, webapp
tests/                one test file per module, plus staged-copy and run_from_src checks
docs/                 spec, build plan, decisions, rebuild log, architecture diagram (png, svg, drawio)
```

Entry points installed with the wheel: `bakehouse-simulate`, `bakehouse-reset`, `bakehouse-load-params`, `bakehouse-silver-build` and `bakehouse-gold-build`. In dev, `jobs/run_from_src.py <job name> ...` runs the same code from source, and `tests/test_run_from_src.py` keeps its job list in step with the entry points.
