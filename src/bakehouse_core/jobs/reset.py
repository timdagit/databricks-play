"""The reset job: return a target to a known starting state (spec section 3.6).

- `data`: raw files, silver and gold tables (including quarantine), checkpoints and the run
  log are cleared, and the clock goes back to virtual day 0. Parameters are kept.
- `hard`: everything in `data`, plus the parameters are reseeded and `ops.app_metrics` is
  cleared.

Tables are truncated, never dropped, so grants, lineage and app bindings survive.
"""

from __future__ import annotations

import argparse
import json
import shutil
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from bakehouse_core.jobs.store import OpsStore, ResetLogEntry
from bakehouse_core.simulator.clock import ClockState

LEVELS = ("data", "hard")
DATA_SCHEMAS = ["silver", "gold"]  # quarantine tables live in silver


@dataclass(frozen=True)
class ResetResult:
    reset_id: str
    level: str
    steps: list[str]


def _empty_directory(path: Path) -> int:
    """Remove everything inside `path`, keeping the folder. Returns how many items went."""
    if not path.is_dir():
        return 0
    items = list(path.iterdir())
    for item in items:
        shutil.rmtree(item) if item.is_dir() else item.unlink()
    return len(items)


def run_reset(
    store: OpsStore,
    *,
    level: str,
    target: str,
    confirm: str,
    raw_dir: str,
    checkpoints_dir: str,
    requested_by: str = "manual",
    allow_prod: bool = False,
    reseed_params: Sequence[Callable[[], None]] = (),
) -> ResetResult:
    """Reset the target. Refuses, without touching anything, unless the guards pass.

    `reseed_params` holds one callable per parameter backend that should be reset to the
    version 1 defaults on a hard reset.
    """
    if level not in LEVELS:
        raise ValueError(f"level must be one of {', '.join(LEVELS)}, got {level!r}")
    if confirm != target:
        raise ValueError(f"confirm must equal the target name ({target!r}); nothing was reset")
    if target == "prod" and level == "hard" and not allow_prod:
        raise ValueError("a hard reset of prod needs allow_prod=true; nothing was reset")

    reset_id = uuid.uuid4().hex
    steps: list[str] = []
    store.ensure_tables()
    try:
        previous = store.read_clock()
        steps.append(f"raw files removed: {_empty_directory(Path(raw_dir))} item(s)")
        steps.append(f"checkpoints removed: {_empty_directory(Path(checkpoints_dir))} item(s)")
        steps.append(
            f"tables truncated: {', '.join(store.truncate_tables(DATA_SCHEMAS)) or 'none'}"
        )
        store.clear_run_log()
        steps.append("run log cleared")
        seed = previous.seed if previous else ClockState.initial().seed
        store.write_clock(ClockState.initial(seed))
        steps.append("clock reset to virtual day 0")

        if level == "hard":
            for reseed in reseed_params:
                reseed()
            steps.append(f"parameter backends reseeded: {len(reseed_params)}")
            existed = store.truncate_ops_table("app_metrics")
            steps.append("app_metrics cleared" if existed else "app_metrics not present")
        status = "completed"
    except Exception as exc:
        steps.append(f"failed: {type(exc).__name__}: {exc}")
        _log(store, reset_id, level, target, requested_by, "failed", steps)
        raise

    _log(store, reset_id, level, target, requested_by, status, steps)
    return ResetResult(reset_id, level, steps)


def _log(store, reset_id, level, target, requested_by, status, steps) -> None:
    store.log_reset(
        ResetLogEntry(
            reset_id=reset_id,
            reset_at=datetime.now(UTC),
            level=level,
            target=target,
            requested_by=requested_by,
            status=status,
            details=json.dumps(steps),
        )
    )


def main(argv: list[str] | None = None) -> None:
    """Entry point for the wheel task (`bakehouse-reset`)."""
    parser = argparse.ArgumentParser(description="Reset a target to a known starting state.")
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--level", default="data", choices=LEVELS)
    parser.add_argument("--confirm", default="")
    parser.add_argument("--requested-by", default="manual")
    parser.add_argument("--allow-prod", action="store_true")
    args = parser.parse_args(argv)

    from pyspark.sql import SparkSession

    from bakehouse_core.io.ops_tables import SparkOpsStore

    spark = SparkSession.builder.getOrCreate()
    result = run_reset(
        SparkOpsStore(spark, args.catalog),
        level=args.level,
        target=args.target,
        confirm=args.confirm,
        raw_dir=f"/Volumes/{args.catalog}/bronze/raw",
        checkpoints_dir=f"/Volumes/{args.catalog}/ops/checkpoints",
        requested_by=args.requested_by,
        allow_prod=args.allow_prod,
    )
    print(f"Reset ({result.level}) completed:")
    for step in result.steps:
        print(f"  - {step}")
