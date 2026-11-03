from datetime import date
from decimal import Decimal

import pytest
from pyspark.sql import functions as F

from bakehouse_core.dq import checks
from bakehouse_core.transforms import silver

pytestmark = pytest.mark.spark

TXN_COLS = [
    "transactionID",
    "customerID",
    "franchiseID",
    "dateTime",
    "product",
    "quantity",
    "unitPrice",
    "totalPrice",
    "paymentMethod",
]


def txn(i, **over):
    row = {
        "transactionID": str(i),
        "customerID": "10",
        "franchiseID": "20",
        "dateTime": "2024-05-01T10:00:00",
        "product": "Golden Gate Ginger",
        "quantity": "2",
        "unitPrice": "3",
        "totalPrice": "6",
        "paymentMethod": "visa",
    }
    row.update(over)
    return tuple(row[c] for c in TXN_COLS)


def raw_txns(spark, rows, file="/v/transactions/date=2024-05-01/transactions.json"):
    df = spark.createDataFrame(rows, TXN_COLS)
    return silver.with_ingest_metadata(df, F.lit(file))


def test_ingest_metadata_takes_virtual_date_from_path(spark):
    df = raw_txns(spark, [txn(1)], "/Volumes/c/bronze/raw/transactions/date=2024-05-07/t.json")
    row = df.first()
    assert row["_virtual_date"] == date(2024, 5, 7)
    assert row["_source_file"].endswith("t.json")
    assert row["_ingested_at"] is not None


def test_cast_makes_bad_values_null_and_missing_columns_null(spark):
    df = spark.createDataFrame([("12", "x")], ["transactionID", "quantity"])
    out = silver.cast_columns(df, {"transactionID": "bigint", "quantity": "bigint", "extra": "int"})
    row = out.first()
    assert row["transactionID"] == 12
    assert row["quantity"] is None
    assert row["extra"] is None


def test_good_rows_are_typed(spark):
    good, bad, _ = silver.build_transactions(raw_txns(spark, [txn(1)]), Decimal("0.02"))
    assert bad.count() == 0
    row = good.first()
    assert row["transactionID"] == 1
    assert row["quantity"] == 2
    assert row["dateTime"].year == 2024


def test_missing_or_unparseable_required_values_are_quarantined(spark):
    rows = [txn(1), txn(2, customerID=None), txn(3, dateTime="not a date"), txn(4, quantity="x")]
    good, bad, _ = silver.build_transactions(raw_txns(spark, rows), Decimal("0.5"))
    assert [r["transactionID"] for r in good.collect()] == [1]
    reasons = {r["transactionID"]: r["_dq_reason"] for r in bad.collect()}
    assert reasons == {2: "missing:customerID", 3: "missing:dateTime", 4: "missing:quantity"}


def test_non_positive_quantity_is_quarantined(spark):
    good, bad, _ = silver.build_transactions(
        raw_txns(spark, [txn(1), txn(2, quantity="0")]), Decimal("0.5")
    )
    assert good.count() == 1
    assert bad.first()["_dq_reason"] == "non_positive_quantity"


def test_duplicates_are_removed_keeping_latest_file(spark):
    old = raw_txns(spark, [txn(1, product="old")], "/v/t/date=2024-05-01/t.json")
    new = raw_txns(spark, [txn(1, product="new")], "/v/t/date=2024-05-02/t.json")
    good, bad, _ = silver.build_transactions(old.unionByName(new), Decimal("0.5"))
    assert bad.count() == 0
    assert [r["product"] for r in good.collect()] == ["new"]


def test_nulls_under_the_limit_are_tolerated(spark):
    rows = [txn(i) for i in range(1, 50)] + [txn(50, paymentMethod=None)]  # 2% null
    good, bad, rates = silver.build_transactions(raw_txns(spark, rows), Decimal("0.05"))
    assert good.count() == 50
    assert bad.count() == 0
    assert rates["paymentMethod"] == pytest.approx(0.02)


def test_nulls_over_the_limit_are_quarantined(spark):
    rows = [txn(i) for i in range(1, 9)] + [txn(9, paymentMethod=None), txn(10, paymentMethod=None)]
    good, bad, rates = silver.build_transactions(raw_txns(spark, rows), Decimal("0.02"))
    assert good.count() == 8
    assert {r["_dq_reason"] for r in bad.collect()} == {"null_rate_exceeded:paymentMethod"}
    assert rates["paymentMethod"] == pytest.approx(0.2)


def test_null_rate_of_empty_frame_is_zero(spark):
    empty = spark.createDataFrame([], "a string")
    assert checks.null_rates(empty, ["a"]) == {"a": 0.0}


def test_reviews_are_typed_checked_and_deduplicated(spark):
    cols = ["review", "franchiseID", "review_date", "new_id"]
    rows = [
        ("great", "1", "2024-05-01T00:00:00", "5"),
        ("great", "1", "2024-05-01T00:00:00", "5"),
        (None, "1", "2024-05-01T00:00:00", "6"),
    ]
    raw = silver.with_ingest_metadata(
        spark.createDataFrame(rows, cols), F.lit("/v/r/date=2024-05-01/r.json")
    )
    good, bad = silver.build_reviews(raw)
    assert good.count() == 1
    assert bad.first()["_dq_reason"] == "missing:review"
