# Build guide

This guide describes what each part of the build should achieve and the decisions already taken. It also records what has been verified and the traps worth knowing about. It is not a procedure: use your own knowledge and skills for the implementation, and challenge anything here that your experience says is wrong or out of date.

[`SPEC.md`](SPEC.md) is the detailed reference. [`CLAUDE_CODE_GUIDE.md`](CLAUDE_CODE_GUIDE.md) holds the guardrails.

The workstreams below are in a sensible dependency order. Reorder, merge or split them as you see fit.

---

## 0. Orientation

**Outcome**

- The existing resources' names and IDs are discovered and confirmed with Tim.
- They are stored in gitignored config, with committed `*.example` templates.
- Tool versions are known.

**Guidance**

- Discover rather than ask where you can: `az`, `databricks` and `gh` read-only commands.
- Pin one Python version that suits both the serverless job environment and the Databricks Apps runtime.
- Note the existing cluster's auto-termination setting. Tim will set it to the platform minimum; the project doesn't manage that cluster.
- Start `docs/decisions.md` here.

---

## 1. Repository foundations

**Outcome**

- An installable `bakehouse_core` package (uv), laid out roughly as in spec §5.1.
- Lint, format, tests, pre-commit hooks and gitleaks.
- CI on PRs.

**Guidance**

- Shape the package around the spec's modules: `params`, `simulator`, `transforms`, `dq`, `jobs`, `config`, `io`. Adjust if a cleaner split emerges.
- **Public repo, so build hygiene in from the first commit:**
  - `.gitignore` covers env files, tfvars, Terraform state and `.databricks/`
  - a gitleaks rule for `*.azuredatabricks.net`
- **CI:**
  - unit tests need no cloud access
  - Spark-dependent tests run in CI where Java is available
  - any step that needs Databricks credentials uses the `test` environment and never runs for PRs from forks

---

## 2. Terraform foundation

**Outcome:** everything the bundle relies on that doesn't exist yet (spec §6.5):

- **Storage:** an ADLS Gen2 account with a container per target, and an access connector with its managed identity.
- **Unity Catalog:** a storage credential, external locations, and catalogs `bakehouse_dev`, `bakehouse_test` and `bakehouse_prod`.
- **Service principals:** Databricks-managed `sp-bakehouse-test` and `sp-bakehouse-prod`, with workspace assignment, OAuth secrets and grants on their own catalog only.
- **Cost controls:** a serverless usage policy and an account budget.
- **Azure governance:** tags on the existing resource group and workspace, tag policy assignments, and a resource-group budget.
- **GitHub:** Environments `test` and `prod` with their secrets, and Tim as the prod reviewer.

**Decisions already taken**

- Existing resources are read as data sources and never managed. Tags are merged onto them without removing existing tags; the Azure Tags API supports a Merge operation.
- State lives in a private Azure Storage backend, created by a small bootstrap stack. It holds SP secrets, so it is never committed.
- Terraform runs from Tim's Mac, not CI. Tim approves `apply`.
- `idle_stop_minutes` exists as a variable here too.

**Known traps**

- A catalog `bakehouse_dev` was created by hand during the workspace checks. Drop it or import it before the first apply.
- If the storage account disables shared keys, the azurerm provider must use Azure AD for data-plane calls.
- Account-level and workspace-level Databricks resources need separate provider configurations.

---

## 3. Bundle foundations

**Outcome**

- `databricks.yml` with `dev`, `test` and `prod` targets.
- Schemas `bronze`, `silver`, `gold` and `ops`, with volumes `bronze.raw` and `ops.checkpoints`.
- Wheel artifacts, preset tags, and shared variables (`catalog`, `idle_stop_minutes`, `job_timeout_seconds`, identity variables).

**Decisions already taken**

- `dev` uses development mode as Tim. `test` and `prod` use production mode, where CI deploys as the target's SP and `run_as` is that same SP.
- The workspace host comes from the environment, never from the YAML.
- Schedules are paused outside prod.

