"""Silver table definitions and the SQL that fills them (spec section 3.1 and 3.3).

The SQL is built by plain functions so it can be tested as text. `SilverTables` runs it with
Spark on Databricks. Identifiers come from fixed specs and are checked with `ident`; row values
never go into SQL text, they travel in DataFrames registered as temporary views.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from bakehouse_core.io.ops_tables import ident
from bakehouse_core.transforms import silver

Kind = Literal["insert_new", "upsert", "scd2"]

META_TYPES = {
    "_rescued_data": "string",
    "_source_file": "string",
    "_ingested_at": "timestamp",
    "_virtual_date": "date",
}
SCD2_TYPES = {
    "_row_hash": "string",
    "_valid_from": "date",
    "_valid_to": "date",
    "_is_current": "boolean",
}


@dataclass(frozen=True)
class TableSpec:
    entity: str  # the folder name in the raw volume
    table: str
    key: str
    kind: Kind
    types: dict[str, str]  # business columns
    quarantine: str | None = None

    @property
    def columns(self) -> dict[str, str]:
        extra = SCD2_TYPES if self.kind == "scd2" else {}
        return {**self.types, **META_TYPES, **extra}

    @property
    def attributes(self) -> list[str]:
        """The business columns an SCD2 table tracks for changes (everything but the key)."""
        return [c for c in self.types if c != self.key]


SPECS = {
    s.entity: s
    for s in (
        TableSpec(
            "transactions",
            "transactions",
            "transactionID",
            "insert_new",
            silver.TRANSACTION_TYPES,
            quarantine="transactions_quarantine",
        ),
        TableSpec(
            "reviews",
            "reviews",
            "new_id",
            "insert_new",
            silver.REVIEW_TYPES,
            quarantine="reviews_quarantine",
        ),
        TableSpec("suppliers", "suppliers", "supplierID", "upsert", silver.SUPPLIER_TYPES),
        TableSpec("customers", "customers", "customerID", "scd2", silver.CUSTOMER_TYPES),
        TableSpec("franchises", "franchises", "franchiseID", "scd2", silver.FRANCHISE_TYPES),
    )
}


def create_table_sql(full_name: str, columns: dict[str, str]) -> str:
    cols = ", ".join(f"{ident(c)} {t.upper()}" for c, t in columns.items())
    return f"CREATE TABLE IF NOT EXISTS {full_name} ({cols})"


def create_quarantine_sql(full_name: str, spec: TableSpec) -> str:
    return create_table_sql(full_name, {**spec.columns, "_dq_reason": "string"})


def insert_new_sql(target: str, view: str, key: str, columns: list[str]) -> str:
    """Insert rows whose key is not in the target yet. Existing rows are left alone."""
    names = ", ".join(ident(c) for c in columns)
    values = ", ".join(f"s.{ident(c)}" for c in columns)
    return (
        f"MERGE INTO {target} t USING {view} s ON t.{ident(key)} = s.{ident(key)} "
        f"WHEN NOT MATCHED THEN INSERT ({names}) VALUES ({values})"
    )


def upsert_sql(target: str, view: str, key: str, columns: list[str]) -> str:
    """Update rows with the same key and insert the rest."""
    names = ", ".join(ident(c) for c in columns)
    values = ", ".join(f"s.{ident(c)}" for c in columns)
    sets = ", ".join(f"t.{ident(c)} = s.{ident(c)}" for c in columns if c != key)
    return (
        f"MERGE INTO {target} t USING {view} s ON t.{ident(key)} = s.{ident(key)} "
        f"WHEN MATCHED THEN UPDATE SET {sets} "
        f"WHEN NOT MATCHED THEN INSERT ({names}) VALUES ({values})"
    )


def scd2_merge_sql(target: str, view: str, key: str, columns: list[str]) -> str:
    """Apply rows staged by `transforms.scd2.stage_scd2`.

    The staged row with `_merge_key = key` closes the open version. The staged row with a null
    `_merge_key` matches nothing and is inserted as the new open version.
    """
    insert_cols = ", ".join(ident(c) for c in columns)
    values = ", ".join(
        {"_valid_to": "CAST(NULL AS DATE)", "_is_current": "TRUE"}.get(c, f"s.{ident(c)}")
        for c in columns
    )
    return (
        f"MERGE INTO {target} t USING {view} s "
        f"ON t.{ident(key)} = s._merge_key AND t._is_current "
        "WHEN MATCHED THEN UPDATE SET t._is_current = FALSE, t._valid_to = s._valid_from "
        f"WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({values})"
    )


class SilverTables:
    """Creates the silver tables and applies batches to them."""

    def __init__(self, spark: Any, catalog: str):
        self._spark = spark
        self._catalog = ident(catalog)

    def name(self, table: str) -> str:
        return f"{self._catalog}.silver.{ident(table)}"

    def ensure_tables(self) -> None:
        for spec in SPECS.values():
            self._spark.sql(create_table_sql(self.name(spec.table), spec.columns))
            if spec.quarantine:
                self._spark.sql(create_quarantine_sql(self.name(spec.quarantine), spec))

    def apply(self, spec: TableSpec, df: Any) -> None:
        """Merge a DataFrame (already shaped like the table) into the spec's table."""
        builder = {"insert_new": insert_new_sql, "upsert": upsert_sql}.get(spec.kind)
        if builder is None:
            raise ValueError("SCD2 batches go through apply_scd2, not apply")
        columns = list(spec.columns)
        view = f"_batch_{spec.entity}"
        df.select(*columns).createOrReplaceTempView(view)
        self._spark.sql(builder(self.name(spec.table), view, spec.key, columns))

    def apply_scd2(self, spec: TableSpec, staged: Any) -> None:
        """Merge staged rows from `stage_scd2` into an SCD2 table."""
        columns = list(spec.columns)
        view = f"_staged_{spec.entity}"
        staged.createOrReplaceTempView(view)
        self._spark.sql(scd2_merge_sql(self.name(spec.table), view, spec.key, columns))

    def append_quarantine(self, spec: TableSpec, bad: Any) -> None:
        if spec.quarantine is None:
            raise ValueError(f"{spec.entity} has no quarantine table")
        columns = [*spec.columns, "_dq_reason"]
        bad.select(*columns).writeTo(self.name(spec.quarantine)).append()
