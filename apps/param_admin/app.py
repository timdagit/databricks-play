"""Entry point for the parameter admin app: reads the environment and builds the page.

Run locally with `uv run python apps/param_admin/app.py`, or in the container with
`gunicorn app:server`. All settings come from environment variables:

- `PARAM_BACKEND`: sqlite, postgres or lakebase (see `bakehouse_core.params.factory`). The
  `lakebase` backend also needs `LAKEBASE_ENDPOINT`, the compute's resource name, and the
  `PG*` variables that Databricks Apps sets for its postgres resource.
- `JOB_TRIGGER`: `fake` (default) simulates runs, `databricks` starts the real jobs and needs
  `ETL_JOB_ID` and `DAILY_RUN_JOB_ID`
- `APP_USER`: the name recorded in `updated_by` when no user header is present
- `IDLE_ENABLED`: `true` makes `/idle` report that the idle watchdog is on
"""

from __future__ import annotations

import os
from pathlib import Path

from bakehouse_core.config.identity import LakebaseTokenProvider
from bakehouse_core.jobs.trigger import DatabricksJobTrigger, FakeJobTrigger, JobTrigger
from bakehouse_core.params.factory import get_repository
from bakehouse_core.webapp import create_app


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

app = create_app(
    repo,
    _trigger(),
    default_user=os.environ.get("APP_USER", "local-user"),
    backend_name=backend,
    idle_enabled=os.environ.get("IDLE_ENABLED", "false").lower() == "true",
    assets_folder=str(Path(__file__).parent / "assets"),
)
server = app.server  # what gunicorn serves

if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8050")),
        debug=False,
    )
