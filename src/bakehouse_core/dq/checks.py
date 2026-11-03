"""Data-quality checks (spec section 3.3). Plain DataFrame functions, no Databricks needed.

Every check returns `(good, bad)`. Bad rows keep their columns and gain `_dq_reason`, ready to
append to a quarantine table.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F

REASON = "_dq_reason"


def null_rates(df: DataFrame, columns: Sequence[str]) -> dict[str, float]:
    """Share of rows with a null in each column. An empty frame has rate 0."""
    if not columns:
        return {}
    row = df.agg(
        F.count(F.lit(1)).alias("_n"),
        *[F.sum(F.col(c).isNull().cast("int")).alias(c) for c in columns],
    ).first()
    total = row["_n"]
    return {c: (float(row[c] or 0) / total if total else 0.0) for c in columns}


def first_reason(*checks: tuple[Column, str]) -> Column:
    """The label of the first check whose condition is true, else null."""
    reason = F.lit(None).cast("string")
    for condition, label in reversed(checks):
        reason = F.when(condition, F.lit(label)).otherwise(reason)
    return reason


def split_by_reason(df: DataFrame, reason: Column) -> tuple[DataFrame, DataFrame]:
    flagged = df.withColumn(REASON, reason)
    good = flagged.filter(F.col(REASON).isNull()).drop(REASON)
    bad = flagged.filter(F.col(REASON).isNotNull())
    return good, bad


def check_required(df: DataFrame, required: Sequence[str]) -> tuple[DataFrame, DataFrame]:
    """Rows with a null in any required column go to bad (`missing:<column>`)."""
    reason = first_reason(*[(F.col(c).isNull(), f"missing:{c}") for c in required])
    return split_by_reason(df, reason)


def check_null_rate(
    df: DataFrame, columns: Sequence[str], max_null_pct: Decimal | float
) -> tuple[DataFrame, DataFrame, dict[str, float]]:
    """If a column's null rate in this batch is above the limit, its null rows go to bad.

    Below the limit, the nulls are tolerated and the rows stay in good. Also returns the rates
    measured, so the caller can log them.
    """
    rates = null_rates(df, columns)
    limit = float(max_null_pct)
    breached = [c for c in columns if rates[c] > limit]
    reason = first_reason(*[(F.col(c).isNull(), f"null_rate_exceeded:{c}") for c in breached])
    good, bad = split_by_reason(df, reason)
    return good, bad, rates
