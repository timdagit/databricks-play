# What has changed since the last direction from the Claude app

Written 2026-11-09 as input for the Claude app, which is where the supporting documentation will be generated. The anchor is the 2026-11-05 decision (workstream 14), taken after a discussion in the Claude app: dev is destroyed and rebuilt by hand in the portal, and the bundle takes over from a new `sandbox` target. Everything below follows from that. Full detail is in `docs/decisions.md` and `docs/dev-rebuild-log.md`. No hosts, IDs or emails appear here.

## 1. The new shape of the project

| Area | Before | Now |
|---|---|---|
| Bundles | One bundle, `bakehouse`, at the repo root, deployed to dev | **Two bundles.** `dab-data/` (`bakehouse-data`) holds schemas, volumes and Lakebase, and is deployed to every environment including dev. `dab-compute/` (`bakehouse`) holds the jobs, the app and the Designer tasks |
| Dev compute | Deployed by the bundle | **Hand-built in the portal** from the workspace Git folder. `dab-compute/` is its reference YAML and is first deployed at `sandbox` |
| Code in dev | The built wheel, installed in each job | **Source**: dev jobs, the app and notebooks 02 and 03 all run `bakehouse_core` from `src` or a committed copy. The wheel is for sandbox and above |
| Targets | `dev` (plus unused `test` and `prod`) | `dev` is the data bundle's target and a reference in the compute bundle. `sandbox` (development mode, as Tim, catalog `bakehouse_sandbox`) is the compute bundle's first deployed target. Workstream 12 (`test` and `prod`) stays out of scope |
| Lakebase | In the compute bundle | **In the data bundle**: the project `bakehouse-params-<target>`, database `bakehouse` and the Unity Catalog registration `bakehouse_params_<target>`. `purge_on_delete: true`, so a destroy leaves a clean workspace |

Reasons, in short: Unity Catalog objects cannot be exported from the portal into Git, so they are always created from a bundle. Keeping them apart from compute means compute can be rebuilt or destroyed without touching data. Real projects start in the IDE and workspace and hand over to a bundle when work leaves dev, and rebuilding by hand shows what that costs.

## 2. What was done

- **Dev destroyed (2026-11-05)** with `bundle destroy`: 15 resources deleted. The leftover `.bundle` folders were removed by hand.
- **Data bundle deployed**: four schemas (`bronze`, `silver`, `gold`, `ops`) and three volumes. Lakebase was added to it on 2026-11-09 and deployed with no errors on the first try.
- **Four jobs hand-built and proven**: `simulator_dev`, `ops_reset_dev`, `bakehouse_etl_dev` and `daily_run_dev`, first on the Delta parameter backend.
- **Lakebase checked**: portal names matched, and the registration showed the `params` schema and tables from the SQL editor.
- **App built in the portal and deployed from the Git folder** with the Deploy button. It started, seeded the `params` schema, saved a change, and the change survived a stop and restart.
- **Self-stop grant run**, and the app stopped itself after its idle minute.
- **ETL switched to Lakebase** (`--param-backend lakebase --params-catalog bakehouse_params_dev` on `load_params` and `gold_build`) and proven end to end: a parameter changed in the app, Rerun ETL pressed from the app, the task outputs showed the changed values read through Lakebase, and gold changed with them.
- **`review_explorer`** (Designer) re-run successfully.
- **Notebooks 02 and 03** moved to source and ran successfully. Notebook 01 (SQL) always worked.

## 3. Decisions made since (each is in `docs/decisions.md`)

1. **Two bundles and source in dev** (2026-11-05). Per-user dev schemas were considered and dropped: schema names are fixed in the code, and a Designer source cannot take a parameter.
2. **Compute bundle moved to `dab-compute/` early** (2026-11-06), so path changes are learned while each resource is built.
3. **Lakebase moved to the data bundle** (2026-11-09). Cross-bundle references do not resolve, so the app names the project through a `lakebase_project` variable, and `params_catalog` is a variable in both bundles. The defaults must match, and the project id in the YAML must match `lakebase_project`.
4. **The staged copy of `bakehouse_core` is committed** (2026-11-09). A Git folder has no gitignored files, and separately uploading a copy was a forgettable extra step. The cost is a committed duplicate of the package, so `tests/test_stale_copy.py` fails if it is missing or stale, and the app refuses to start locally on a stale copy (`apps/param_admin/stale_copy.py`). A pre-commit hook that refreshes it is recommended in the README and parked for time.
5. **The app's command and env vars live in `apps/param_admin/app.yaml`** (2026-11-09). The portal has no field for them, and the bundle's `config:` block was removed so there is one copy. `APP_NAME` was dropped in favour of the platform's `DATABRICKS_APP_NAME`.
6. **Notebooks 02 and 03 run from source in dev** (2026-11-09), reversing the earlier wheel-from-volume choice. Deployed notebooks that use the wheel are not built, because there is no use case yet.
7. **Working rule**: Claude asks before deploying the compute bundle to dev, and Tim runs every command that reaches Databricks, Azure or GitHub.

## 4. Challenges and findings worth documenting

- **`__file__` is not defined on serverless**, which runs the file through `exec(compile(...))`. The runner `jobs/run_from_src.py` reads `main.__code__.co_filename` instead, and a test runs it that way.
- **`foreachBatch` handlers run in another process** that cannot import `bakehouse_core` from a path added in the driver. The runner registers the package to be pickled by value (`pickle_package_by_value`). Sandbox and above install the wheel and do not need this.
- **Each serverless task must select its environment**, or it gets no `sqlalchemy`. `jobs/base_environment.yml` is a second copy of the dependencies in `pyproject.toml`.
- **Parent job parameters override the child's by name.** `daily_run` uses `etl_mode` so it does not clash with the simulator's `mode`.
- **The Apps UI has no field for the command, env vars or source path.** They come from `app.yaml` and the Deploy button.
- **The `params_catalog` argument is ignored by the Delta backend**, which is why it was left off while the ETL ran on Delta. It is required for the Lakebase backend.
- **Lakebase hierarchy**: project, then branch (`production`), then endpoint (`primary`, the compute), role and database, then schema and tables. The Unity Catalog registration is a separate object pointing at one database.
- **Portal lists lag**, the Jobs list has no refresh button, `bundle destroy` needs an interactive terminal, and `destroy` deletes tables in a bundle-managed schema even if the bundle did not create them.
- **`databricks workspace import-dir` turns `.py` files into notebooks**, so it cannot upload an app.
- **`uv run` fails inside the Claude Code sandbox** (cache write blocked). Run dependency-free scripts with `.venv/bin/python`.
- **A bundle cannot refer to its own outputs**, which is why the self-stop grant is a one-off script.
- **The app page does not show it has stopped until refreshed.**

## 5. Where the docs are out of step with the work

- `docs/SPEC.md`: §9.3 describes the workflow, but the file-tree sketch in §8 is stale, and spec §5.1, §6.5 and BUILD_PLAN workstream 2 still mention `infra/` (Terraform is in az-inf).
- The README deploy commands and its status section still describe the old single dev bundle and the Delta parameter store.
- `CLAUDE.md` has a stale "Known differences" line about venv and `requirements.txt` (the project uses uv).
