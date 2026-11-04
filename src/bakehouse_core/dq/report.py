"""Reads the data-quality numbers that `silver_build` saves in `ops.run_log.details`.

Plain Python, no Spark: the DQ report notebook passes in the `details` text of each run and gets
back flat rows to show. A run that failed, or has no details, gives no rows.
"""

from __future__ import annotations

import json
from typing import Any


def parse_silver_details(details: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split one `silver_build` run's details into `(batch_rows, null_rate_rows)`.

    A batch row has `entity`, `batch`, `rows_in`, `applied`, `quarantined`, `already_seen` and
    `reasons` (a `reason=count` text). `applied` and `already_seen` are `None` for the SCD2
    entities, which do not report them. `already_seen` is what the batch held but silver did not
    take: duplicates inside the batch, or rows from an earlier file.
    A null-rate row has `entity`, `batch`, `column` and `null_rate`.
    """
    if not details:
        return [], []
    try:
        parsed = json.loads(details)
    except json.JSONDecodeError:
        return [], []
    if not isinstance(parsed, dict):
        return [], []

    batch_rows: list[dict[str, Any]] = []
    null_rows: list[dict[str, Any]] = []
    for entity, body in parsed.items():
        if not isinstance(body, dict):  # the "error" entry is text
            continue
        for number, stats in enumerate(body.get("batches", [])):
            rows_in = stats.get("rows_in")
            applied = stats.get("applied")
            quarantined = stats.get("quarantined", 0)
            seen = None if applied is None or rows_in is None else rows_in - applied - quarantined
            reasons = stats.get("reasons") or {}
            batch_rows.append(
                {
                    "entity": entity,
                    "batch": number,
                    "rows_in": rows_in,
                    "applied": applied,
                    "quarantined": quarantined,
                    "already_seen": seen,
                    "reasons": ", ".join(f"{r}={n}" for r, n in sorted(reasons.items())),
                }
            )
            for column, rate in (stats.get("null_rates") or {}).items():
                null_rows.append(
                    {"entity": entity, "batch": number, "column": column, "null_rate": float(rate)}
                )
    return batch_rows, null_rows
