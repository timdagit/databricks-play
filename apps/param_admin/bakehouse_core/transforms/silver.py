"""Silver transforms: typing, ingest metadata, deduplication and row checks (spec section 3.3).

Bronze is read with every column as a string, so a value that does not fit its type becomes null
under `try_cast` and is caught by the required-column check instead of failing the job.
"""

from __future__ import annotations

from decimal import Decimal

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F
from pyspark.sql.window import Window

from bakehouse_core.dq import checks

# Column types per entity. Names match the raw files, which keep the sample table's names.
TRANSACTION_TYPES = {
    "transactionID": "bigint",
    "customerID": "bigint",
    "franchiseID": "bigint",
    "dateTime": "timestamp",
    "product": "string",
    "quantity": "bigint",
    "unitPrice": "bigint",
    "totalPrice": "bigint",
    "paymentMethod": "string",
}
REVIEW_TYPES = {
    "review": "string",
    "franchiseID": "bigint",
    "review_date": "timestamp",
    "new_id": "int",
}
CUSTOMER_TYPES = {
    "customerID": "bigint",
    "first_name": "string",
    "last_name": "string",
    "email_address": "string",
    "phone_number": "string",
    "address": "string",
    "city": "string",
    "state": "string",
    "country": "string",
    "continent": "string",
    "postal_zip_code": "bigint",
    "gender": "string",
}
FRANCHISE_TYPES = {
    "franchiseID": "bigint",
    "name": "string",
    "city": "string",
    "district": "string",
    "zipcode": "string",
    "country": "string",
    "size": "string",
    "longitude": "double",
    "latitude": "double",
    "supplierID": "bigint",
}
SUPPLIER_TYPES = {
    "supplierID": "bigint",
    "name": "string",
    "ingredient": "string",
    "continent": "string",
    "city": "string",
    "district": "string",
    "size": "string",
    "longitude": "double",
    "latitude": "double",
    "approved": "string",
}

TRANSACTION_REQUIRED = [
    "transactionID",
    "customerID",
    "franchiseID",
    "dateTime",
    "quantity",
    "unitPrice",
    "totalPrice",
]
REVIEW_REQUIRED = ["review", "franchiseID", "review_date", "new_id"]
TRANSACTION_NULL_CHECKED = ["paymentMethod"]

_PATH_DATE = r"date=(\d{4}-\d{2}-\d{2})"


def with_ingest_metadata(df: DataFrame, file_path: Column) -> DataFrame:
    """Add `_source_file`, `_ingested_at` and `_virtual_date` (taken from the folder name)."""
    return (
        df.withColumn("_source_file", file_path)
        .withColumn("_ingested_at", F.current_timestamp())
        .withColumn(
            "_virtual_date",
            F.to_date(F.regexp_extract(F.col("_source_file"), _PATH_DATE, 1)),
        )
    )


def cast_columns(df: DataFrame, types: dict[str, str]) -> DataFrame:
    """Cast the named columns with `try_cast`. A column missing from the input becomes null."""
    for name, type_name in types.items():
        source = F.col(name) if name in df.columns else F.lit(None).cast("string")
        df = df.withColumn(name, source.try_cast(type_name))
    return df


def deduplicate(df: DataFrame, keys: list[str]) -> DataFrame:
    """Keep one row per key: the one from the latest `_virtual_date`, then the latest file."""
    order = [F.col("_virtual_date").desc(), F.col("_source_file").desc()]
    ranked = df.withColumn("_rn", F.row_number().over(Window.partitionBy(*keys).orderBy(*order)))
    return ranked.filter(F.col("_rn") == 1).drop("_rn")


def build_transactions(
    df: DataFrame, max_null_pct: Decimal | float
) -> tuple[DataFrame, DataFrame, dict[str, float]]:
    """Typed, checked and deduplicated transactions: `(good, quarantined, null_rates)`.

    `df` is raw (all strings) with ingest metadata already added. Rows missing a required
    value are quarantined. Then a column whose null rate exceeds `max_null_pct` has its null
    rows quarantined too. Duplicates are removed from what is left.
    """
    typed = cast_columns(df, TRANSACTION_TYPES)
    present, missing = checks.check_required(typed, TRANSACTION_REQUIRED)
    positive, non_positive = checks.split_by_reason(
        present, checks.first_reason((F.col("quantity") <= 0, "non_positive_quantity"))
    )
    clean, noisy, rates = checks.check_null_rate(positive, TRANSACTION_NULL_CHECKED, max_null_pct)
    good = deduplicate(clean, ["transactionID"])
    bad = missing.unionByName(non_positive).unionByName(noisy)
    return good, bad, rates


def build_reviews(df: DataFrame) -> tuple[DataFrame, DataFrame]:
    """Typed, checked and deduplicated reviews: `(good, quarantined)`."""
    typed = cast_columns(df, REVIEW_TYPES)
    present, missing = checks.check_required(typed, REVIEW_REQUIRED)
    return deduplicate(present, ["new_id"]), missing
