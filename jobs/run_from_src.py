"""Run one bakehouse_core job from source. Used by the hand-built dev jobs (spec 9.3).

In dev a job is a `spark_python_task` that runs this file from the workspace Git folder, with the
job name first and then the job's own parameters:

    run_from_src.py silver-build --catalog bakehouse_dev --run-id {{job.run_id}}

Sandbox and above run the built wheel through `python_wheel_task` entry points instead. The names
here are those entry points without the `bakehouse-` prefix, and a test keeps the two in step.

This folder is not the `bakehouse_core.jobs` package. That holds the job code; this file only
finds it and starts it.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

# Job name -> module with a `main(argv)`. Matches [project.scripts] in pyproject.toml.
JOBS = {
    "simulate": "bakehouse_core.jobs.simulate",
    "reset": "bakehouse_core.jobs.reset",
    "load-params": "bakehouse_core.jobs.load_params",
    "silver-build": "bakehouse_core.jobs.silver_build",
    "gold-build": "bakehouse_core.jobs.gold_build",
}


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in JOBS:
        raise SystemExit(f"usage: run_from_src.py <{'|'.join(JOBS)}> [job arguments]")

    sys.path.insert(0, str(src_dir()))
    importlib.import_module(JOBS[argv[0]]).main(argv[1:])


def src_dir() -> Path:
    """The repo's `src` folder, which is not on the path when this file is run as a script.

    Serverless runs the file with `exec(compile(source, filename, "exec"))`, so `__file__` is not
    defined there. The code object still carries the real path, which works either way.
    """
    return Path(main.__code__.co_filename).resolve().parents[1] / "src"


if __name__ == "__main__":
    main()
