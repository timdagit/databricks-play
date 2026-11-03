"""The simulator job: release one or more virtual days into the raw volume."""

from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

from bakehouse_core.jobs.store import OpsStore, RunLogEntry
from bakehouse_core.simulator import clock, generate, scenarios, writer
from bakehouse_core.simulator.generate import SourceData


@dataclass(frozen=True)
class SimulationResult:
    run_id: str
    dates: list[date]
    rows: dict[str, int]
    clock: clock.ClockState


def run_simulation(
    store: OpsStore,
    source: SourceData,
    raw_dir: str,
    *,
    days: int = 1,
    mode: clock.Mode = "auto",
    scenario_names: list[str] | None = None,
    triggered_by: str = "manual",
    run_id: str | None = None,
) -> SimulationResult:
    """Release the next `days` days and advance the clock.

    The clock only moves once every day has been written. If a run fails part way, the files it
    wrote are overwritten when the same days are released again, so a retry is safe.
    """
    run_id = run_id or uuid.uuid4().hex
    started = datetime.now(UTC)
    store.ensure_tables()
    names = scenario_names or []
    scenarios.parse_scenarios(",".join(names))  # fail on a typo before doing any work

    state = store.read_clock() or clock.ClockState.initial()
    details: dict = {"days": days, "mode": mode, "scenarios": names, "seed": state.seed}
    released: list[date] = []
    try:
        plans = clock.plan_days(state, days, mode)
        totals = {e: 0 for e in generate.ENTITIES}
        for plan in plans:
            day = generate.generate_day(plan, state.seed, source)
            day = scenarios.apply_scenarios(day, plan, state.seed, source, names)
            writer.delete_day(plan.virtual_date, raw_dir)
            writer.write_day(day, plan.virtual_date, raw_dir)
            for entity, rows in day.items():
                totals[entity] += len(rows)
            released.append(plan.virtual_date)
        new_state = clock.advance(state, plans)
        store.write_clock(new_state)
        details |= {"rows": totals, "phases": sorted({p.phase for p in plans})}
        status = "succeeded"
    except Exception as exc:
        details |= {
            "error": f"{type(exc).__name__}: {exc}",
            "released_before_failure": len(released),
        }
        store.log_run(_entry(run_id, "failed", started, state.virtual_date, triggered_by, details))
        raise

    store.log_run(_entry(run_id, status, started, new_state.virtual_date, triggered_by, details))
    return SimulationResult(run_id, released, totals, new_state)


def _entry(run_id, status, started, virtual_date, triggered_by, details) -> RunLogEntry:
    return RunLogEntry(
        run_id=run_id,
        job="simulator",
        status=status,
        started_at=started,
        finished_at=datetime.now(UTC),
        virtual_date=virtual_date,
        triggered_by=triggered_by,
        details=json.dumps(details, default=str),
    )


def main(argv: list[str] | None = None) -> None:
    """Entry point for the wheel task (`bakehouse-simulate`)."""
    parser = argparse.ArgumentParser(description="Release virtual days of raw data.")
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--mode", default="auto", choices=["auto", "replay", "synthetic"])
    parser.add_argument("--scenarios", default="")
    parser.add_argument("--triggered-by", default="manual")
    args = parser.parse_args(argv)

    from pyspark.sql import SparkSession

    from bakehouse_core.io.ops_tables import SparkOpsStore
    from bakehouse_core.io.samples import load_source

    spark = SparkSession.builder.getOrCreate()
    result = run_simulation(
        SparkOpsStore(spark, args.catalog),
        load_source(spark),
        f"/Volumes/{args.catalog}/bronze/raw",
        days=args.days,
        mode=args.mode,
        scenario_names=scenarios.parse_scenarios(args.scenarios),
        triggered_by=args.triggered_by,
    )
    print(f"Released {len(result.dates)} day(s) up to {result.clock.virtual_date}: {result.rows}")