**Known traps**

- When the deploying identity differs from `run_as`, bundles only support jobs and pipelines. Apps and Lakebase would fail, which is why deployer and runner are the same SP.
- Usage-policy field names for jobs and apps have changed over time. Check them with `databricks bundle schema`.

---

## 4. Simulator and reset

**Outcome**

- A virtual clock (`ops.sim_clock`) and a simulator that writes one day of raw JSON files per run into the bronze volume. There is no landing zone and no bronze Delta tables.
- An `ops_reset` job with `data` and `hard` levels (spec §3.2, §3.6).

**Guidance**

- **Calendar:** the simplest mapping is a virtual calendar starting on 2024-05-01. Replay then covers the 17 real days, and synthetic days follow from 2024-05-18.
- **Synthetic days:** must be reproducible from a seed and the date.
- **Scenarios:** late files, duplicates, null spike, underperforming franchise, negative-review burst. These exist to exercise the DQ rules and parameters, so make their effect visible.
- **Reset:**
  - truncate tables rather than dropping them, so grants and bindings survive
  - a hard reset reseeds the parameters in both backends from the same seed used locally
  - guard with `confirm=<target>` and a prod opt-in
  - log every reset to `ops.reset_log`, which is never cleared

---

## 5. ETL

**Outcome**

- `bakehouse_etl`: parameters loaded once as of the virtual date, Auto Loader from bronze into silver (with ingest metadata, deduplication, SCD2 and DQ quarantine), then gold.
- `daily_run`: simulator, then ETL.
- Two modes: `incremental`, and `recompute_gold`, which rebuilds gold for every virtual day so far using each day's own as-of parameters.

**Guidance**

- Write the transforms as pure functions over DataFrames so they're testable with small fixtures.
- Record the parameter versions used and `triggered_by` in `ops.run_log`.
- **Job settings:**
  - one concurrent run per job
  - a timeout that only catches runaways (spec default 15 minutes)
  - a duration health warning

---

## 6. Parameter core

**Outcome**

- The 10 parameters (spec §4.1), with a shared DDL and seed that works on SQLite and Postgres.
- An append-only `param_versions` model with `effective_from`.
- A `ParamRepository` interface with SQL and Delta implementations and a factory selected by `PARAM_BACKEND`.

**Guidance**

- Saves only insert new versions. That avoids upsert differences between Postgres and Delta, and gives a full audit trail.
- **Hide auth and identity behind small interfaces:**
  - a `TokenProvider`, with Databricks Apps, local CLI and managed-identity implementations
  - a user-identity function
  - a `JobTrigger`, with a real Databricks implementation and a fake for local use

  These are what make the AKS and local options in spec §9 cheap later.

---

## 7. Dash app (local first)

**Outcome**

- The app runs in Docker Compose against Postgres 16, seeded from the shared files.
- It loads, validates, saves and shows history.
- It has Rerun ETL and Run next day buttons, which use the fake trigger locally.

**Guidance**

- Behaviour is described in spec §4.4. The layout and component choices are yours.
- **Idle watchdog** (spec §6.4):
  - only user-initiated callbacks count as activity, so status polling must not keep the app alive
  - expose `/idle`
  - disabled locally
- The same image should be deployable elsewhere later (spec §9.1). Keep it container-first and configured only through environment variables.

---

## 8. Lakebase backend and app on Databricks

**Outcome**

- The app deployed to dev on Databricks Apps, backed by a Lakebase Autoscaling project per target.
- Lakebase registered in Unity Catalog for the ETL's as-of read.

**Decisions already taken**

- Lakebase: smallest size, scale-to-zero at 60 seconds.
- App: smallest compute size.
- App resources: `postgres`, plus `job` resources with CAN_MANAGE_RUN on the two jobs.

**Known traps**

- **Bundle support:** Lakebase in bundles (`postgres_projects` and related resources) is Beta. Confirm the fields with CLI 1.23.
- **Two permission layers:**
  - project ACLs
  - Postgres roles: the app's role is created through the app resource. The env SP needs SELECT, INSERT and DELETE on `param_versions` for the ETL read and the hard reset, granted after the first deploy.
