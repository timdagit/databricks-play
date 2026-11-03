"""Gold table definitions and writing one day's snapshot (spec section 3.1)."""

from __future__ import annotations

from datetime import date
from typing import Any

from pyspark.sql import functions as F

from bakehouse_core.io.ops_tables import ident
from bakehouse_core.io.silver_tables import create_table_sql

# Column order and types of every gold table. Each holds one snapshot per `as_of_date`.
GOLD_TABLES: dict[str, dict[str, str]] = {
    "franchise_daily_kpis": {
        "as_of_date": "date",
        "franchiseID": "bigint",
        "transactions": "bigint",
        "units": "bigint",
        "revenue": "bigint",
        "unique_customers": "bigint",
        "avg_basket": "double",
        "revenue_at_promo": "double",
    },
    "franchise_flags": {
        "as_of_date": "date",
        "franchiseID": "bigint",
        "earlier_txn_count": "bigint",
        "recent_avg_revenue": "double",
        "earlier_avg_revenue": "double",
        "change_pct": "double",
        "network_change_pct": "double",
        "relative_change_pct": "double",
        "eligible": "boolean",
        "revenue_rank": "int",
        "underperforming": "boolean",
    },
    "customer_loyalty_tiers": {
        "as_of_date": "date",
        "customerID": "bigint",
        "spend": "bigint",
        "transactions": "bigint",
        "tier": "string",
    },
    "supplier_concentration": {
        "as_of_date": "date",
        "supplierID": "bigint",
        "ingredient": "string",
        "continent": "string",
        "supplier_known": "boolean",
        "revenue": "bigint",
        "franchises": "bigint",
        "revenue_share": "double",
        "concentrated": "boolean",
    },
    "review_alerts": {
        "as_of_date": "date",
        "franchiseID": "bigint",
        "reviews": "bigint",
        "negative_reviews": "bigint",
        "alert": "boolean",
    },
    "sales_vs_target": {
        "as_of_date": "date",
        "franchiseID": "bigint",
        "revenue": "bigint",
        "earlier_avg_revenue": "double",
        "target": "double",
        "attainment": "double",
    },
}


def missing_columns(existing: list[str], table: str) -> dict[str, str]:
    """Columns the table definition has but the existing table lacks, with their types.

    Raises if the existing table has a column the definition no longer has, since that needs
    a deliberate rebuild rather than a silent change.
    """
    wanted = GOLD_TABLES[table]
    extra = [c for c in existing if c not in wanted]
    if extra:
        raise ValueError(
            f"gold.{table} has columns not in its definition ({', '.join(extra)}); "
            f"drop the table so it can be recreated, then run recompute_gold"
        )
    return {c: t for c, t in wanted.items() if c not in existing}


def conform(df: Any, table: str) -> Any:
    """Select and cast a DataFrame to the table's columns, in order."""
    return df.select(*[F.col(c).cast(t).alias(c) for c, t in GOLD_TABLES[table].items()])


class GoldTables:
    def __init__(self, spark: Any, catalog: str):
        self._spark = spark
        self._catalog = ident(catalog)

    def name(self, table: str) -> str:
        if table not in GOLD_TABLES:
            raise ValueError(f"unknown gold table: {table}")
        return f"{self._catalog}.gold.{ident(table)}"

    def ensure_tables(self) -> None:
        """Create missing tables and add columns a table lacks. New columns are null on old
        rows until `recompute_gold` rebuilds them."""
        for table, columns in GOLD_TABLES.items():
            full = self.name(table)
            self._spark.sql(create_table_sql(full, columns))
            added = missing_columns(self._spark.table(full).columns, table)
            if added:
                cols = ", ".join(f"{ident(c)} {t.upper()}" for c, t in added.items())
                self._spark.sql(f"ALTER TABLE {full} ADD COLUMNS ({cols})")

    def replace_day(self, table: str, df: Any, as_of: date) -> None:
        """Atomically replace the table's rows for one day with `df`."""
        conform(df, table).writeTo(self.name(table)).overwrite(F.col("as_of_date") == F.lit(as_of))

    def row_counts(self) -> dict[str, int]:
        return {t: self._spark.table(self.name(t)).count() for t in GOLD_TABLES}
