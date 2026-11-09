"""Entry point for the parameter admin app: reads the environment and builds the page.

Run locally with `uv run python apps/param_admin/app.py`, or in the container with
`gunicorn app:server`. All settings come from environment variables:

- `PARAM_BACKEND`: sqlite, postgres or lakebase (see `bakehouse_core.params.factory`). The
  `lakebase` backend also needs `LAKEBASE_ENDPOINT`, the compute's resource name, and the
  `PG*` variables that Databricks Apps sets for its postgres resource.
- `JOB_TRIGGER`: `fake` (default) simulates runs, `databricks` starts the real jobs and needs
  `ETL_JOB_ID` and `DAILY_RUN_JOB_ID`
- `APP_USER`: the name recorded in `updated_by` when no user header is present
- `IDLE_STOP_MINUTES`: stop the app after this many idle minutes (0, the default, is off). It
  stops itself through the Apps API, using `APP_NAME` (set by the bundle) or else the
  platform's `DATABRICKS_APP_NAME`.
  `IDLE_STARTUP_GRACE_SECONDS` (default 180) delays the first possible stop.
"""

from __future__ import annotations

import os
from pathlib import Path

from stale_copy import check as refuse_stale_copy

# Before the package is imported, so a stale staged copy is caught rather than used.
refuse_stale_copy(Path(__file__).resolve().parent)

from bakehouse_core.config.identity import LakebaseTokenProvider  # noqa: E402
from bakehouse_core.jobs.trigger import (  # noqa: E402
    DatabricksJobTrigger,
    FakeJobTrigger,
    JobTrigger,
)
from bakehouse_core.params.factory import get_repository  # noqa: E402
from bakehouse_core.webapp import create_app  # noqa: E402


def _trigger() -> JobTrigger:
    kind = os.environ.get("JOB_TRIGGER", "fake").lower()
    if kind == "fake":
        return FakeJobTrigger()
    if kind == "databricks":
        from databricks.sdk import WorkspaceClient

        # On Databricks Apps the job ids come from the app's job resources. Locally, take them
        # from `databricks bundle summary`.
        job_ids = {
            "bakehouse_etl": int(os.environ["ETL_JOB_ID"]),
            "daily_run": int(os.environ["DAILY_RUN_JOB_ID"]),
        }
        return DatabricksJobTrigger(WorkspaceClient(), job_ids)
    raise ValueError(f"JOB_TRIGGER={kind!r} is not known (use 'fake' or 'databricks')")


def _token_provider(backend: str):
    if backend != "lakebase":
        return None
    from databricks.sdk import WorkspaceClient

    return LakebaseTokenProvider(WorkspaceClient(), os.environ["LAKEBASE_ENDPOINT"])


backend = os.environ.get("PARAM_BACKEND", "sqlite").lower()
repo = get_repository(
    {**os.environ, "PARAM_BACKEND": backend}, token_provider=_token_provider(backend)
)
repo.ensure_schema()


def _stop_app():
    """Stop this app through the Apps API (the app's service principal needs CAN_MANAGE)."""
    from databricks.sdk import WorkspaceClient

    # APP_NAME is set by the bundle. DATABRICKS_APP_NAME is the platform's own variable.
    name = os.environ.get("APP_NAME") or os.environ["DATABRICKS_APP_NAME"]
    return WorkspaceClient().apps.stop(name)


idle_minutes = float(os.environ.get("IDLE_STOP_MINUTES", "0") or 0)

app = create_app(
    repo,
    _trigger(),
    default_user=os.environ.get("APP_USER", "local-user"),
    backend_name=backend,
    idle_stop_seconds=idle_minutes * 60,
    idle_grace_seconds=float(os.environ.get("IDLE_STARTUP_GRACE_SECONDS", "180")),
    stop_app=_stop_app if idle_minutes > 0 else None,
    assets_folder=str(Path(__file__).parent / "assets"),
)
server = app.server  # what gunicorn serves

if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8050")),
        debug=False,
    )