- **Resource type:** for an Autoscaling project, use the app's `postgres` resource type, not `database`.

---

## 9. Run buttons and idle stop on Databricks

**Outcome**

- The buttons start real runs, which execute as the job's SP.
- The buttons are disabled while a run is active, with status and a link to the run.
- The app stops itself about 1 minute after the last user interaction.

**Guidance**

- Self-stop needs the app's SP to have CAN_MANAGE on its own app. If the platform won't allow it, use the fallback in spec §6.4: a scheduled GitHub workflow that checks `/idle` and stops the app.
- Provide an easy way to start the app again.

---

## 10. Delta backend

**Outcome:** the same app behaviour on Delta, through a serverless 2X-Small warehouse with `auto_stop_mins` at 1, switchable per target.

**Guidance**

- The ETL reads the Delta parameter table directly, never through the warehouse.
- App access is through `sql-warehouse` and `table` app resources.

---

## 11. Designer and notebooks

**Outcome**

- Two visual data preps, `review_explorer` and `supplier_mix`, run as tasks in the ETL job, with the catalog overridden per target.
- Four notebooks (spec §3.5).

**Guidance**

- Tim builds the visual data preps on the canvas in a workspace Git folder and commits them. You wire them into the bundle as `notebook_task`s.
- The comparison notebook should use `system.billing.usage` by tag (spec §6.3).

---

## 12. Promotion and verification

> **Out of scope for the initial build (decided 2026-11-03).** There is no time to try other environments, so the bundle stays `dev` only. The dev checks in the list below, and a short `docs/ops.md`, are folded into a wrap-up at the end instead. See `docs/decisions.md`.

**Outcome**

- Merging to `main` deploys `test` and runs a smoke test. A `v*` tag deploys `prod` after approval.
- `docs/ops.md` covers start/stop, reset, secret rotation and cost checks.

**Verify, with Tim, in both Azure and Databricks:**

- tags appear on resources and in billing
- budgets exist
- Lakebase suspends, the warehouse stops and the app stops at their idle settings
- nothing is left running unexpectedly

---

## 13. Comparison

**Outcome:** `docs/backend-comparison.md` compares Lakebase and Delta on latency, cost, local development experience, governance and code complexity. It uses a scripted exerciser in `dev` over 14 virtual days (it was planned for `test`, which is out of scope), and ends with a recommendation that notes when the spec §9 options would change it.

*Where it runs, the hand-built dev or the new `sandbox` target, is decided with workstream 14. The Delta backend (workstream 10) needs a warehouse and app resources, so it is easier to build in the bundle than by hand.*

---

## 14. Rebuild: portal-first dev, bundle from sandbox

**Context:** Tim has chosen to destroy the bundle-deployed dev environment and rebuild it from scratch, following the workflow in spec §9.3. Dev becomes hand-built in the portal from the workspace Git folder. The bundle's first deployment target becomes a new `sandbox`. This replaces the dev part of workstream 3. There's no real data, so nothing needs preserving except what's in Git.

**Outcome**

- **Dev, rebuilt by hand:** dev runs again, built in the portal from the Git folder with no bundle deployment. It produces the same gold outputs, the app works on Lakebase, and the run buttons and idle stop behave as before.
- **A log:** `docs/dev-rebuild-log.md` records every manual step, setting and parameter value used to build dev.
- **Captured YAML:** the jobs built by hand in dev are exported to YAML and compared with the existing bundle YAML. Differences are resolved and recorded in `docs/decisions.md`.
- **Two bundles:** `dab-data/` (schemas and volumes, deployed to every environment including dev) and `dab-compute/` (jobs, app, Lakebase, Designer tasks, starting at sandbox). See spec §9.3.
- **Sandbox:** a `bakehouse_sandbox` catalog (Terraform) and a `sandbox` target in both bundles (development mode for compute only), deployed for the first time and seeded.

