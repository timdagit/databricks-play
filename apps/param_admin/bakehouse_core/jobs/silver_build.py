"""The `silver_build` task: Auto Loader from the raw volume into the silver tables.

Each entity is read with `trigger(availableNow=True)`, so one run processes every new file and
stops. Checkpoints live in the `ops` volume, which `ops_reset` empties. `process_batch` holds
the per-batch logic and is tested locally. `run_silver_build` wires it to Auto Loader.
"""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pyspark.sql import functions as F

from bakehouse_core.io.silver_tables import SPECS, SilverTables, TableSpec
from bakehouse_core.jobs.store import OpsStore, RunLogEntry
from bakehouse_core.params import snapshot
from bakehouse_core.transforms import scd2, silver

JOB = "silver_build"
LOAD_PARAMS_JOB = "load_params"


def write_batch_stats(stats_dir: str, entity: str, batch_id: int, stats: dict[str, Any]) -> None:
    """Save one batch's counts as a file.

    On serverless, `foreachBatch` runs in a separate Python process, so the handler cannot hand
    results back through a variable. Files in the checkpoints volume can be read afterwards.
    """
    folder = Path(stats_dir)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{entity}-{batch_id:06d}.json").write_text(json.dumps(stats, default=str))


def read_batch_stats(stats_dir: str, entity: str) -> list[dict[str, Any]]:
    """The saved counts for an entity, oldest batch first."""
    folder = Path(stats_dir)
    if not folder.exists():
        return []
    return [json.loads(f.read_text()) for f in sorted(folder.glob(f"{entity}-*.json"))]


def process_batch(
    tables: Any, spec: TableSpec, df: Any, max_null_pct: Decimal | float
) -> dict[str, Any]:
    """Clean one micro-batch (raw strings plus ingest metadata) and apply it to silver.

    Returns counts for the run log.
    """
    if "_rescued_data" not in df.columns:
        df = df.withColumn("_rescued_data", F.lit(None).cast("string"))
    stats: dict[str, Any] = {"rows_in": df.count()}

    if spec.entity == "transactions":
        good, bad, rates = silver.build_transactions(df, max_null_pct)
        stats["null_rates"] = rates
        stats |= _apply_with_quarantine(tables, spec, good, bad)
    elif spec.entity == "reviews":
        good, bad = silver.build_reviews(df)
        stats |= _apply_with_quarantine(tables, spec, good, bad)
    elif spec.kind == "upsert":
        typed = silver.cast_columns(df, spec.types).filter(F.col(spec.key).isNotNull())
        good = silver.deduplicate(typed, [spec.key])
        tables.apply(spec, good)
        stats["applied"] = good.count()
    else:
        stats |= _apply_scd2_by_day(tables, spec, df)
    return stats


def _apply_with_quarantine(tables: Any, spec: TableSpec, good: Any, bad: Any) -> dict[str, Any]:
    tables.apply(spec, good)
    tables.append_quarantine(spec, bad)
    reasons = {r["_dq_reason"]: r["count"] for r in bad.groupBy("_dq_reason").count().collect()}
    return {"applied": good.count(), "quarantined": sum(reasons.values()), "reasons": reasons}


def _apply_scd2_by_day(tables: Any, spec: TableSpec, df: Any) -> dict[str, Any]:
    """One virtual day at a time, oldest first, so a backfill gives the same history as
    running the days one by one."""
    typed = silver.cast_columns(df, spec.types)
    keyed = typed.filter(F.col(spec.key).isNotNull())
    dropped = typed.count() - keyed.count()
    days = sorted(r[0] for r in keyed.select("_virtual_date").distinct().collect())
    for day in days:
        rows = silver.deduplicate(keyed.filter(F.col("_virtual_date") == F.lit(day)), [spec.key])
        staged = scd2.stage_scd2(rows, tables.current(spec), spec.key, spec.attributes)
        tables.apply_scd2(spec, staged)
    return {"days": [d.isoformat() for d in days], "dropped_null_key": dropped}


def _read_stream(spark: Any, raw_dir: str, checkpoints: str, entity: str) -> Any:
    stream = (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "json")
        .option("cloudFiles.inferColumnTypes", "false")  # everything as string, typed in silver
        .option("cloudFiles.schemaLocation", f"{checkpoints}/{entity}/schema")
        .option("cloudFiles.schemaEvolutionMode", "rescue")  # unexpected columns -> _rescued_data
        .load(f"{raw_dir}/{entity}")
    )
    if "date" in stream.columns:  # the date=... folder, also read from the file path
        stream = stream.drop("date")
    return silver.with_ingest_metadata(stream, F.col("_metadata.file_path"))


def run_silver_build(
    spark: Any,
    store: OpsStore,
    catalog: str,
    *,
    run_id: str | None = None,
    triggered_by: str = "manual",
) -> dict[str, Any]:
    run_id = run_id or uuid.uuid4().hex
    started = datetime.now(UTC)
    state = store.read_clock()
    details: dict[str, Any] = {}
    status = "failed"
    try:
        params_entry = store.find_run(run_id, LOAD_PARAMS_JOB)
        if params_entry is None:
            raise RuntimeError(f"no load_params entry for run {run_id}; run load_params first")
        max_null_pct = snapshot.values_from_details(params_entry.details)["dq_max_null_pct"]
        tables = SilverTables(spark, catalog)
        tables.ensure_tables()
        raw_dir = f"/Volumes/{catalog}/bronze/raw"
        checkpoints = f"/Volumes/{catalog}/ops/checkpoints"
        stats_dir = f"{checkpoints}/_stats/{run_id}"
        for spec in SPECS.values():

            def handle(batch_df, batch_id, spec=spec):
                if not batch_df.isEmpty():
                    stats = process_batch(tables, spec, batch_df, max_null_pct)
                    write_batch_stats(stats_dir, spec.entity, batch_id, stats)

            query = (
                _read_stream(spark, raw_dir, checkpoints, spec.entity)
                .writeStream.foreachBatch(handle)
                .option("checkpointLocation", f"{checkpoints}/{spec.entity}/stream")
                .trigger(availableNow=True)
                .start()
            )
            query.awaitTermination()
            details[spec.entity] = {"batches": read_batch_stats(stats_dir, spec.entity)}
        status = "succeeded"
    except Exception as exc:
        details["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        store.log_run(
            RunLogEntry(
                run_id=run_id,
                job=JOB,
                status=status,
                started_at=started,
                finished_at=datetime.now(UTC),
                virtual_date=state.virtual_date if state else None,
                triggered_by=triggered_by,
                details=json.dumps(details, default=str),
            )
        )
    return details


def main(argv: list[str] | None = None) -> None:
    """Entry point for the wheel task (`bakehouse-silver-build`)."""
    parser = argparse.ArgumentParser(description="Load new raw files into silver.")
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--triggered-by", default="manual")
    args = parser.parse_args(argv)

    from pyspark.sql import SparkSession

    from bakehouse_core.io.ops_tables import SparkOpsStore

    spark = SparkSession.builder.getOrCreate()
    details = run_silver_build(
        spark,
        SparkOpsStore(spark, args.catalog),
        args.catalog,
        run_id=args.run_id or None,
        triggered_by=args.triggered_by,
    )
    print(json.dumps(details, default=str))
