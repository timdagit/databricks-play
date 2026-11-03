"""Entry point for the parameter admin app: reads the environment and builds the page.

Run locally with `uv run python apps/param_admin/app.py`, or in the container with
`gunicorn app:server`. All settings come from environment variables:

- `PARAM_BACKEND`: sqlite, postgres, lakebase or delta (see `bakehouse_core.params.factory`)
- `JOB_TRIGGER`: `fake` (default) simulates runs, `databricks` starts the real jobs
- `APP_USER`: the name recorded in `updated_by` when no user header is present
- `IDLE_ENABLED`: `true` makes `/idle` report that the idle watchdog is on
"""

from __future__ import annotations

import os
from pathlib import Path

from bakehouse_core.config.identity import StaticTokenProvider
from bakehouse_core.jobs.trigger import FakeJobTrigger, JobTrigger
from bakehouse_core.params.factory import get_repository
from bakehouse_core.webapp import create_app


def _trigger() -> JobTrigger:
    kind = os.environ.get("JOB_TRIGGER", "fake").lower()
    if kind == "fake":
        return FakeJobTrigger()
    raise ValueError(f"JOB_TRIGGER={kind!r} is not available yet (only 'fake')")


backend = os.environ.get("PARAM_BACKEND", "sqlite").lower()
repo = get_repository(
    {**os.environ, "PARAM_BACKEND": backend},
    token_provider=StaticTokenProvider.from_env() if backend == "lakebase" else None,
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