**Sequence (suggested)**

1. **Pre-destroy check, with Tim.**
   - Everything is committed and pushed, from both the Mac and the workspace Git folder, including `lakeflow/review_explorer.designer.ipynb`.
   - Run `databricks bundle plan -t dev` and summarise anything deployed that differs from the YAML. Those differences are about to be lost.
   - List the manual post-deploy steps that were done: Lakebase grants, app self-stop permission, saved parameter values.
2. **Destroy (Tim runs it).** `databricks bundle destroy -t dev`. Review the deletion list together first.
   - Afterwards, check what's left: the catalog, tables created outside the bundle (the Designer output tables, `gold.review_weekly_summary*`), the Git folder.
   - Drop leftover schemas by hand if a clean start is wanted.
3. **Rebuild dev in the portal.** Tim does this; Claude Code guides and logs.
   - Deploy the data bundle (`dab-data/`): the schemas and volumes, including `ops.artifacts`. It is the one part of dev that comes from a bundle.
   - Create the jobs (`simulator`, `bakehouse_etl`, `daily_run`, `ops_reset`) in the Jobs UI. Each runs `jobs/run_from_src.py` from the Git folder as a `spark_python_task`, with the job name first and then its parameters, and lists `sqlalchemy` in its environment. Notebook and Designer tasks point at the Git folder, never at `.bundle/`.
   - The wheel is not used in dev, except where something cannot import from `src` (the app, probably).
   - Create the Lakebase project (scale-to-zero at 60 seconds) and run the grant script.
   - Create the app in the Apps UI from `apps/param_admin` in the Git folder, with the `postgres` and `job` resources. Then grant it self-stop.
   - Set dev's parameter values on the resources: job parameters, app environment variables.
   - Seed with `ops_reset` and `daily_run`, then confirm the gold tables, the app and `review_explorer` all work.
4. **Capture and compare.**
   - Export each hand-built job with **Edit as YAML** into a scratch folder (not `resources/`).
   - Diff the exports against the existing `resources/*.yml`. For each difference, decide whether the YAML or the portal version is right. Update `resources/` so environment-specific values are variables.
   - Note which resources had no export route: app, Lakebase, schemas, warehouse. Those stay hand-written YAML.
5. **Sandbox.**
   - **Terraform:** add a `bakehouse-sandbox` container, external location and the `bakehouse_sandbox` catalog. Tim applies.
   - **Layout:** move the compute bundle into `dab-compute/`. Its relative paths change, and the app, notebooks and `src` sit outside its root, so it needs `sync.paths`. Bundle commands are then run from inside each folder. Sandbox compute uses wheel tasks again.
   - **`databricks.yml`:** add a `sandbox` target (development mode, as Tim, catalog `bakehouse_sandbox`, schedules paused). Retire the `dev` target; remove it or leave it as a comment.
   - **Deploy:** `bundle validate`, then `bundle deploy -t sandbox`. Seed with `ops_reset` and `daily_run`. Check the app works and that its name doesn't clash with the hand-built dev app.
6. **Docs.** Update spec §5.3 (targets), `CLAUDE_CODE_GUIDE.md` (dev is hand-built; the bundle starts at sandbox) and `docs/ops.md`.

**Known traps**

- **Destroy can take data with it.** It removes bundle-managed schemas and volumes along with their tables, and the Lakebase project with the whole parameter history. That's acceptable here; just confirm the list first.
- **Hand-built dev jobs must point at the Git folder and the uploaded wheel.** If they point at any `.bundle/` path, they'll run stale code or break.
- **`bundle generate job` only supports notebook-task jobs.** Wheel-task jobs must be captured with **Edit as YAML**.
- **App names are unique per workspace.** Use the target name in bundle-deployed app names (`${bundle.target}`), and a different name for the hand-built dev app if needed.
- **Cost.** For a while, two environments run side by side: two apps and two Lakebase projects. Both have 1-minute idle stops; check they're stopped after each session.
