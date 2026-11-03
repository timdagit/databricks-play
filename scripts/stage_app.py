"""Copy the `bakehouse_core` package into the app folder before a bundle deploy.

A Databricks app only gets the files in its own folder, so the shared package has to be
inside it. The copy is generated and gitignored. The bundle runs this on every deploy (it is
part of the `bakehouse_core` artifact build), so it always matches the deployed jobs.

Heads up for local work: with this copy present, `python apps/param_admin/app.py` imports it
instead of `src/`. Delete `apps/param_admin/bakehouse_core` if you want local edits picked up.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "src" / "bakehouse_core"
TARGET = ROOT / "apps" / "param_admin" / "bakehouse_core"


def stage(source: Path = SOURCE, target: Path = TARGET) -> int:
    """Replace `target` with a clean copy of `source`. Returns the number of files copied."""
    if not source.is_dir():
        raise FileNotFoundError(f"package not found: {source}")
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return sum(1 for p in target.rglob("*") if p.is_file())


if __name__ == "__main__":
    count = stage()
    print(f"Staged {count} files into {TARGET.relative_to(ROOT)}", file=sys.stderr)
