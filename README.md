# Bakehouse Databricks PoC

A small, low-cost learning PoC on Azure Databricks. It builds a bakery franchise analytics platform on `samples.bakehouse`, with a simulator that releases one virtual day of data at a time.

It exercises Python ETL jobs, Lakeflow Designer, Databricks Apps (a Dash app that maintains 10 business parameters, backed by Lakebase or Delta), a reusable Python package and Declarative Automation Bundles. Everything uses synthetic or public sample data.

- Design: [`docs/SPEC.md`](docs/SPEC.md)
- Build order: [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md)
- Decisions and departures from the spec: [`docs/decisions.md`](docs/decisions.md)
- Changes needed in the separate Terraform project: [`docs/az-inf-requirements.md`](docs/az-inf-requirements.md)

## Status

Early. Built and working on Databricks: the `dev` bundle (schemas, volumes), the simulator and the reset job. Built and tested locally, not yet run on Databricks: the ETL (parameters, silver, gold) and the Delta parameter store. Built and tested locally: the SQLite parameter store and its backend factory. Not built yet: the app, the Lakebase parameter backend, Designer and the notebooks.

## Local development

The project uses [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
uv sync                  # create ./.venv and install everything from uv.lock
uv run pytest            # run the tests
uv run ruff check .      # lint
uv run ruff format .     # format
uv add <package>         # add a dependency (updates pyproject.toml and uv.lock)
```

Tests run without cloud access. Tests that need Spark or Databricks are marked `spark` or `databricks`, so `uv run pytest -m "not spark and not databricks"` skips them.

## Variables, deploy and destroy

Commands use the `personal` Databricks CLI profile (`-p personal`), and no workspace identifiers are stored in the repo.

Variables (defaults are placeholders, override with `--var` or `BUNDLE_VAR_<name>`):

| Variable | Meaning |
|---|---|
| `catalog` | Unity Catalog catalog for the target |
| `idle_stop_minutes` | Idle minutes before anything that bills while running stops |
| `job_timeout_seconds` | Run timeout for jobs |
| `job_duration_warning_seconds` | A run longer than this raises a health warning |

```bash
databricks bundle validate -t dev -p personal
databricks bundle plan -t dev -p personal
databricks bundle deploy -t dev -p personal

# Release one virtual day, or several, optionally with scenarios switched on
databricks bundle run simulator -t dev -p personal
databricks bundle run simulator -t dev -p personal --params 'days=3,scenarios=duplicates;null_spike'

# Run the ETL on what has been released (incremental, or mode=recompute_gold)
databricks bundle run bakehouse_etl -t dev -p personal
databricks bundle run bakehouse_etl -t dev -p personal --params mode=recompute_gold

# Release a day, then run the ETL on it
databricks bundle run daily_run -t dev -p personal

# Back to virtual day 0 (confirm must equal the target name)
databricks bundle run ops_reset -t dev -p personal --params level=data,confirm=dev
```

Scenarios: `late_files`, `duplicates`, `null_spike`, `underperforming_franchise`, `negative_review_burst`.

The jobs run on serverless compute. `daily_run` has a schedule, but it is paused, so nothing runs or costs money until you start it. `databricks bundle destroy -t dev -p personal` removes everything the bundle created.
