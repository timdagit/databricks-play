"""Load the `samples.bakehouse` tables into memory for the simulator.

The tables are small (a few thousand rows), so collecting them to the driver is fine. This is
the only place the simulator touches Spark. `spark` is any object with a `table(name)` method
returning something with `collect()` of rows that have `asDict()`.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from bakehouse_core.simulator.generate import Row, SourceData

SAMPLE_SCHEMA = "samples.bakehouse"


def _rows(spark: Any, table: str) -> list[Row]:
    return [r.asDict() for r in spark.table(f"{SAMPLE_SCHEMA}.{table}").collect()]


def load_source(spark: Any) -> SourceData:
    by_day: dict = defaultdict(list)
    for row in _rows(spark, "sales_transactions"):
        by_day[row["dateTime"].date()].append(row)
    return SourceData(
        transactions=dict(by_day),
        customers=_rows(spark, "sales_customers"),
        franchises=_rows(spark, "sales_franchises"),
        suppliers=_rows(spark, "sales_suppliers"),
        reviews=_rows(spark, "media_customer_reviews"),
    )
