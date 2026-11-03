from datetime import UTC, datetime, timedelta

import pytest

from bakehouse_core.io.gold_tables import GOLD_TABLES, GoldTables, missing_columns
from bakehouse_core.jobs.gold_build import build_day, days_to_build
from bakehouse_core.jobs.store import InMemoryOpsStore, RunLogEntry
from bakehouse_core.params import model
from bakehouse_core.simulator.clock import START_DATE

D = START_DATE


def test_incremental_builds_days_after_the_last_success():
    assert days_to_build("incremental", D + timedelta(days=3), D + timedelta(days=1)) == [
        D + timedelta(days=2),
        D + timedelta(days=3),
    ]


def test_incremental_with_no_history_builds_from_the_start():
    assert days_to_build("incremental", D + timedelta(days=2), None) == [
        D,
        D + timedelta(days=1),
        D + timedelta(days=2),
    ]


def test_incremental_with_nothing_new_builds_nothing():
    assert days_to_build("incremental", D, D) == []


def test_recompute_rebuilds_everything_from_the_start():
    assert days_to_build("recompute_gold", D + timedelta(days=1), D + timedelta(days=1)) == [
        D,
        D + timedelta(days=1),
    ]


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError, match="mode must be one of"):
        days_to_build("full", D, None)


def test_last_success_date_ignores_failures_and_other_jobs():
    store = InMemoryOpsStore()
    now = datetime.now(UTC)

    def log(job, status, day):
        store.log_run(RunLogEntry("r", job, status, now, now, day, "t", "{}"))

    log("gold_build", "succeeded", D)
    log("gold_build", "failed", D + timedelta(days=5))
    log("silver_build", "succeeded", D + timedelta(days=9))
    assert store.last_success_date("gold_build") == D
    assert store.last_success_date("never") is None


def test_name_rejects_unknown_tables():
    with pytest.raises(ValueError, match="unknown gold table"):
        GoldTables(object(), "cat").name("nope")


def test_missing_columns_lists_what_a_table_lacks():
    wanted = list(GOLD_TABLES["review_alerts"])
    assert missing_columns(wanted, "review_alerts") == {}
    assert missing_columns(wanted[:-1], "review_alerts") == {
        wanted[-1]: GOLD_TABLES["review_alerts"][wanted[-1]]
    }


def test_missing_columns_refuses_to_drop_columns():
    with pytest.raises(ValueError, match="drop the table"):
        missing_columns([*GOLD_TABLES["review_alerts"], "old_column"], "review_alerts")


def test_every_table_starts_with_as_of_date():
    for columns in GOLD_TABLES.values():
        assert next(iter(columns)) == "as_of_date"


@pytest.mark.spark
def test_build_day_returns_every_table_with_the_declared_columns(spark):
    transactions = spark.createDataFrame(
        [(1, 1, 10, 1, 100, D)],
        "transactionID long, franchiseID long, customerID long, quantity long, "
        "totalPrice long, _virtual_date date",
    )
    reviews = spark.createDataFrame(
        [("terrible", 1, D)], "review string, franchiseID long, _virtual_date date"
    )
    franchises = spark.createDataFrame(
        [(1, 7, D, None)],
        "franchiseID long, supplierID long, _valid_from date, _valid_to date",
    )
    suppliers = spark.createDataFrame(
        [(7, "cacao", "Asia")], "supplierID long, ingredient string, continent string"
    )
    out = build_day(transactions, reviews, franchises, suppliers, D, model.defaults())
    assert set(out) == set(GOLD_TABLES)
    for table, df in out.items():
        assert set(df.columns) == set(GOLD_TABLES[table]), table
        assert df.count() >= 1, table
