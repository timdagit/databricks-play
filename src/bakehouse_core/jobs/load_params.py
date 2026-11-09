"""The `load_params` task: read the parameters as of the virtual date, once per ETL run.

The values and versions go into `ops.run_log` under this run's id. The later tasks read them
from there, so a parameter edit during a run cannot make the tasks disagree.
"""

from __future__ import annotations

import argparse
import uuid
from datetime import UTC, datetime

from bakehouse_core.jobs.store import OpsStore, RunLogEntry
from bakehouse_core.params import snapshot
from bakehouse_core.params.repository import ParamRepository

JOB = "load_params"


class NoVirtualDateError(RuntimeError):
    """The simulator has not run yet, so there is no virtual date to read parameters for."""


def run_load_params(
    store: OpsStore,
    repo: ParamRepository,
    *,
    run_id: str | None = None,
    triggered_by: str = "manual",
) -> RunLogEntry:
    run_id = run_id or uuid.uuid4().hex
    started = datetime.now(UTC)
    store.ensure_tables()
    state = store.read_clock()
    if state is None or state.days_released == 0:
        raise NoVirtualDateError("no virtual day has been released yet; run the simulator first")
    as_of = state.virtual_date
    values = repo.load_as_of(as_of)
    versions = repo.versions_as_of(as_of)
    entry = RunLogEntry(
        run_id=run_id,
        job=JOB,
        status="succeeded",
        started_at=started,
        finished_at=datetime.now(UTC),
        virtual_date=as_of,
        triggered_by=triggered_by,
        details=snapshot.to_json(
            as_of, values, versions, defaults_used=getattr(repo, "used_defaults", False)
        ),
    )
    store.log_run(entry)
    return entry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record the parameters for this ETL run.")
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--param-backend", default="lakebase", choices=["delta", "lakebase"])
    parser.add_argument("--params-catalog", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--triggered-by", default="manual")
    return parser


def main(argv: list[str] | None = None) -> None:
    """Entry point for the wheel task (`bakehouse-load-params`)."""
    args = build_parser().parse_args(argv)

    from pyspark.sql import SparkSession

    from bakehouse_core.io.ops_tables import SparkOpsStore
    from bakehouse_core.params.factory import spark_repository

    spark = SparkSession.builder.getOrCreate()
    repo = spark_repository(spark, args.param_backend, args.catalog, args.params_catalog)
    repo.ensure_schema()
    entry = run_load_params(
        SparkOpsStore(spark, args.catalog),
        repo,
        run_id=args.run_id or None,
        triggered_by=args.triggered_by,
    )
    print(f"Parameters as of {entry.virtual_date}: {entry.details}")
