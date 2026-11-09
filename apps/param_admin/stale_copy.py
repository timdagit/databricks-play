"""Refuse to run locally against an out-of-date staged copy of `bakehouse_core`.

`scripts/stage_app.py` copies the package into this folder for deploys. Run as a script, the app
puts this folder first on `sys.path`, so that copy wins over `src/` and local edits seem to have
no effect. On Databricks the copy is the point, so the check is skipped there (the platform sets
`DATABRICKS_APP_PORT`). It also does nothing without a `src/` next to the app, as in the container.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

PACKAGE = "bakehouse_core"


def _files(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    }


def differs(copy: Path, source: Path) -> bool:
    return _files(copy) != _files(source)


def check(app_dir: Path, env: Mapping[str, str] = os.environ) -> None:
    """Exit with a message if the staged copy differs from `src/bakehouse_core`."""
    if env.get("DATABRICKS_APP_PORT"):
        return
    copy = app_dir / PACKAGE
    source = app_dir.parents[1] / "src" / PACKAGE
    if copy.is_dir() and source.is_dir() and differs(copy, source):
        raise SystemExit(
            f"The staged copy {copy} differs from {source}, so the app would run old code.\n"
            f"Delete it (rm -rf {copy}) or refresh it with `python scripts/stage_app.py`."
        )
