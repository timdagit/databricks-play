"""The `gold_build` task: build the six gold tables, one snapshot per virtual day.

`incremental` builds the days after the last successful run, up to the clock date.
`recompute_gold` rebuilds every day from the start. Either way each day uses the parameters in
force on that day: the clock date uses the run's `load_params` snapshot, earlier days read the
parameter store as of their own date.
"""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from bakehouse_core.io.gold_tables import GoldTables
from bakehouse_core.jobs.store import OpsStore, RunLogEntry
from bakehouse_core.params import model, snapshot
from bakehouse_core.params.repository import ParamRepository
from bakehouse_core.simulator.clock import START_DATE
from bakehouse_core.transforms import gold

JOB = "gold_build"
LOAD_PARAMS_JOB = "load_params"
MODES = ("incremental", "recompute_gold")


def days_to_build(
    mode: str, clock_date: date, last_built: date | None, from_date: date | None = None
) -> list[date]:
    """The virtual days to build, oldest first.

    `incremental` builds the days after the last successful run. `recompute_gold` builds every
    day, or only the days from `from_date` when it is given (a changed parameter only affects
    days from its effective date). `from_date` is ignored by `incremental`, and a date before the
    first day just means the first day.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}, got {mode!r}")
    if mode == "incremental" and last_built is not None:
        start = last_built + timedelta(days=1)
    elif mode == "recompute_gold" and from_date is not None:
        start = max(from_date, START_DATE)
    else:
        start = START_DATE
    count = (clock_date - start).days + 1
    return [start + timedelta(days=i) for i in range(max(count, 0))]


def build_day(
    transactions: Any,
    reviews: Any,
    franchises: Any,
    suppliers: Any,
    as_of: date,
    params: dict[str, model.Value],
) -> dict[str, Any]:
    """The six gold DataFrames for one day, keyed by table name."""
    return {
        "franchise_daily_kpis": gold.franchise_daily_kpis(
            transactions, as_of, params["promo_discount_rate"]
        ),
        "franchise_flags": gold.franchise_flags(
            transactions, as_of, params["underperform_pct"], int(params["min_txn_for_ranking"])
        ),
        "customer_loyalty_tiers": gold.customer_loyalty_tiers(
            transactions,
            as_of,
            params["loyalty_gold_min_spend"],
            params["loyalty_silver_min_spend"],
            int(params["loyalty_lookback_days"]),
        ),
        "supplier_concentration": gold.supplier_concentration(
            transactions, franchises, suppliers, as_of, params["supplier_concentration_threshold"]
        ),
        "review_alerts": gold.review_alerts(reviews, as_of, int(params["review_alert_min_count"])),
        "sales_vs_target": gold.sales_vs_target(
            transactions, as_of, params["sales_target_uplift_pct"]
        ),
    }


def run_gold_build(
    spark: Any,
    store: OpsStore,
    repo: ParamRepository,
    catalog: str,
    *,
    mode: str = "incremental",
    from_date: date | None = None,
    run_id: str | None = None,
    triggered_by: str = "manual",
) -> dict[str, Any]:
    run_id = run_id or uuid.uuid4().hex
    started = datetime.now(UTC)
    state = store.read_clock()
    details: dict[str, Any] = {"mode": mode}
    if from_date is not None:
        details["from_date"] = from_date.isoformat()
    status = "failed"
    try:
        if state is None:
            raise RuntimeError("no virtual day has been released yet; run the simulator first")
        params_entry = store.find_run(run_id, LOAD_PARAMS_JOB)
        if params_entry is None:
            raise RuntimeError(f"no load_params entry for run {run_id}; run load_params first")
        run_params = snapshot.values_from_details(params_entry.details)

        days = days_to_build(mode, state.virtual_date, store.last_success_date(JOB), from_date)
        details["days"] = [d.isoformat() for d in days]
        tables = GoldTables(spark, catalog)
        tables.ensure_tables()
        transactions = spark.table(f"{catalog}.silver.transactions")
        reviews = spark.table(f"{catalog}.silver.reviews")
        franchises = spark.table(f"{catalog}.silver.franchises")
        suppliers = spark.table(f"{catalog}.silver.suppliers")
        versions: dict[str, dict[str, int]] = {}
        for day in days:
            params = run_params if day == state.virtual_date else repo.load_as_of(day)
            versions[day.isoformat()] = repo.versions_as_of(day)
            for table, df in build_day(
                transactions, reviews, franchises, suppliers, day, params
            ).items():
                tables.replace_day(table, df, day)
        details["parameter_versions"] = versions
        details["row_counts"] = tables.row_counts()
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the gold tables.")
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--mode", default="incremental", choices=MODES)
    parser.add_argument("--param-backend", default="lakebase", choices=["delta", "lakebase"])
    parser.add_argument("--params-catalog", default="")
    parser.add_argument(
        "--from-date", default="", help="recompute_gold only: first day to rebuild (YYYY-MM-DD)"
    )
    parser.add_argument("--run-id", default="")
    parser.add_argument("--triggered-by", default="manual")
    return parser


def main(argv: list[str] | None = None) -> None:
    """Entry point for the wheel task (`bakehouse-gold-build`)."""
    args = build_parser().parse_args(argv)

    from pyspark.sql import SparkSession

    from bakehouse_core.io.ops_tables import SparkOpsStore
    from bakehouse_core.params.factory import spark_repository

    spark = SparkSession.builder.getOrCreate()
    details = run_gold_build(
        spark,
        SparkOpsStore(spark, args.catalog),
        spark_repository(spark, args.param_backend, args.catalog, args.params_catalog),
        args.catalog,
        mode=args.mode,
        from_date=date.fromisoformat(args.from_date) if args.from_date else None,
        run_id=args.run_id or None,
        triggered_by=args.triggered_by,
    )
    print(json.dumps(details, default=str))
