# Bakehouse Databricks PoC

A small, low-cost learning PoC on Azure Databricks. It builds a bakery franchise analytics platform on `samples.bakehouse`, with a simulator that releases one virtual day of data at a time.

It exercises Python ETL jobs, Lakeflow Designer, Databricks Apps (a Dash app that maintains 10 business parameters, backed by Lakebase or Delta), a reusable Python package and Declarative Automation Bundles. Everything uses synthetic or public sample data.

- Design: [`docs/SPEC.md`](docs/SPEC.md)
- Build order: [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md)
- Decisions and departures from the spec: [`docs/decisions.md`](docs/decisions.md)
- Changes needed in the separate Terraform project: [`docs/az-inf-requirements.md`](docs/az-inf-requirements.md)

## Status

Early: the `bakehouse_core` package skeleton and CI exist. The bundle, ETL and app are not built yet.

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

To be filled in as the bundle is built. Commands use the `personal` Databricks CLI profile (`-p personal`), and no workspace identifiers are stored in the repo.
