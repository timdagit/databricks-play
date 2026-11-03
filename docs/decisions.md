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
