"""Slowly changing dimension type 2 (SCD2) staging for the silver reference tables.

Delta `MERGE` cannot close an old row and insert its replacement from one source row, so the
usual pattern stages two rows for every changed key:

- `_merge_key = key`: matches the current target row, which the MERGE closes.
- `_merge_key = null`: matches nothing, so the MERGE inserts it as the new current version.

A new key only gets the second row. Unchanged keys get nothing. This module builds those staged
rows. The MERGE statement itself is in `bakehouse_core.io.silver_tables`.

Call it with one virtual day of incoming rows at a time and apply the merge before the next
day. Then a backfill of several days gives the same history as running them one by one.
"""

from __future__ import annotations

from collections.abc import Sequence

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

HASH = "_row_hash"
MERGE_KEY = "_merge_key"
VALID_FROM = "_valid_from"
VALID_TO = "_valid_to"
IS_CURRENT = "_is_current"


def row_hash(attributes: Sequence[str]):
    """A stable hash of the tracked columns, treating null as its own value."""
    parts = [F.coalesce(F.col(a).cast("string"), F.lit("\x00null")) for a in attributes]
    return F.sha2(F.concat_ws("\x1f", *parts), 256)


def stage_scd2(
    incoming: DataFrame,
    current: DataFrame,
    key: str,
    attributes: Sequence[str],
    valid_from: str = "_virtual_date",
) -> DataFrame:
    """Staged rows for the MERGE.

    `incoming` is one day of typed, deduplicated rows (one per key) with a `valid_from` date
    column. `current` is the target table. Only its open rows (`_is_current`) are compared.
    The result has the incoming columns plus `_row_hash`, `_valid_from` and `_merge_key`.
    """
    open_rows = current.filter(F.col(IS_CURRENT)).select(
        F.col(key).alias("_cur_key"), F.col(HASH).alias("_cur_hash")
    )
    staged = (
        incoming.withColumn(HASH, row_hash(attributes))
        .withColumn(VALID_FROM, F.col(valid_from))
        .join(open_rows, F.col(key) == F.col("_cur_key"), "left")
    )
    changed = staged.filter(F.col("_cur_hash").isNull() | (F.col("_cur_hash") != F.col(HASH)))
    base = [c for c in changed.columns if c not in ("_cur_key", "_cur_hash")]
    closing = changed.filter(F.col("_cur_key").isNotNull()).select(
        *base, F.col(key).alias(MERGE_KEY)
    )
    inserting = changed.select(
        *base, F.lit(None).cast(changed.schema[key].dataType).alias(MERGE_KEY)
    )
    return closing.unionByName(inserting)
