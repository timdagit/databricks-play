from datetime import date
from decimal import Decimal

import pytest
from pyspark.sql import functions as F

from bakehouse_core.io.silver_tables import SPECS
from bakehouse_core.jobs.silver_build import process_batch

pytestmark = pytest.mark.spark

CUSTOMER_COLS = [*SPECS["customers"].types]


class FakeTables:
    """Collects what would be written, and keeps SCD2 state in a DataFrame."""

    def __init__(self, spark):
        self.spark = spark
        self.applied = {}
        self.quarantined = {}
        self.staged_days = []
        self._current = {}

    def current(self, spec):
        if spec.entity not in self._current:
            schema = ", ".join(f"{c} {t}" for c, t in spec.columns.items())
            self._current[spec.entity] = self.spark.createDataFrame([], schema)
        return self._current[spec.entity]

    def apply(self, spec, df):
        self.applied[spec.entity] = df.collect()

    def append_quarantine(self, spec, bad):
        self.quarantined[spec.entity] = bad.collect()

    def apply_scd2(self, spec, staged):
        rows = staged.collect()
        self.staged_days.append(sorted({r["_valid_from"] for r in rows}))
        # Emulate the MERGE well enough to carry state between days.
        cur = [r.asDict() for r in self.current(spec).collect()]
        for r in rows:
            r = r.asDict()
            if r["_merge_key"] is not None:
                for c in cur:
                    if c[spec.key] == r["_merge_key"] and c["_is_current"]:
                        c["_is_current"], c["_valid_to"] = False, r["_valid_from"]
            else:
                new = {k: r.get(k) for k in spec.columns}
                new["_valid_to"], new["_is_current"] = None, True
                cur.append(new)
        schema = ", ".join(f"{c} {t}" for c, t in spec.columns.items())
        self._current[spec.entity] = self.spark.createDataFrame(
            [tuple(c[k] for k in spec.columns) for c in cur], schema
        )


def raw(spark, columns, rows, day):
    df = spark.createDataFrame(rows, columns)
    path = f"/v/x/date={day}/f.json"
    return (
        df.withColumn("_source_file", F.lit(path))
        .withColumn("_ingested_at", F.current_timestamp())
        .withColumn("_virtual_date", F.to_date(F.lit(day)))
    )


def customer(i, city):
    row = dict.fromkeys(CUSTOMER_COLS)
    row.update(customerID=str(i), city=city, first_name="A")
    return tuple(row[c] for c in CUSTOMER_COLS)


def test_transactions_are_applied_and_bad_rows_quarantined(spark):
    cols = [*SPECS["transactions"].types]
    ok = dict(
        zip(cols, ["1", "2", "3", "2024-05-01T10:00:00", "p", "1", "5", "5", "visa"], strict=True)
    )
    bad = {**ok, "transactionID": "2", "customerID": None}
    df = raw(spark, cols, [tuple(ok.values()), tuple(bad.values())], "2024-05-01")
    tables = FakeTables(spark)
    stats = process_batch(tables, SPECS["transactions"], df, Decimal("0.5"))
    assert [r["transactionID"] for r in tables.applied["transactions"]] == [1]
    assert stats["quarantined"] == 1
    assert stats["reasons"] == {"missing:customerID": 1}
    assert stats["rows_in"] == 2


def test_missing_rescued_data_column_is_tolerated(spark):
    cols = [*SPECS["reviews"].types]
    df = raw(spark, cols, [("nice", "1", "2024-05-01T00:00:00", "9")], "2024-05-01")
    assert "_rescued_data" not in df.columns
    stats = process_batch(FakeTables(spark), SPECS["reviews"], df, Decimal("0.5"))
    assert stats["applied"] == 1


def test_suppliers_with_a_null_key_are_dropped_and_duplicates_collapsed(spark):
    cols = [*SPECS["suppliers"].types]

    def row(i, name):
        d = dict.fromkeys(cols)
        d.update(supplierID=i, name=name)
        return tuple(d[c] for c in cols)

    df = raw(spark, cols, [row("1", "a"), row("1", "b"), row(None, "c")], "2024-05-01")
    tables = FakeTables(spark)
    stats = process_batch(tables, SPECS["suppliers"], df, Decimal("0.5"))
    assert stats["applied"] == 1
    assert len(tables.applied["suppliers"]) == 1


def test_scd2_batch_with_several_days_is_applied_oldest_first(spark):
    spec = SPECS["customers"]
    day1 = raw(spark, CUSTOMER_COLS, [customer(1, "Leeds")], "2024-05-01")
    day2 = raw(spark, CUSTOMER_COLS, [customer(1, "York")], "2024-05-02")
    day3 = raw(spark, CUSTOMER_COLS, [customer(1, "York"), customer(2, "Hull")], "2024-05-03")
    tables = FakeTables(spark)
    stats = process_batch(tables, spec, day3.unionByName(day1).unionByName(day2), Decimal("0.5"))
    assert tables.staged_days == [[date(2024, 5, 1)], [date(2024, 5, 2)], [date(2024, 5, 3)]]
    assert stats["days"] == ["2024-05-01", "2024-05-02", "2024-05-03"]
    rows = [r.asDict() for r in tables.current(spec).collect()]
    leeds = [r for r in rows if r["customerID"] == 1 and r["city"] == "Leeds"]
    york = [r for r in rows if r["customerID"] == 1 and r["city"] == "York"]
    assert len(leeds) == 1 and leeds[0]["_valid_to"] == date(2024, 5, 2)
    assert not leeds[0]["_is_current"]
    assert len(york) == 1 and york[0]["_is_current"]  # day 3 repeats York: no new version
    assert any(r["customerID"] == 2 and r["_is_current"] for r in rows)
